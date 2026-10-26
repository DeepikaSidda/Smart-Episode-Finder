# Weekend Deployment Challenge: Smart Episode Finder

#deployment

---

I built an AI app that reads through a podcast channel and tells you which episodes are
actually worth your time, then deployed it on AWS with no servers to manage.
It runs on nine AWS services, answers in about six seconds, and costs under a dollar a
month to keep online.

---

## What Your App Does

Podcast episodes are getting longer. A single channel can hold hundreds of two-hour
conversations, and the titles rarely tell you whether an episode actually covers the
thing you care about. Searching YouTube gives you popular episodes, not *relevant*
ones.

**Smart Episode Finder** fixes that. You give it two things: a podcast channel name
and a plain-English description of your interests. It returns every long-form episode
from that channel scored 0–100 for how well it matches what you actually want to
learn, sorted best-first, with a one-line explanation of each score.

From a user's point of view it's two text boxes and a slider. Type "Apna College",
type "DSA, web development, placements, coding interviews", pick how many episodes to
scan, and press Analyze. A progress bar walks through three stages — Search, Parse &
Transcripts, Rank & Summarize — and then results appear as cards with thumbnails and
a coloured relevance bar.

Here's real output from the deployed app:

```
Channel: Apna College          Tone: educational

 90 | Must Do "DSA Problems" for Tech Placements & Internships
 88 | How to prepare for Tech Placements & Internships? | CGPA, Development, DSA
 85 | How to get Tech Placement in 6 Months? Study plan for SDE Placements
 78 | How to Start Coding & Get a Job (in 2026)?
```

You also get an AI-generated channel summary, three to five key insights about
recurring themes, and a content-tone classification. Any episode can be expanded
into an **Episode Deep Dive** — a detailed breakdown with a TL;DR, key takeaways,
notable quotes, tools mentioned, and who the episode is for.

Only episodes longer than ten minutes are considered, because Shorts and clips aren't
podcasts.

## How You Built It

The app is a multi-step pipeline, not a single request-response call. It has to search
a channel, parse the user's interests, attempt transcript fetching, then rank and
summarise — with some of those steps running in parallel and any of them able to fail
on a rate limit or timeout. So the real design question was where the orchestration
lives, and that decision drove everything else.

I chose fully managed services with nothing running when the app is idle. Three
reasons made that the right call:

- **This app is idle most of the time.** A small always-on instance costs about
  $20/month — roughly $15 for compute, $1.60 for a volume, and $3.65 for a public
  IPv4 address — whether anyone uses it or not. Lambda, Step Functions and CloudFront
  all have perpetual free tiers that cover this workload, so idle costs nothing.
- **HTTPS for free.** CloudFront issues a certificate on its own domain with no
  configuration, so visitors never see a "Not secure" warning.
- **A permanent hostname and no single host to fail.** The CloudFront domain doesn't
  change, and there's no individual machine whose failure takes the app down.

So orchestration became **AWS Step Functions**, the pipeline steps became **Lambda**
functions, and the frontend is served from **S3 behind CloudFront**.

The application logic — the prompts, the scoring rules, the YouTube integration —
stayed exactly as written; only the deployment layer is AWS-native. I confirmed that
by running the same query repeatedly and checking the scores were stable and
explainable (90, 88, 85, 78 for the Apna College example above). A full analysis
completes in about six seconds.

### Challenges, and how I got past them

**A public Lambda Function URL returned 403.** My first plan was CloudFront in front
of a Lambda Function URL. With `AuthType: NONE` every request was Forbidden, despite
a correct resource policy. Rather than guess, I isolated the variable: I invoked the
function directly with a synthetic event and got a clean `200`, which proved the code
was fine and the problem was the front door.

**Then OAC turned out to be incompatible with browser POSTs.** I switched the
function URL to `AWS_IAM` and added CloudFront Origin Access Control. The docs then
revealed a hard blocker: a function URL behind OAC requires the *caller* to send an
`x-amz-content-sha256` header containing a SHA-256 hash of the request body, because
Lambda rejects unsigned payloads. My UI POSTs JSON with a plain `fetch()` and cannot
do that. I moved the API to an **API Gateway HTTP API**, which accepts POST natively
and emits the same payload format — zero code changes.

**A permissions bug that looks like a bug in your code.** My job store treats "object
does not exist in S3" as "this job hasn't started yet". That broke with
`AccessDenied`. The cause: `s3:GetObject` on a missing key returns `AccessDenied`
instead of `NoSuchKey` **unless the caller also holds `s3:ListBucket`**. Adding
`s3:ListBucket` on the bucket fixed it immediately.

**State payloads that don't fit.** With up to 50 episodes, video metadata plus
transcript excerpts can blow past the Step Functions 256 KB state limit *and* the
DynamoDB 400 KB item limit. I made the state machine pass only a `job_id` and keep
the working set in S3.

**A silent race in the parallel stages.** Two branches run concurrently. Both
originally did read-modify-write on the same status object, so one would clobber the
other. The fix was a rule: **parallel branches only ever write their own key**, and
the progress phase is advanced by the sequential steps on either side.

