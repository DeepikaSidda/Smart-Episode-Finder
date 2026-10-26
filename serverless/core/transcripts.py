"""YouTube transcript fetching.

Ported from activities/transcript.py.

Note: YouTube blocks most AWS IP ranges, so these calls generally fail from Lambda
just as they did from EC2. Every failure is swallowed per-video and the pipeline
continues with metadata-only ranking, which is the pre-existing behaviour.
"""
import logging
import re

from youtube_transcript_api import YouTubeTranscriptApi

log = logging.getLogger(__name__)

TRANSCRIPT_LANGUAGES = ["en", "en-US", "en-GB", "hi"]
EXCERPT_CHARS = 2000  # keep LLM context manageable


def extract_video_id(url: str):
    """Extract the video ID from a YouTube watch URL."""
    m = re.search(r"[?&]v=([^&]+)", url)
    return m.group(1) if m else None


def fetch_transcripts(videos: list) -> dict:
    """Fetch transcript excerpts for a list of video dicts.

    Returns a {url: excerpt} map containing only videos that produced a transcript.
    """
    api = YouTubeTranscriptApi()
    transcripts = {}

    for video in videos:
        url = video.get("url", "")
        video_id = extract_video_id(url)
        if not video_id:
            continue

        try:
            transcript = api.fetch(video_id, languages=TRANSCRIPT_LANGUAGES)
            full_text = " ".join(s.text for s in transcript.snippets)
            if full_text:
                transcripts[url] = full_text[:EXCERPT_CHARS]
                log.info(
                    "Got transcript for %s (%d chars)",
                    video.get("title", "")[:50], len(full_text),
                )
        except Exception as e:
            log.warning("No transcript for '%s': %s", video.get("title", "")[:50], e)

    log.info("Fetched %d/%d transcripts", len(transcripts), len(videos))
    return transcripts


def fetch_full_transcript(video_url: str) -> str:
    """Fetch the complete transcript for one video, trying fallback languages.

    Returns an empty string when no transcript is available.
    """
    video_id = extract_video_id(video_url)
    if not video_id:
        raise ValueError(f"Invalid YouTube URL: {video_url}")

    api = YouTubeTranscriptApi()

    try:
        transcript = api.fetch(
            video_id, languages=["en", "en-US", "en-GB", "hi", "hi-IN"]
        )
        return " ".join(s.text for s in transcript.snippets)
    except Exception:
        pass

    # Fallback: whatever language the video does have
    try:
        available = list(api.list(video_id))
        if available:
            transcript = api.fetch(video_id, languages=[available[0].language_code])
            log.info("Used fallback language: %s", available[0].language_code)
            return " ".join(s.text for s in transcript.snippets)
    except Exception as e:
        log.warning("No transcript available: %s", e)

    return ""
