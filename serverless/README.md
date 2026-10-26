# Serverless deployment (no EC2, no Temporal)

Replaces the EC2 + Temporal deployment. Nothing runs around the clock, so there is
no hourly compute charge.

**Live URL: https://dad3m4gxlizq4.cloudfront.net**

## Architecture

```
Browser
  │
  ▼
CloudFront  EPGC4W454TWH0  (dad3m4gxlizq4.cloudfront.net, free *.cloudfront.net TLS)
  ├── /*         ──► S3 spf-site-414691912352      (UI, private, read via OAC)
  └── /api/*     ──► API Gateway HTTP API xcz68za6vg
      /health         └─► Lambda spf-api
                            ├── reads/writes job state in S3 spf-jobs-414691912352
                            └── StartExecution ──► Step Functions
                                                     spf-insights   (analysis pipeline)
                                                     spf-deepdive   (episode deep dive)
                                                       └─► Lambda spf-activities
                                                             ├── Bedrock (Llama 3.3 70B)
                                                             ├── YouTube Data API v3
                                                             └── job state in S3
```

### What runs where

| Concern | Where it lives |
|---|---|
| Orchestration, retries, parallel stages | Step Functions (`spf-insights`, `spf-deepdive`) |
| Search, transcripts, ranking | Lambda `spf-activities` + `serverless/core/` |
| HTTP API | Lambda `spf-api` (`api_handler.py`) behind API Gateway |
| Agent loop and tool calls | Lambda `spf-agent` (`agent_handler.py` + `serverless/agent/`) |
| Static page | S3 + CloudFront |
| Job state and saved transcripts | S3 (`spf-jobs-…`), polled via `/api/status` |

Job state goes to S3 rather than being passed between states because 50 episodes
plus transcripts exceed both the Step Functions 256 KB payload limit and the
DynamoDB 400 KB item limit.

## Resources (us-east-1, account 414691912352)

| Type | Name / ID |
|---|---|
| CloudFront | `EPGC4W454TWH0` — `dad3m4gxlizq4.cloudfront.net` |
| S3 (site) | `spf-site-414691912352` |
| S3 (job state) | `spf-jobs-414691912352` (7-day lifecycle on `jobs/`) |
| API Gateway | HTTP API `xcz68za6vg` |
| Lambda | `spf-api` (256 MB, 30 s), `spf-activities` (512 MB, 300 s) |
| Step Functions | `spf-insights`, `spf-deepdive` (both STANDARD) |
| IAM roles | `spf-api-role`, `spf-activities-role`, `spf-sfn-role` |
| OAC | `spf-s3-oac` (`E1PHFKPH3AF2O0`) |
| SSM parameter | `/smart-podcast-finder/youtube_api_key` (SecureString, shared with the old stack) |

No secrets live in the code or in Lambda environment variables. `spf-activities`
resolves the YouTube key from Parameter Store once per cold start, and Bedrock is
reached through the execution role.

## Layout

```
serverless/
├── core/                  # temporalio-free logic, shared by both Lambdas
│   ├── config.py          # env vars + lazy SSM key resolution
│   ├── llm.py             # prompts (verbatim from the Temporal version) + Bedrock
│   ├── youtube.py         # YouTube Data API search
│   ├── spotify.py         # Spotify show/episode search
│   ├── transcripts.py     # transcript fetching
│   ├── analyze.py         # extract / rank / summarise / deep dive
│   └── jobs.py            # S3-backed job state
├── activities_handler.py  # Lambda invoked by Step Functions (dispatch on "action")
├── api_handler.py         # Lambda behind API Gateway (7 endpoints)
├── statemachine.insights.asl.json
├── statemachine.deepdive.asl.json
├── requirements.txt       # activities Lambda deps only
├── build.ps1              # builds build/api.zip and build/activities.zip
├── build-site.py          # generates site/index.html from static/index.html
└── infra/                 # IAM policies, env files, CloudFront config
```

