# I gave my podcast agent ears

**One question. Same agent. One new tool.**

> *On the Syntax podcast, in the episode called 'Deno Is Dead', which specific AI
> models did the hosts actually talk about? Quote them.*

| | Before | After |
|---|---|---|
| Tool the agent chose | `search_podcast_episodes` | `listen_to_episode` |
| Answer | *"it does not contain or reveal the actual spoken content... I cannot tell you which specific AI models the hosts talked about"* | **Gemini 4 Argon** (0:00), **Muse Wonder** (0:19), **Claude Haiku 5.5** (0:19) — each with a verbatim quote |
| Source of truth | the episode title | the actual audio |

The new tool is **Amazon Transcribe**. The rest of this post is how it got wired in,
and the three things I had to fix before the agent used it reliably.

You can reproduce the contrast yourself — the deployed agent takes a `with_ears`
flag that withholds the tool, so both rows above come from identical code.

---

## The agent I started with

**Smart Episode Finder** recommends podcast episodes. You name a channel, describe
what you want to learn, and it scores every long-form episode for relevance using
Meta's Llama 3.3 70B on Amazon Bedrock. It is deployed and public, running on Lambda,
Step Functions, S3 and CloudFront.

It had one tool: `search_podcast_episodes`, a YouTube Data API search returning
titles, descriptions, tags, chapters and view counts.

And it had one embarrassing hole. **It could read, but it could not listen.**

Every judgement it made came from metadata. It could tell you an episode was *called*
"Deno Is Dead". It could not tell you a single thing that was said in it. There was a
transcript step, but YouTube blocks requests from AWS IP ranges, so in production
`transcript_count` was permanently `0`. The agent was recommending hours of audio
based on titles.

## The one tool I added, and why

**`listen_to_episode`** — powered by **Amazon Transcribe**.

I picked it because it closes the gap that actually mattered. Every other
improvement I considered — better prompts, more metadata, richer scoring — was
polish on top of guessing. The agent's core problem was that it had never heard a
single second of the content it was recommending.

The interesting part is *how* it gets the audio. YouTube blocks AWS, and that is not
negotiable. So instead of fighting the block, the tool goes around it. Podcasts are
published as plain MP3 enclosures in RSS feeds, served by podcast CDNs that do not
block datacenter IPs:

```
show name
   |  iTunes Search API          (free, no API key)
   v
RSS feed  ->  <enclosure> MP3 URL
   |  range-GET the first N minutes only
   v
Amazon S3  ->  Amazon Transcribe  ->  timestamped transcript
```

Two engineering decisions worth stealing:

**Partial fetch.** Episodes run to 250 MB. You do not need all of one to know what it
covers, so the tool range-GETs only the opening minutes. A 3-minute slice is ~2.9 MB,
transcribes in seconds instead of twenty minutes, and caps the cost per call. MP3 is
frame-based, so a truncated file is still perfectly decodable.

**The tool reports its own uncertainty.** `find_feed_url` returns which show it
actually resolved, and `pick_episode` returns `hint_matched: false` when the requested
title is not in the feed rather than silently transcribing the latest episode. That
matters — my first version took the first iTunes result for "Syntax" and confidently
transcribed a *horror audio drama* of the same name. Now the tool ranks candidates by
name overlap and episode count, and hands the agent enough information to notice a
mismatch and retry.

## Before and after

Same agent. Same question. The only difference is whether the Transcribe tool is
registered. The metadata-only agent is never even told the tool exists, so this is a
real capability gap, not an agent declining to use something it can see.

> **Q: On the Syntax podcast, in the episode called 'Deno Is Dead', which specific AI
> models did the hosts actually talk about? Quote them.**

**BEFORE** — `tools used: search_podcast_episodes`

> "The tool I attempted to use only returns metadata about podcast episodes (like
> titles, descriptions, and tags), and **it does not contain or reveal the actual
> spoken content** of any episode. Because of this limitation, **I cannot tell you
> which specific AI models the hosts talked about or provide any quotes**."

**AFTER** — `tools used: listen_to_episode`

> In the episode "Deno Is Dead" of the Syntax podcast, the hosts specifically
> mentioned the following AI models:
>
> 1. **Gemini 4 Argon** — at 0:00: *"In fact, just, uh, 2 hours ago or 3 hours ago, I
>    don't know what, uh, Gemini 4 Argon was just released..."*
> 2. **Muse Wonder** — at 0:19: *"...we have thoughts here as well as Muse Wonder..."*
> 3. **Claude Haiku 5.5** — at 0:19: *"...Claude Haiku 5.5..."*

