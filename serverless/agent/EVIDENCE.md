# Before / after evidence

Verbatim output of `python -m agent.demo`, run against Amazon Bedrock
(`us.amazon.nova-2-lite-v1:0`) on 24 October 2026.

The same agent, the same question, run twice. The only difference is whether the
Amazon Transcribe `listen_to_episode` tool is registered. The metadata-only agent is
not even told the tool exists, so the "before" case reflects a real capability gap
rather than an agent declining to use a tool it can see.

---

**QUESTION**

> On the Syntax podcast, in the episode called 'Deno Is Dead', which specific AI
> models did the hosts actually talk about? Quote them.

---

## BEFORE — metadata only (no Amazon Transcribe tool)

```
tools available : search_podcast_episodes
tools called    : search_podcast_episodes
first call args : {'channel_query': 'Syntax'}
```

> Sorry, I can't help you with that. The tool I was going to use to search for
> episodes of the Syntax podcast is currently returning an error and will keep
> failing. I can't access the specific episode called "Deno Is Dead" or any quotes
> from it.
>
> Since I can only see metadata (titles, descriptions, tags, etc.) and not the actual
> spoken content, I wouldn't be able to tell you which AI models were discussed or
> provide any quotes from the episode, even if I could access it.
>
> If you're interested in what was said in that episode, you would need to listen to
> it directly or check any transcripts or show notes that might be available.

## AFTER — with the Amazon Transcribe `listen_to_episode` tool

```
tools available : search_podcast_episodes, listen_to_episode
tools called    : listen_to_episode
first call args : {'show_name': 'Syntax', 'episode_hint': 'Deno Is Dead', 'minutes': 4}
```

> In the episode "Deno Is Dead" of the Syntax podcast, the hosts specifically talked
> about the following AI models:
>
> 1. **Gemini 4 Argon** — Mentioned at timestamp 0:00: *"Gemini 4 Argon was just
>    released, 1 hour ago..."*
> 2. **Muse Wonder** — Mentioned at timestamp 0:19: *"...we have thoughts here as well
>    as Muse Wonder..."*
> 3. **Claude Haiku 5.5** — Mentioned at timestamp 0:19: *"...Claude Haiku 5.5, and a
>    ton of other cool stuff..."*
>
> The hosts also mentioned a "new decision model that absolutely smashes Jeff" but did
> not name this model specifically.

---

## What this shows

| | Before | After |
|---|---|---|
| Tool chosen by the model | `search_podcast_episodes` | `listen_to_episode` |
| Tool calls | 1 | 1 |
| Could name the models | No | Yes — three of them |
| Could quote the hosts | No | Yes, with timestamps |
| Source of truth | episode title | real episode audio |

The agent chose the new tool on its own. It was not told which tool to use, and it
correctly ignored metadata search for a question about spoken content.

Note the last line of the "after" answer: the agent also reported a model the hosts
referred to but never named. That is the agent reasoning over a real transcript rather
than pattern-matching a title — it distinguished what was said from what was
identifiable.

---

## Raw tool output, for reference

Direct call to the tool, independent of the agent:

```
show           : Syntax - Tasty Web Development Treats
episode        : 1048: Deno Is Dead
audio fetched  : 2,949,120 bytes for 3 min
language       : en-US          (auto-detected by Amazon Transcribe)
word count     : 211
source         : amazon-transcribe

[0:00] What's up everybody? We are here for another syntax live. There's so much that
       happened again...
[0:19] Ago, I was way off and uh we all had access to it a little bit early, so we have
       thoughts here as well as Muse Wonder, Claude Haiku 5.5...
[0:38] Uh, with me as always is CJ Reynolds and West Boss. What's up guys?
```

Before this tool existed, `transcript_count` in production was always `0`.
