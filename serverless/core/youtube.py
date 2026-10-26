"""YouTube Data API v3 search.

Ported from activities/scraper.py. Two deliberate changes:
  * returns plain dicts instead of VideoMetadata dataclasses, so results are
    JSON-serialisable for Step Functions / S3 without a converter
  * synchronous `requests` instead of async httpx — Lambda handlers are sync and
    the parallelism now comes from Step Functions Parallel states, so dropping
    async also drops httpx/anyio/httpcore from the deployment package
"""
import logging
import re

import requests

from .config import settings

log = logging.getLogger(__name__)

YT_BASE = "https://www.googleapis.com/youtube/v3"
MIN_DURATION_SECONDS = 600  # podcasts are 10+ minutes


def _parse_chapters(description: str) -> list:
    """Extract chapter titles from description timestamps like '0:00 Intro'."""
    chapters = []
    for line in description.split("\n"):
        m = re.match(r"\s*(?:\d{1,2}:)?\d{1,2}:\d{2}\s+(.+)", line.strip())
        if m:
            chapters.append(m.group(1).strip())
    return chapters


def _duration_seconds(iso: str) -> int:
    """Convert ISO 8601 duration (PT1H2M3S) to total seconds."""
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso)
    if not m:
        return 0
    h, mins, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mins * 60 + s


def _parse_duration(iso: str) -> str:
    """Convert ISO 8601 duration (PT1H2M3S) to human readable (1:02:03)."""
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso)
    if not m:
        return iso
    h, mins, s = (int(x) if x else 0 for x in m.groups())
    if h:
        return f"{h}:{mins:02d}:{s:02d}"
    return f"{mins}:{s:02d}"


def search_youtube(query: str, interests: str = "", max_results: int = 10) -> dict:
    """Find the channel, then search within it using the user's interests."""
    api_key = settings.youtube_api_key
    if not api_key:
        raise RuntimeError("YOUTUBE_API_KEY is not configured")

    session = requests.Session()
    session.headers.update({"Accept": "application/json"})

    # Step 1: resolve the channel by name (100 quota units)
    ch_resp = session.get(f"{YT_BASE}/search", timeout=25, params={
        "part": "snippet",
        "q": query,
        "type": "channel",
        "maxResults": 1,
        "key": api_key,
    })
    ch_resp.raise_for_status()
    channels = ch_resp.json().get("items", [])

    if not channels:
        log.warning("No channel found for '%s'", query)
        return {"channel_name": "Unknown", "videos": []}

    channel_id = channels[0]["id"]["channelId"]
    channel_name = channels[0]["snippet"]["title"]
    log.info("Found channel: %s (%s)", channel_name, channel_id)

    # Step 2: search within the channel, ranked by YouTube relevance (100 units)
    search_query = interests if interests else query
    vid_resp = session.get(f"{YT_BASE}/search", timeout=25, params={
        "part": "snippet",
        "q": search_query,
        "type": "video",
        "channelId": channel_id,
        "maxResults": min(max_results, 50),
        "order": "relevance",
        "key": api_key,
    })
    vid_resp.raise_for_status()
    vid_items = vid_resp.json().get("items", [])

    if not vid_items:
        log.warning("No relevant videos in %s for '%s'", channel_name, search_query)
        return {"channel_name": channel_name, "videos": []}

    video_ids = [
        item["id"]["videoId"]
        for item in vid_items
        if "videoId" in item.get("id", {})
    ]
    if not video_ids:
        return {"channel_name": channel_name, "videos": []}

    # Step 3: full details, tags and topics (1 unit for up to 50 IDs)
    details_resp = session.get(f"{YT_BASE}/videos", timeout=25, params={
        "part": "snippet,statistics,contentDetails,topicDetails",
        "id": ",".join(video_ids),
        "key": api_key,
    })
    details_resp.raise_for_status()
    details_data = details_resp.json()

    videos = []
    for item in details_data.get("items", []):
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        content = item.get("contentDetails", {})
        raw_duration = content.get("duration", "")
        full_desc = snippet.get("description", "")

        if _duration_seconds(raw_duration) < MIN_DURATION_SECONDS:
            continue

        tags = snippet.get("tags", [])[:15]
        topic_urls = item.get("topicDetails", {}).get("topicCategories", [])
        topic_names = [u.rsplit("/", 1)[-1].replace("_", " ") for u in topic_urls]

        videos.append({
            "title": snippet.get("title", ""),
            "url": f"https://www.youtube.com/watch?v={item['id']}",
            "description": full_desc[:800],
            "views": int(stats.get("viewCount", 0)),
            "likes": int(stats.get("likeCount", 0)),
            "comments": int(stats.get("commentCount", 0)),
            "duration": _parse_duration(raw_duration),
            "date": snippet.get("publishedAt", "")[:10],
            "tags": tags + topic_names,
            "chapters": _parse_chapters(full_desc),
        })

    log.info(
        "Kept %d podcast-length videos (10+ min) from %d total",
        len(videos), len(video_ids),
    )
    return {"channel_name": channel_name, "videos": videos}