One tool call. Three models named, each with a verbatim quote and a timestamp.

My favourite detail is a line the agent added in one run: it also flagged *"a new
decision model that absolutely smashes Jeff"* and noted that the model's name was
never stated. That is the agent reasoning over a real transcript — distinguishing
what was said from what was identifiable. No amount of title-reading produces that.

## How the tool plugs into the agent

Built with the **Strands Agents SDK** on **Amazon Bedrock**. Here is the entire
wiring — a complete, runnable file. If you want to bolt an AWS service onto a Strands
agent, this is the whole pattern:

```python
# pip install strands-agents
import boto3, time, uuid, requests
from strands import Agent, tool
from strands.models import BedrockModel

BUCKET = "your-bucket"


@tool
def listen_to_episode(show_name: str, episode_hint: str = "", minutes: int = 4) -> dict:
    """Listen to a podcast episode and return a transcript of what was ACTUALLY SAID.

    Call this for ANY question about episode content: what was discussed, claimed or
    mentioned, who appeared, or to quote the hosts.

    This reads the podcast's public RSS feed and transcribes the real audio with
    Amazon Transcribe. It is INDEPENDENT of search_podcast_episodes and reaches a
    different catalogue, so call it directly — it works even when an episode does not
    appear in search results.

    Args:
        show_name: The podcast's name, e.g. "Syntax".
        episode_hint: Words from the episode title. Empty for the latest episode.
        minutes: Minutes of audio to transcribe, 1 to 8.
    """
    audio_url = find_audio_via_rss(show_name, episode_hint)   # iTunes API -> RSS

    # Range-GET only the opening minutes. MP3 is frame-based, so a truncated file
    # still decodes, and this caps both latency and cost.
    key = f"audio/{uuid.uuid4().hex}.mp3"
    chunk = requests.get(audio_url, headers={"Range": f"bytes=0-{minutes*960*1024}"})
    boto3.client("s3").put_object(Bucket=BUCKET, Key=key, Body=chunk.content)

    transcribe = boto3.client("transcribe")
    job = f"listen-{uuid.uuid4().hex[:12]}"
    transcribe.start_transcription_job(
        TranscriptionJobName=job,
        Media={"MediaFileUri": f"s3://{BUCKET}/{key}"},
        MediaFormat="mp3",
        IdentifyLanguage=True,
        LanguageOptions=["en-US", "en-GB", "en-IN"],
        OutputBucketName=BUCKET,
        OutputKey=f"transcripts/{job}.json",
    )
    while True:                                   # Transcribe is asynchronous
        info = transcribe.get_transcription_job(TranscriptionJobName=job)
        status = info["TranscriptionJob"]["TranscriptionJobStatus"]
        if status in ("COMPLETED", "FAILED"):
            break
        time.sleep(5)

    return {"transcript": read_transcript_from_s3(job), "source": "amazon-transcribe"}


agent = Agent(
    model=BedrockModel(
        model_id="us.amazon.nova-2-lite-v1:0",   # tool use + streaming, first-party
        region_name="us-east-1",
        streaming=True,
    ),
    tools=[search_podcast_episodes, listen_to_episode],
    system_prompt=SYSTEM_PROMPT,
)

print(agent("Which AI models did the Deno Is Dead episode actually discuss?"))
```

That is it. `@tool` turns a plain function into something the model can call, and
**the docstring is the tool contract** — it is the only thing the model reads when
deciding. Which is exactly why the next three points matter more than the code does.

Three things I had to get right before the agent used the tool reliably:

**Say what the tool is, and what it is not.** Naming the data source explicitly
("metadata only, never what was said" vs "actual spoken words") is what makes the
model pick correctly.

**Say that the tools are independent.** My first version failed completely: the agent
searched YouTube, found no match, and concluded the episode did not exist — never
trying the tool that uses RSS. One sentence fixed it: *"listen_to_episode is
INDEPENDENT of search_podcast_episodes and reaches a different catalogue. An episode
missing from search results may still be available to listen to."*

