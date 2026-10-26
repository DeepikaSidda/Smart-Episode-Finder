"""Strands tools for the podcast agent.

Two groups:

  EXISTING capability — `search_podcast_episodes` wraps the YouTube Data API search
  the app already had. This is all the agent could do before: read metadata.

  NEW capability — `listen_to_episode` is the tool added for this challenge. It
  routes around YouTube's block on AWS IP ranges by pulling the episode's MP3 from
  its podcast RSS enclosure and running Amazon Transcribe over it.

Keeping the tool surface deliberately small: one thing the agent could already do,
one thing it could not.
"""
import logging
import os
import re

from strands import tool

from core.youtube import search_youtube

from .listen_tool import ListenError
from .listen_tool import listen_to_episode as _listen

log = logging.getLogger(__name__)

JOBS_BUCKET = os.environ.get("JOBS_BUCKET", "spf-jobs-414691912352")
AWS_REGION = os.environ.get("BEDROCK_REGION", "us-east-1")

# Populated so a demo can show exactly which tools the agent chose.
TOOL_CALLS = []

# Guard rails. An LLM that is unhappy with a tool result will happily call it again
# in a loop. For `listen_to_episode` every call is a paid Amazon Transcribe job, and
# for search every call burns YouTube Data API quota, so the tools enforce their own
# limits rather than trusting the model to behave.
_TRANSCRIPT_CACHE = {}
_CALL_COUNTS = {}
# Distinct paid Amazon Transcribe jobs allowed per question.
MAX_LISTEN_JOBS_PER_RUN = 2
# Total listen_to_episode invocations allowed per question, cache hits included.
# Without this the model can keep re-asking with slightly different arguments; once
# we have a transcript, further attempts add latency and never add information.
MAX_LISTEN_CALLS_PER_RUN = 3
MAX_SEARCH_CALLS_PER_RUN = 3


# The most recent transcript produced in this run, so the caller can show the raw
# Amazon Transcribe output alongside the agent's answer.
LAST_TRANSCRIPT = {}


def reset_guards():
    """Clear per-run caches and counters. Called before each agent invocation."""
    TOOL_CALLS.clear()
    _TRANSCRIPT_CACHE.clear()
    _CALL_COUNTS.clear()
    LAST_TRANSCRIPT.clear()


def _bump(name):
    _CALL_COUNTS[name] = _CALL_COUNTS.get(name, 0) + 1
    return _CALL_COUNTS[name]


def _norm(text) -> str:
    """Normalise a show name or episode hint into a stable cache key.

    The model rarely repeats arguments verbatim: "Deno Is Dead", "the Deno Is Dead
    episode" and "Deno is dead?" should all resolve to one cached transcript.
    Reducing to a sorted set of significant words makes the cache hit regardless of
    word order, case, filler words or punctuation.
    """
    words = sorted({w for w in re.findall(r"\w+", str(text or "").lower()) if len(w) > 2})
    return " ".join(words)


@tool
def search_podcast_episodes(channel_query: str, interests: str = "", max_results: int = 8) -> dict:
    """Search a YouTube podcast channel and return episode metadata.

    Returns only what is published alongside the video: titles, descriptions, tags,
    chapters, duration and view counts. It does NOT return anything that was said
    inside the episode.

    Args:
        channel_query: Name of the podcast channel to search, e.g. "Syntax" or "Apna College".
        interests: Topics the listener cares about, used to rank the search.
        max_results: How many episodes to return, between 3 and 20.
    """
    max_results = max(3, min(int(max_results), 20))
    TOOL_CALLS.append({"tool": "search_podcast_episodes", "channel_query": channel_query})
    log.info("TOOL search_podcast_episodes(%r, %r)", channel_query, interests)

    if _bump("search") > MAX_SEARCH_CALLS_PER_RUN:
        return {
            "error": "search_limit_reached",
            "episodes": [],
            "contains_spoken_content": False,
            "instruction": (
                "You have already searched the maximum number of times. Do not call "
                "this tool again. Answer using what you have, and state plainly what "
                "you could not determine."
            ),
        }

    try:
        result = search_youtube(channel_query, interests, max_results)
    except Exception as e:
        # Surfacing the failure as data rather than raising stops the model from
        # retrying a quota error in a tight loop.
        return {
            "error": f"{type(e).__name__}: {e}"[:200],
            "episodes": [],
            "contains_spoken_content": False,
            "instruction": (
                "This tool failed and will keep failing. Do not retry it. Either use "
                "listen_to_episode instead, or tell the user what you cannot determine."
            ),
        }
    episodes = [
        {
            "title": v["title"],
            "url": v["url"],
            "duration": v["duration"],
            "views": v["views"],
            "description_excerpt": (v.get("description") or "")[:300],
            "tags": (v.get("tags") or [])[:8],
            "chapters": (v.get("chapters") or [])[:8],
        }
        for v in result.get("videos", [])
    ]
    return {
        "channel_name": result.get("channel_name", "Unknown"),
        "episode_count": len(episodes),
        "episodes": episodes,
        "contains_spoken_content": False,
        "note": "Metadata only. Nothing here reflects what was actually said in the episode.",
    }


