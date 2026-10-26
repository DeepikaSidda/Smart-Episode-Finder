"""Spotify show/episode search.

Ported from activities/spotify.py, converted to sync `requests` and plain dicts.
Requires SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET; without them this raises,
matching the behaviour of the previous deployment where Spotify was unconfigured.
"""
import base64
import logging

import requests

from .config import settings

log = logging.getLogger(__name__)

SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_BASE = "https://api.spotify.com/v1"
MIN_DURATION_MS = 600_000  # podcasts are 10+ minutes


def _get_token() -> str:
    if not settings.spotify_client_id or not settings.spotify_client_secret:
        raise RuntimeError(
            "Spotify is not configured. Set SPOTIFY_CLIENT_ID and "
            "SPOTIFY_CLIENT_SECRET to enable the Spotify provider."
        )

    credentials = base64.b64encode(
        f"{settings.spotify_client_id}:{settings.spotify_client_secret}".encode()
    ).decode()

    resp = requests.post(
        SPOTIFY_TOKEN_URL,
        timeout=10,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"grant_type": "client_credentials"},
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _format_duration(ms: int) -> str:
    total_secs = ms // 1000
    h, remainder = divmod(total_secs, 3600)
    m, s = divmod(remainder, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def search_spotify(query: str, interests: str = "", max_results: int = 10) -> dict:
    """Find a Spotify show by name, then fetch its episodes."""
    token = _get_token()
    headers = {"Authorization": f"Bearer {token}"}
    session = requests.Session()

    resp = session.get(f"{SPOTIFY_BASE}/search", headers=headers, timeout=25, params={
        "q": query,
        "type": "show",
        "market": "US",
        "limit": 1,
    })
    resp.raise_for_status()
    shows = resp.json().get("shows", {}).get("items", [])

    if not shows:
        log.warning("No Spotify show found for '%s'", query)
        return {"channel_name": "Unknown", "videos": []}

    show = shows[0]
    show_name = show["name"]
    show_id = show["id"]
    log.info("Found Spotify show: %s (%s)", show_name, show_id)

    resp = session.get(
        f"{SPOTIFY_BASE}/shows/{show_id}/episodes",
        headers=headers,
        timeout=25,
        params={"market": "US", "limit": min(max_results, 50)},
    )
    resp.raise_for_status()
    episodes = resp.json().get("items", [])

    videos = []
    for ep in episodes:
        duration_ms = ep.get("duration_ms", 0)
        if duration_ms < MIN_DURATION_MS:
            continue

        videos.append({
            "title": ep.get("name", ""),
            "url": ep.get("external_urls", {}).get("spotify", ""),
            "description": (ep.get("description", "") or "")[:800],
            "views": 0,
            "likes": 0,
            "comments": 0,
            "duration": _format_duration(duration_ms),
            "date": (ep.get("release_date", "") or "")[:10],
            "tags": [],
            "chapters": [],
        })

    log.info(
        "Kept %d podcast-length episodes (10+ min) from %d total",
        len(videos), len(episodes),
    )
    return {"channel_name": show_name, "videos": videos}