**Building Linux packages on Windows.** I develop on Windows but Lambda runs Linux. I
dropped heavier dependencies in favour of plain dictionaries and `requests`, then
installed with `--platform manylinux2014_x86_64 --python-version 3.12
--only-binary=:all:`. I verified the resulting zip contained
`cpython-312-x86_64-linux-gnu.so` binaries and no Windows `.pyd` files.

## AWS Services Used / Architecture Overview

This application is deployed on AWS using the following **nine AWS services**:

1. **Amazon CloudFront** — the public HTTPS entry point for the app. Serves the free
   TLS certificate, caches the frontend at the edge, and routes `/api/*` requests to
   the backend.

2. **Amazon S3** — used for two separate purposes: hosting the static frontend in a
   private bucket readable only by CloudFront through Origin Access Control, and
   storing the job state for each in-flight analysis.

3. **Amazon API Gateway** — an HTTP API that fronts the backend and exposes the seven
   REST endpoints the frontend calls.

4. **AWS Lambda** — runs all application code on Python 3.12. Two functions: one
   handles HTTP requests, the other executes each step of the analysis pipeline.

5. **AWS Step Functions** — orchestrates the analysis pipeline as a state machine,
   providing durable execution, per-task retries with exponential backoff, and the two
   parallel stages.

6. **Amazon Bedrock** — supplies the AI. Meta's Llama 3.3 70B does the interest
   parsing, episode scoring, channel summarisation, and episode deep dives.

7. **AWS Identity and Access Management (IAM)** — three separate least-privilege
   execution roles, one each for the API function, the pipeline function, and the
   state machine.

8. **AWS Systems Manager Parameter Store** — stores the YouTube Data API key as an
   encrypted SecureString, so no credential is ever committed to the repository or
   placed in an environment variable.

9. **Amazon CloudWatch Logs** — collects logs from both Lambda functions, which is how
   I debugged every issue described above.

```
                        Browser
                           │  HTTPS
                           ▼
        ┌──────────── CloudFront ────────────┐
        │                                    │
      /*│                          /api/*    │/health
        ▼                                    ▼
   S3 (static UI)                   API Gateway HTTP API
   private, via OAC                          │
                                             ▼
                                    Lambda  spf-api
                                             │
                        ┌────────────────────┴───────────────┐
                        ▼                                    ▼
                 S3 (job state)                      Step Functions
                                                             │
                                                             ▼
                                                 Lambda  spf-activities
                                                      │         │
                                            Amazon Bedrock   YouTube Data API
```

The pipeline runs as a state machine with two parallel stages:

```
Search ─► HasVideos? ─► ┌ Extract Interests ┐ ─► Prepare ─► ┌ Rank Episodes  ┐ ─► Finalize
                        └ Fetch Transcripts ┘               └ Summarize      ┘
```

Every task carries its own retry policy with exponential backoff, so a transient API
timeout or rate limit is retried automatically instead of failing the run.

No credentials live in the code. Bedrock is reached through the Lambda execution
role, and the YouTube API key is resolved from Parameter Store once per cold start —
which means rotating it needs no redeploy.

**Total infrastructure cost is roughly $0–1/month**, plus Bedrock tokens per analysis.
Lambda's 1M requests and 400,000 GB-seconds, Step Functions' 4,000 state transitions,
and CloudFront's 1 TB of transfer are all perpetual free tiers, and this workload sits
comfortably inside every one of them.

## What You Learned

**"I need a durable workflow engine" doesn't mean "I need a server running one".**
This was the most valuable realisation of the whole build. Step Functions gave me
per-task retries, parallel execution, and a visual execution history for every run —
all the durability guarantees I wanted — with nothing to patch, monitor, or pay for
while idle.

**Read the constraints before designing around a service.** I lost time assuming a
Lambda Function URL behind CloudFront was a drop-in HTTPS front door. The payload
signing requirement is documented and would have redirected me to API Gateway
immediately. Checking limits early beats debugging late.

**Isolate the variable before you theorise.** When the 403 appeared, invoking the
function directly with a synthetic event proved the code worked and narrowed the
problem to authorisation in one step. That habit turned a vague failure into a
specific one.

**IAM errors are not always about permission to do the thing you asked.**
`s3:ListBucket` changing a 403 into a 404 was genuinely surprising, and it only
mattered because my design used object absence as meaningful state.

**Serverless changes the shape of your cost risk.** A fixed-size server has a spending
ceiling — overload it and it simply gets slow. Pay-per-use scales with traffic
instead, so a public endpoint with no authentication is also a spending risk. That
makes budget alerts more important on serverless, not less.

**Design for graceful degradation.** YouTube blocks most cloud provider IP ranges, so
transcript fetching returns nothing when it runs from AWS. Because that step was
written as best-effort from the start, the pipeline falls back to metadata-only
ranking and still produces good results instead of failing outright. Deciding up front
which parts of a pipeline are *allowed* to fail turned an external blocker into a
non-event.

## Link to App or Repo

**Live application:** https://dad3m4gxlizq4.cloudfront.net

**Source code:** https://github.com/DeepikaSidda/Smart-Podcast-Finder

Try it with a channel like *Apna College*, *Lex Fridman*, or *Fireship*, and describe
your interests in your own words. The first request after an idle period takes an
extra second or two while Lambda warms up.