@tool
def listen_to_episode(show_name: str, episode_hint: str = "", minutes: int = 4) -> dict:
    """Listen to a podcast episode and return a transcript of what was ACTUALLY SAID.

    Call this for ANY question about episode content: what was discussed, claimed or
    mentioned, who appeared, whether a topic really came up, or to quote the hosts.

    This reads the podcast's public RSS feed and transcribes the real audio with
    Amazon Transcribe. It is INDEPENDENT of search_podcast_episodes and reaches a
    different catalogue, so call it directly — it works even when an episode does not
    appear in YouTube search results.

    Args:
        show_name: The podcast's name, e.g. "Syntax" or "Darknet Diaries".
        episode_hint: Words from the episode title. Leave empty for the latest episode.
        minutes: Minutes of audio to transcribe, 1 to 45. Each minute is a paid
            Amazon Transcribe job, so use the value the caller asked for and do not
            increase it on your own.
    """
    TOOL_CALLS.append({
        "tool": "listen_to_episode",
        "show_name": show_name,
        "episode_hint": episode_hint,
        "minutes": minutes,
    })
    log.info("TOOL listen_to_episode(%r, %r, %s min)", show_name, episode_hint, minutes)

    # Cache on normalised show+episode, deliberately ignoring `minutes`. Re-asking
    # for more audio is the exact loop that costs money and time, so any repeat
    # request returns the transcript we already have, with an instruction to stop.
    total_calls = _bump("listen_calls")
    cache_key = (_norm(show_name), _norm(episode_hint))

    def _reuse(entry, reason):
        out = dict(entry)
        out["cached"] = True
        out["instruction"] = (
            f"{reason} This is the complete transcript available to you. Do NOT call "
            "this tool again and do not request more minutes. Answer from what is "
            "here; if something is not in the transcript, say it was not discussed "
            "in the portion transcribed."
        )
        return out

    # 1. exact match on the normalised key
    if cache_key in _TRANSCRIPT_CACHE:
        return _reuse(
            _TRANSCRIPT_CACHE[cache_key],
            "You already have the transcript for this episode.",
        )

    # 2. same show, different phrasing of the episode. Transcribing the same show
    #    twice in one question is almost always the model second-guessing itself.
    for (cached_show, _hint), entry in _TRANSCRIPT_CACHE.items():
        if cached_show == cache_key[0]:
            return _reuse(
                entry,
                f"You already transcribed '{entry.get('episode_title')}' from this show.",
            )

    # 3. budgets. If anything has been transcribed, hand it back rather than
    #    returning a bare error the model might try to work around.
    over_calls = total_calls > MAX_LISTEN_CALLS_PER_RUN
    over_jobs = _bump("listen_jobs") > MAX_LISTEN_JOBS_PER_RUN
    if over_calls or over_jobs:
        if _TRANSCRIPT_CACHE:
            last = list(_TRANSCRIPT_CACHE.values())[-1]
            return _reuse(last, "The per-question transcription limit is reached.")
        return {
            "error": "transcription_limit_reached",
            "transcript": "",
            "contains_spoken_content": False,
            "instruction": (
                "The per-question transcription limit has been reached. Do not call "
                "this tool again. Tell the user what you could not determine."
            ),
        }

    try:
        out = _listen(
            show_name=show_name,
            episode_hint=episode_hint,
            minutes=minutes,
            bucket=JOBS_BUCKET,
            region=AWS_REGION,
        )
    except ListenError as e:
        return {
            "error": str(e),
            "transcript": "",
            "contains_spoken_content": False,
            "instruction": "Do not retry with different minutes; this will not help.",
        }

    payload = {
        "show": out["show"],
        "episode_title": out["episode_title"],
        "hint_matched": out["hint_matched"],
        "available_episodes": out["available_episodes"],
        "language_detected": out["language_detected"],
        "minutes_transcribed": out["minutes_requested"],
        "word_count": out["word_count"],
        "transcript": out["transcript"],
        # Trimmed for the model's context window only. The UI gets the full set
        # from LAST_TRANSCRIPT below.
        "segments": out["segments"][:60],
        "contains_spoken_content": True,
        "cached": False,
        "source": "amazon-transcribe",
        "instruction": (
            "This is the transcript of the opening of the episode. It is the complete "
            "transcript available to you — do NOT call this tool again for this "
            "episode and do not request more minutes. Quote it with timestamps. If a "
            "topic is absent, say it was not discussed in the transcribed portion. "
            "If hint_matched is false, the requested title was not in the feed and the "
            "latest episode was transcribed instead; check available_episodes."
        ),
    }
    _TRANSCRIPT_CACHE[cache_key] = payload
    LAST_TRANSCRIPT.clear()
    LAST_TRANSCRIPT.update({
        "show": out["show"],
        "episode_title": out["episode_title"],
        "language_detected": out["language_detected"],
        "minutes_transcribed": out["minutes_requested"],
        "word_count": out["word_count"],
        "transcript": out["transcript"],
        "segments": out["segments"],
    })
    return payload
