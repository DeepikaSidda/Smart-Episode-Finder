"""Activities Lambda — invoked by Step Functions once per pipeline step.

Event shape:
    {"action": "<step name>", "job_id": "...", ...}

Only job_id travels through the state machine; the working set lives in S3. That
keeps state payloads tiny and sidesteps the 256 KB Step Functions limit.

Concurrency: actions that run inside a Parallel state (extract_interests,
fetch_transcripts, rank, summary) write ONLY their own part key and never touch
state.json, so the two branches cannot clobber each other.
"""
import logging

from core import jobs
from core.redact import redact
from core.analyze import (
    deep_dive_episode,
    extract_interests,
    generate_summary,
    rank_videos,
)
from core.spotify import search_spotify
from core.youtube import search_youtube

logging.getLogger().setLevel(logging.INFO)
log = logging.getLogger(__name__)


# --- pipeline steps (sequential) ---

def _do_search(job_id: str, event: dict) -> dict:
    state = jobs.get_state(job_id) or {}
    provider = state.get("provider", "youtube")
    query = state.get("channel_query", "")
    interests = state.get("interests", "")
    max_videos = int(state.get("max_videos", 10))

    label = "Spotify" if provider == "spotify" else "YouTube"
    jobs.set_state(job_id, jobs.PHASE_SEARCHING, f"Searching {label} for: {query}")

    search_fn = search_spotify if provider == "spotify" else search_youtube
    result = search_fn(query, interests, max_videos)

    jobs.put_part(job_id, "search", result)

    videos = result.get("videos") or []
    if videos:
        # Advance the phase here: this is the last sequential point before the
        # first Parallel state, whose branches must not write state.json.
        jobs.set_state(
            job_id, jobs.PHASE_PARSING, "Parsing interests & fetching transcripts"
        )

    return {
        "job_id": job_id,
        "provider": provider,
        "video_count": len(videos),
        "has_videos": bool(videos),
    }


def _do_stage2_prep(job_id: str, event: dict) -> dict:
    """Sequential step between the two Parallel stages.

    Exists so the ranking phase can be published without a Parallel branch
    writing state.json.
    """
    search = jobs.get_part(job_id, "search", default={}) or {}
    videos = search.get("videos") or []
    jobs.set_state(
        job_id,
        jobs.PHASE_RANKING,
        f"Ranking & summarizing {len(videos)} videos",
    )
    return {"job_id": job_id, "video_count": len(videos)}


def _do_finalize(job_id: str, event: dict) -> dict:
    state = jobs.get_state(job_id) or {}
    search = jobs.get_part(job_id, "search", default={}) or {}
    transcripts = jobs.get_part(job_id, "transcripts", default={}) or {}
    recommendations = jobs.get_part(job_id, "rank", default=[]) or []
    summary = jobs.get_part(job_id, "summary", default={}) or {}
    videos = search.get("videos") or []

    result = {
        "workflow_id": job_id,
        "channel_name": search.get("channel_name", "Unknown"),
        "recommendations": recommendations,
        "summary": summary.get("summary", ""),
        "key_insights": summary.get("key_insights", []),
        "tone": summary.get("tone", "mixed"),
        "video_count": len(videos),
        "provider": state.get("provider", "youtube"),
        "transcripts": transcripts,
        "transcript_count": len(transcripts),
    }

    jobs.put_part(job_id, "result", result)
    jobs.set_state(
        job_id,
        jobs.PHASE_COMPLETED,
        f"Found {len(recommendations)} recommendations",
    )
    return {"job_id": job_id, "recommendation_count": len(recommendations)}


def _do_finalize_empty(job_id: str, event: dict) -> dict:
    """No videos matched — mirrors the early return in the Temporal workflow."""
    state = jobs.get_state(job_id) or {}
    search = jobs.get_part(job_id, "search", default={}) or {}
    query = state.get("channel_query", "")

    result = {
        "workflow_id": job_id,
        "channel_name": search.get("channel_name", "Unknown"),
        "recommendations": [],
        "summary": f"No videos found for '{query}'.",
        "key_insights": [],
        "tone": "unknown",
        "video_count": 0,
        "provider": state.get("provider", "youtube"),
        "transcripts": {},
        "transcript_count": 0,
    }
    jobs.put_part(job_id, "result", result)
    jobs.set_state(job_id, jobs.PHASE_COMPLETED, "No videos found")
    return {"job_id": job_id, "recommendation_count": 0}


# --- pipeline steps (inside Parallel states: own part key only) ---

