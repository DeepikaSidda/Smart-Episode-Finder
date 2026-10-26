"""LLM analysis steps: interest parsing, ranking, summarisation, deep dive.

Ported from activities/analyzer.py. Prompt construction is unchanged so output
quality matches the Temporal version; pydantic models are replaced with plain
dicts and the same validation/clamping is applied by hand.
"""
import logging

import requests

from .config import settings
from .llm import (
    DEEP_DIVE_PROMPT,
    EXTRACT_PROMPT,
    RANK_PROMPT,
    SUMMARY_PROMPT,
    call_llm,
    extract_json,
)
from .transcripts import extract_video_id, fetch_full_transcript

log = logging.getLogger(__name__)

VALID_TONES = {"educational", "entertainment", "news", "tutorial", "mixed"}


def extract_interests(interests: str) -> dict:
    """Parse a raw interests string into structured keywords + topics."""
    log.info("Extracting interests from: %s", interests[:80])

    response = call_llm(EXTRACT_PROMPT, f"User interests: {interests}")
    data = extract_json(response)

    result = {
        "keywords": data.get("keywords", []) or [],
        "topics": data.get("topics", []) or [],
    }
    log.info(
        "Extracted %d keywords, %d topics",
        len(result["keywords"]), len(result["topics"]),
    )
    return result


def rank_videos(videos: list, keywords: list, topics: list, transcripts: dict) -> list:
    """Score every video 0-100 for relevance, sorted descending."""
    video_lines = []
    for i, v in enumerate(videos, 1):
        parts = [
            f"[{i}] {v.get('title', '')}",
            f"    URL: {v.get('url', '')} | Duration: {v.get('duration', '')} | "
            f"Views: {v.get('views', 0)} | Likes: {v.get('likes', 0)} | "
            f"Comments: {v.get('comments', 0)} | Date: {v.get('date', '')}",
            f"    Description: {v.get('description', '')[:500]}",
        ]
        tags = v.get("tags") or []
        if tags:
            parts.append(f"    Tags: {', '.join(tags[:12])}")
        chapters = v.get("chapters") or []
        if chapters:
            parts.append(f"    Chapters: {', '.join(chapters[:10])}")
        transcript = transcripts.get(v.get("url", ""), "")
        if transcript:
            parts.append(f"    Transcript excerpt: {transcript[:800]}")
        video_lines.append("\n".join(parts))

    videos_text = "\n\n".join(video_lines)

    prompt = f"""Listener keywords: {', '.join(keywords)}
Listener topics: {', '.join(topics)}

{len(videos)} podcast episodes from the channel:

{videos_text}

Score and rank ALL {len(videos)} episodes for this listener. Return every episode with a score."""

    log.info("Ranking %d videos with %s", len(videos), settings.llm_provider)
    response = call_llm(RANK_PROMPT, prompt)
    data = extract_json(response)

    recs = []
    for r in data.get("recommendations", []) or []:
        try:
            recs.append({
                "title": r["title"],
                "url": r["url"],
                "score": min(100, max(0, int(r.get("score", 50)))),
                "why": r.get("why", ""),
                "duration": r.get("duration", ""),
                "views": int(r.get("views", 0) or 0),
            })
        except (KeyError, TypeError, ValueError) as e:
            log.warning("Skipping malformed recommendation: %s", e)

    return sorted(recs, key=lambda x: x["score"], reverse=True)


def generate_summary(
    channel_name: str, videos: list, keywords: list, transcripts: dict
) -> dict:
    """Produce a channel overview, key insights and tone classification."""
    titles_text = "\n".join(f"- {v.get('title', '')}" for v in videos)

    transcript_section = ""
    for v in videos:
        transcript = transcripts.get(v.get("url", ""), "")
        if transcript:
            transcript_section += f"\n--- {v.get('title', '')} ---\n{transcript[:500]}\n"

    transcript_prompt = ""
    if transcript_section:
        transcript_prompt = (
            f"\n\nTranscript excerpts from select episodes:{transcript_section}"
        )

    prompt = f"""Channel: {channel_name}
User interest keywords: {', '.join(keywords)}

Recent video titles:
{titles_text}{transcript_prompt}

Analyze this channel."""

    log.info("Generating summary for %s", channel_name)
    response = call_llm(SUMMARY_PROMPT, prompt)
    data = extract_json(response)

    tone = str(data.get("tone", "mixed")).lower()
    if tone not in VALID_TONES:
        tone = "mixed"

    return {
        "summary": data.get("summary", "") or "",
        "key_insights": data.get("key_insights", []) or [],
        "tone": tone,
    }


def _metadata_fallback(video_id: str) -> str:
    """When no transcript exists, synthesise context from video metadata."""
    try:
        resp = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            timeout=15,
            params={
                "part": "snippet",
                "id": video_id,
                "key": settings.youtube_api_key,
            },
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if items:
            snippet = items[0].get("snippet", {})
            title = snippet.get("title", "")
            desc = snippet.get("description", "")
            tags = snippet.get("tags", []) or []
            text = f"Title: {title}\nDescription: {desc}\nTags: {', '.join(tags[:20])}"
            log.info("Using video metadata as fallback (%d chars)", len(text))
            return text
    except Exception as e:
        log.warning("Metadata fallback also failed: %s", e)
    return ""


def deep_dive_episode(video_url: str) -> dict:
    """Detailed single-episode analysis from its full transcript."""
    video_id = extract_video_id(video_url)
    if not video_id:
        raise ValueError(f"Invalid YouTube URL: {video_url}")

    log.info("Fetching full transcript for %s", video_id)
    full_text = fetch_full_transcript(video_url)
    used_transcript = bool(full_text)

    if not full_text:
        # YouTube blocks most cloud provider IP ranges, so this is the common path
        # in production. The result is derived from the video's own description, not
        # from anything that was said, and must be labelled as such.
        full_text = _metadata_fallback(video_id)

    if not full_text:
        return {
            "tldr": "Transcript not available for this episode.",
            "key_topics": [],
            "key_takeaways": [],
            "notable_quotes": [],
            "timestamps_summary": [],
            "tools_and_resources": [],
            "who_should_watch": "N/A",
            "source": "unavailable",
            "based_on_spoken_content": False,
        }

    # More content yields better analysis; 15k chars keeps us inside the context window.
    truncated = full_text[:15000]

    prompt = f"""Episode transcript (may be truncated):

{truncated}

Analyze this episode in detail."""

    log.info("Generating deep dive for %s (%d chars)", video_id, len(full_text))
    response = call_llm(DEEP_DIVE_PROMPT, prompt)
    result = extract_json(response)

    # Guarantee the keys the UI reads are always present.
    result.setdefault("tldr", "")
    result.setdefault("key_topics", [])
    result.setdefault("key_takeaways", [])
    result.setdefault("notable_quotes", [])
    result.setdefault("timestamps_summary", [])
    result.setdefault("tools_and_resources", [])
    result.setdefault("who_should_watch", "")

    # Be explicit about provenance so the UI never presents a description summary as
    # though it were a record of what was said.
    result["source"] = "youtube-transcript" if used_transcript else "video-metadata"
    result["based_on_spoken_content"] = used_transcript
    if not used_transcript:
        result["notable_quotes"] = []
        result["timestamps_summary"] = []
        result["provenance_note"] = (
            "No transcript was available for this video, so this analysis is based on "
            "the video's title, description and tags - not on what was actually said. "
            "Quotes and timeline have been omitted because they cannot be verified."
        )
    return result
