# Demo video script — target 2:30, hard limit 3:00

The whole video is one idea: **the agent could read, now it can listen.** Show the
contrast early and let it breathe. Do not tour the architecture.

## Before you record

```powershell
cd serverless
$env:PYTHONIOENCODING="utf-8"
```

Run `python -m agent.demo` once beforehand. It warms the model, confirms the
transcript is cached, and tells you roughly how long the live run takes.

Make the terminal font large. Judges may watch on a laptop.

---

## 0:00–0:20 — The hole

> "This is Smart Episode Finder. It's an agent that recommends podcast episodes, and
> it's been live on AWS for weeks. It had one problem I couldn't ignore."

Show the old production result on screen — the field `transcript_count: 0`.

> "It recommends hours of audio. It has never heard a single second of any of it.
> Every recommendation came from reading titles. YouTube blocks AWS IP ranges, so my
> transcript step returned nothing, always."

## 0:20–0:50 — Before

Run the before case. Show the question on screen.

> "So I asked it a question it cannot possibly answer from a title: which AI models
> did the hosts actually talk about in this episode?"

Let the answer appear. Highlight the admission.

> "One tool: metadata search. And to its credit, it's honest — it says it cannot
> reveal the actual spoken content. It won't guess. But it also can't help."

## 0:50–1:40 — The new tool, and after

> "So I gave it one new tool: `listen_to_episode`, powered by Amazon Transcribe."
>
> "Here's the trick. YouTube blocks AWS, and that's not changing. But podcasts are
> also published as plain MP3s in RSS feeds, on CDNs that don't block datacenters. So
> the tool goes around the block — finds the feed, pulls just the opening few minutes
> of real audio into S3, and runs Amazon Transcribe over it."

Run the after case. Stay quiet while it works, then read the result.

> "Same agent. Same question. One tool call — and it chose the new tool on its own."
>
> "Gemini 4 Argon at zero seconds. Muse Wonder and Claude Haiku 5.5 at nineteen
> seconds. Real quotes, real timestamps, from real audio."

If your run includes it, point at the unnamed-model line:

> "And look at this — it also caught a model the hosts mentioned but never named. It's
> reasoning over a transcript, not pattern-matching a title."

## 1:40–2:10 — It's deployed, and it's reliable

Show the curl call against the live endpoint.

> "The agent runs on Lambda, behind API Gateway and CloudFront. It takes a
> `with_ears` flag, so you can watch the capability appear and disappear against
> identical code."

> "The hardest part wasn't wiring the tool up — it was making the agent use it
> *reliably*. My first version looped forty-seven search calls and started seven
> transcription jobs for one question, about a dollar sixty, because the model kept
> asking for more audio. Prompt rules didn't fix that. What fixed it was making the
> tool defend itself: cache the transcript, ignore repeat requests for more minutes,
> cap the jobs, and return failures as data with instructions instead of raising."

> "One tool call now. Every time."

## 2:10–2:30 — Close

> "One tool. Amazon Transcribe. It turned an agent that guessed from titles into one
> that quotes what was actually said."
>
> "Links to the live app and the source are in the description."

---

## Shot list

| Time | On screen |
|---|---|
| 0:00 | Old result JSON showing `transcript_count: 0` |
| 0:20 | Terminal: before run, metadata-only answer |
| 0:50 | The `@tool` function + the RSS-to-Transcribe diagram |
| 1:10 | Terminal: after run, quotes with timestamps |
| 1:40 | curl against the live CloudFront endpoint |
| 2:10 | Final frame: app URL + repo URL |

## Do not

- Walk through IAM policies or the build script
- Apologise for the transcript only covering the opening minutes — frame it as the
  deliberate cost control it is
- Read the whole transcript aloud; the three model names are the payload
- Run over 3:00. Entries are disqualified on requirements, and this is one of them