def _do_extract_interests(job_id: str, event: dict) -> dict:
    state = jobs.get_state(job_id) or {}
    result = extract_interests(state.get("interests", ""))
    jobs.put_part(job_id, "interests", result)
    return {"job_id": job_id, "keyword_count": len(result.get("keywords", []))}


def _do_fetch_transcripts(job_id: str, event: dict) -> dict:
    """Transcripts are best-effort.

    Spotify has none, and YouTube blocks most AWS IP ranges, so any failure is
    swallowed and the pipeline proceeds with metadata-only ranking. This mirrors
    the try/except around the Temporal version's asyncio.gather.
    """
    state = jobs.get_state(job_id) or {}
    if state.get("provider") == "spotify":
        jobs.put_part(job_id, "transcripts", {})
        return {"job_id": job_id, "transcript_count": 0, "skipped": True}

    search = jobs.get_part(job_id, "search", default={}) or {}
    videos = search.get("videos") or []

    try:
        from core.transcripts import fetch_transcripts
        transcripts = fetch_transcripts(videos)
    except Exception as e:
        log.warning("Transcript stage failed entirely, continuing without: %s", e)
        transcripts = {}

    jobs.put_part(job_id, "transcripts", transcripts)
    return {"job_id": job_id, "transcript_count": len(transcripts)}


def _do_rank(job_id: str, event: dict) -> dict:
    search = jobs.get_part(job_id, "search", default={}) or {}
    interests = jobs.get_part(job_id, "interests", default={}) or {}
    transcripts = jobs.get_part(job_id, "transcripts", default={}) or {}

    recs = rank_videos(
        videos=search.get("videos") or [],
        keywords=interests.get("keywords") or [],
        topics=interests.get("topics") or [],
        transcripts=transcripts,
    )
    jobs.put_part(job_id, "rank", recs)
    return {"job_id": job_id, "recommendation_count": len(recs)}


def _do_summary(job_id: str, event: dict) -> dict:
    search = jobs.get_part(job_id, "search", default={}) or {}
    interests = jobs.get_part(job_id, "interests", default={}) or {}
    transcripts = jobs.get_part(job_id, "transcripts", default={}) or {}

    result = generate_summary(
        channel_name=search.get("channel_name", ""),
        videos=search.get("videos") or [],
        keywords=interests.get("keywords") or [],
        transcripts=transcripts,
    )
    jobs.put_part(job_id, "summary", result)
    return {"job_id": job_id, "tone": result.get("tone")}


# --- deep dive ---

def _do_deep_dive(job_id: str, event: dict) -> dict:
    state = jobs.get_state(job_id) or {}
    video_url = state.get("video_url", "")
    video_title = state.get("video_title", "")

    jobs.set_state(job_id, "analyzing", f"Analyzing: {video_title[:50]}")

    result = deep_dive_episode(video_url)
    result["video_title"] = video_title
    result["video_url"] = video_url

    jobs.put_part(job_id, "result", result)
    jobs.set_state(job_id, jobs.PHASE_COMPLETED, "Deep dive complete")
    return {"job_id": job_id}


ACTIONS = {
    "search": _do_search,
    "extract_interests": _do_extract_interests,
    "fetch_transcripts": _do_fetch_transcripts,
    "stage2_prep": _do_stage2_prep,
    "rank": _do_rank,
    "summary": _do_summary,
    "finalize": _do_finalize,
    "finalize_empty": _do_finalize_empty,
    "deep_dive": _do_deep_dive,
}


def handler(event, context):
    action = event.get("action")
    job_id = event.get("job_id")

    if action not in ACTIONS:
        raise ValueError(f"Unknown action '{action}'")
    if not job_id:
        raise ValueError("job_id is required")

    log.info("action=%s job_id=%s", action, job_id)

    try:
        return ACTIONS[action](job_id, event)
    except Exception as e:
        # Record the failure so /api/status can surface it, then re-raise so
        # Step Functions can apply its Retry/Catch policy.
        log.exception("action=%s job_id=%s failed", action, job_id)
        # Redacted before persisting: requests/httpx exception text embeds the full
        # request URL, which for the YouTube API contains the API key.
        safe = redact(f"{type(e).__name__}: {e}")[:400]
        try:
            jobs.set_state(
                job_id,
                jobs.PHASE_FAILED,
                f"{action} failed: {safe}"[:400],
                error=safe,
            )
        except Exception:
            log.exception("could not record failure state for %s", job_id)
        raise