**Put the guard rails in the tool, not the prompt.** An unguarded run looped 47
search calls and started 7 Transcribe jobs — about $1.58 for one question — because
the model kept re-asking for more minutes hoping for more content. Prompt rules
helped but were not reliable. What fixed it was making the tool itself enforce
limits: a transcript cache keyed on show and episode that *deliberately ignores*
`minutes`, a hard cap of 2 transcription jobs per question, and failures returned as
data with an explicit `instruction` field rather than raised as exceptions. The agent
now answers the same question with exactly one tool call.

That last point is the biggest thing I learned. A tool that can be called in a loop
by a model that is dissatisfied with the answer needs to defend itself. Treating tool
design as API design — idempotent, cached, bounded, self-describing on failure — is
what turned an expensive, erratic agent into a reliable one.

### Reusing this on your own agent

The specifics here are podcasts, but nothing about the pattern is. If you are wrapping
any slow, paid or rate-limited AWS service as a Strands tool, these five points carry
over directly:

1. **Name the data source in the docstring**, and say what the tool is *not*. "Metadata
   only, never what was said" versus "actual spoken words" is what makes the model
   choose correctly. Vague descriptions produce vague tool selection.
2. **State tool independence explicitly** if two tools read different sources.
   Otherwise the model invents a prerequisite chain and gives up when the first step
   returns nothing. This single sentence was the difference between the tool being
   used and never being touched.
3. **Bound the work inside the tool, not the prompt.** Range-GET a slice instead of a
   whole file. Cap the expensive operation per run. Prompt rules are advisory; code is
   not.
4. **Cache on intent, ignoring the knobs the model will fiddle with.** I key on
   `(show, episode)` and deliberately ignore `minutes`, because "ask again for more
   audio" was the exact loop that cost money.
5. **Return failures as data with an `instruction` field**, never as raised exceptions.
   A model that receives `{"error": ..., "instruction": "do not retry"}` stops. A model
   that receives a stack trace tries again.

For the asynchronous shape: a Transcribe job outlives API Gateway's 30-second ceiling,
so the HTTP handler writes a job record to S3, self-invokes with
`InvocationType="Event"`, and returns a `job_id` to poll. One Lambda serves both the
request and the background run, distinguished by a `mode` field on the event — no
queue, no second function.

## AWS services used

- **Amazon Bedrock** — Amazon Nova 2 Lite drives the agent's tool selection; Llama
  3.3 70B does the heavier content scoring
- **Amazon Transcribe** — the new capability; speech to timestamped text, with
  automatic language identification
- **AWS Lambda** — the agent itself runs here, Python 3.12
- **Amazon S3** — episode audio, Transcribe output, and agent job state
- **Amazon API Gateway** — public HTTP endpoint for the agent
- **Amazon CloudFront** — HTTPS entry point
- **AWS IAM** — a dedicated least-privilege role for the agent
- **AWS Systems Manager Parameter Store** — encrypted API key, resolved at cold start

A note on model choice: Anthropic Claude is unavailable on this account, so I tested
the alternatives and found that Llama 3.3 70B supports tool use only with streaming
disabled, while Amazon Nova 2 Lite handles streaming tool calls cleanly. Nova 2 Lite
drives the agent as a result.

### Architecture

```
POST /api/agent  ->  CloudFront  ->  API Gateway  ->  Lambda (spf-agent)
                                                          |
                                      writes job to S3, self-invokes async
                                                          v
                                               Strands Agent on Bedrock
                                                    |            |
                                      search_podcast_episodes   listen_to_episode
                                                    |                 |
                                            YouTube Data API    iTunes -> RSS -> MP3
                                                                      |
                                                              S3 -> Amazon Transcribe
```

The endpoint is asynchronous because a Transcribe job outlives API Gateway's
30-second ceiling: the POST enqueues work and returns a `job_id`, and the agent runs
in a background invocation that writes its answer to S3.

## Try it

The deployed agent takes a `with_ears` flag, so you can watch the capability appear
and disappear against the same code:

```bash
# with the new tool
curl -X POST https://dad3m4gxlizq4.cloudfront.net/api/agent \
  -H 'Content-Type: application/json' \
  -d '{"question":"On the Syntax podcast, in the episode called Deno Is Dead, which AI models did the hosts actually talk about?","with_ears":true}'

# then poll
curl https://dad3m4gxlizq4.cloudfront.net/api/agent/status/<job_id>

# same question, tool withheld
# -d '{"question":"...","with_ears":false}'
```

**App:** https://dad3m4gxlizq4.cloudfront.net
**Source:** https://github.com/DeepikaSidda/Smart-Podcast-Finder