## Deploying changes

```powershell
cd serverless

# Python changes
.\build.ps1
aws lambda update-function-code --function-name spf-api          --zip-file fileb://build/api.zip        --region us-east-1
aws lambda update-function-code --function-name spf-activities   --zip-file fileb://build/activities.zip --region us-east-1

# UI changes
python build-site.py
aws s3 cp site/index.html s3://spf-site-414691912352/index.html --content-type "text/html; charset=utf-8" --cache-control "public, max-age=60"
aws cloudfront create-invalidation --distribution-id EPGC4W454TWH0 --paths "/index.html" "/"
```

Rotate the YouTube key (shared with the old stack, no redeploy needed):

```powershell
aws ssm put-parameter --name /smart-podcast-finder/youtube_api_key --type SecureString --value "AIza..." --overwrite --region us-east-1
```

## Debugging

```powershell
aws logs tail /aws/lambda/spf-activities --follow --region us-east-1
aws logs tail /aws/lambda/spf-api --follow --region us-east-1

aws stepfunctions list-executions --state-machine-arn arn:aws:states:us-east-1:414691912352:stateMachine:spf-insights --region us-east-1 --max-items 5
```

Step Functions gives a visual execution graph per run in the console, which is the
closest equivalent to the old Temporal dashboard.

## Design notes

**Job state in S3, not DynamoDB.** With `max_videos` up to 50, video metadata plus
transcript excerpts can exceed both the Step Functions 256 KB state payload limit
and the DynamoDB 400 KB item limit. Only `job_id` flows between states; everything
else is read from and written to S3.

**Parallel branches never write `state.json`.** Both `Parallel` stages would
otherwise race on read-modify-write and lose an update. Each branch writes only its
own part key, and the phase is advanced by the sequential steps either side
(`search` and `PrepareRanking`).

**`s3:ListBucket` is required, not optional.** Without it, `GetObject` on a missing
key returns `AccessDenied` rather than `NoSuchKey`, and the job store treats a
missing object as "not created yet". Omitting it caused a 500 on `/api/analyze`.

**Failures return non-2xx.** The UI does `if (!res.ok) throw` while polling, so a
failed job must surface as an HTTP error. Returning 200 with `phase: "failed"` would
leave the UI spinning until `MAX_POLLS`.

**Pure-Python dependencies only.** Dropping pydantic and httpx in favour of plain
dicts and `requests` means the package can be built on Windows with
`--platform manylinux2014_x86_64` and no cross-compilation.

**API Gateway rather than a Lambda function URL.** Two reasons. A public function
URL (`AuthType: NONE`) returns 403 in this account. And a function URL behind
CloudFront OAC requires callers to send `x-amz-content-sha256` for POST bodies —
Lambda rejects unsigned payloads — which a plain browser `fetch()` cannot do.

## Known limitations

- **No authentication.** Anyone with the URL can run analyses and consume your
  Bedrock and YouTube quota. Unlike the fixed-cost EC2 setup, serverless cost scales
  with usage, so a budget alert is worth adding.
- **Account Lambda concurrency limit is 10**, not the default 1000. Each analysis
  uses two concurrent branches, so a handful of simultaneous users is fine but heavy
  concurrent traffic will throttle.
- **Transcripts return nothing.** YouTube blocks most AWS IP ranges, so
  `transcript_count` is 0 and ranking falls back to metadata. Identical to the EC2
  behaviour; verified again here.
- **Spotify is not configured.** `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` are
  unset, so the Spotify tab fails with a clear error. Add them as Parameter Store
  entries and wire them into `infra/env-activities.json` to enable it.
- **Gemini is not bundled.** `google-genai` is excluded to keep the package small.
  The code path exists and raises a clear error; add the dependency to
  `requirements.txt` to re-enable it.
- **API Gateway is reachable directly**, bypassing CloudFront. Harmless for a public
  app, but it means the CloudFront hostname is not the only entry point.
