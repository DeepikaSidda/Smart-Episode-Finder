"""THE NEW TOOL: give the agent ears.

Background — the gap this closes
--------------------------------
Smart Episode Finder could only ever read *metadata*: titles, descriptions, tags.
It has a transcript step, but YouTube blocks requests from AWS IP ranges, so in
production `transcript_count` was always 0 and every ranking decision came from a
title. The agent could tell you an episode was *called* "Deno Is Dead". It could
not tell you a single thing that was actually said in it.

How this tool closes it
-----------------------
Podcasts are published as plain MP3 enclosures in an RSS feed, served by podcast
CDNs that -- unlike YouTube -- do not block datacenter IPs. So instead of fighting
the block, this tool goes around it:

    iTunes Search API  ->  RSS feed  ->  MP3 enclosure
                                             |
                            range-GET first N minutes only
                                             |
                                      Amazon S3  ->  Amazon Transcribe
                                             |
                                   real, timestamped transcript

Two deliberate engineering choices:

* **Partial fetch.** Episodes run to 250 MB. You do not need all of it to know what
  an episode covers, so we range-GET only the opening few minutes. That keeps the
  Transcribe job to seconds-to-a-minute instead of 20+, and caps the cost per call.
* **No API key anywhere.** The iTunes Search API needs no credentials, and RSS is
  public. The only AWS permissions required are S3 read/write and Transcribe.
"""
import json
import logging
import re
import time
import uuid
import xml.etree.ElementTree as ET

import boto3
import requests

log = logging.getLogger(__name__)

ITUNES_SEARCH = "https://itunes.apple.com/search"
USER_AGENT = "SmartPodcastFinder/1.0 (+https://github.com/DeepikaSidda/Smart-Podcast-Finder)"
HEADERS = {"User-Agent": USER_AGENT}

# MP3 podcasts are typically 64-192 kbps; 128 kbps ~= 960 KB per minute. Used only
# to size the partial download, so being approximate is fine.
KB_PER_MINUTE = 960
DEFAULT_MINUTES = 10
# Amazon Transcribe bills per second of audio, so this ceiling is a cost and latency
# control rather than a technical limit. At roughly $0.024/minute, 45 minutes is
# about $1.08 and covers most of a typical episode.
MAX_MINUTES = 45

TRANSCRIBE_POLL_SECONDS = 5
TRANSCRIBE_TIMEOUT_SECONDS = 600

# Fraction of the query's significant words that must appear in a show's name before
# it is considered a match at all, unless the query is an outright substring.
MIN_NAME_COVERAGE = 0.7


class ListenError(RuntimeError):
    """Raised when audio cannot be located or transcribed."""


# --------------------------------------------------------------------------- #
# Step 1-2: locate a real audio file for the show
# --------------------------------------------------------------------------- #

def _words(s: str) -> set:
    return {w for w in re.findall(r"\w+", (s or "").lower()) if len(w) > 2}


def _feed_score(query: str, result: dict):
    """Rank candidate podcasts for a search term.

    Two traps here, both hit in testing with the term "Syntax":
      * taking the first iTunes result gave a horror audio drama
      * weighting popularity too highly gave "Best Drum and Bass Podcast", because
        it posts daily and happens to feature an artist called "Bad Syntax"

    So name evidence dominates. `coverage` is the fraction of the query's significant
    words present in the show's name, scaled to outweigh popularity entirely. A
    candidate sharing no words with the query scores None and is rejected outright --
    transcribing the wrong show is far worse than admitting we cannot find it.
    """
    name = result.get("collectionName", "")
    name_lower = name.lower()
    query_lower = query.lower().strip()
    query_words = _words(query)
    if not query_words:
        return None

    matched = len(query_words & _words(name))
    coverage = matched / len(query_words)
    substring = query_lower in name_lower

    # Reject weak matches outright. Without this, "totally made up show" matched
    # "Klein/Ally Show" on the single word "show".
    if not substring and coverage < MIN_NAME_COVERAGE:
        return None

    # A name that *starts* with the query is the strongest signal available. It is
    # what separates "Syntax - Tasty Web Development Treats" from "Best Drum and Bass
    # Podcast - Hosted by Bad Syntax", which also legitimately contains the word.
    prefix = 8 if name_lower.startswith(query_lower) else 0
    exact = 5 if substring else 0

    # Episode count only ever breaks ties between comparable name matches, so it is
    # scaled to stay below the name signals. Spread over 2000 so it can actually
    # discriminate between a 50-episode show and a 1000-episode one.
    popularity = min(result.get("trackCount", 0), 2000) / 2000.0 * 3
    return coverage * 10 + exact + prefix + popularity


def find_feed_url(show_name: str) -> dict:
    """Resolve a podcast name to its RSS feed via the iTunes Search API."""
    resp = requests.get(
        ITUNES_SEARCH,
        # 50, not 10: the iTunes relevance order is unstable, and for the term
        # "Syntax" the well-known web development show only appears beyond result 25.
        params={"term": show_name, "entity": "podcast", "limit": 50},
        headers=HEADERS,
        timeout=20,
    )
    resp.raise_for_status()
    raw = [r for r in resp.json().get("results", []) if r.get("feedUrl")]

    if not raw:
        raise ListenError(f"No podcast RSS feed found for '{show_name}'")

    scored = []
    for r in raw:
        s = _feed_score(show_name, r)
        if s is not None:
            scored.append((s, r))

    if not scored:
        names = [r.get("collectionName", "?") for r in raw[:5]]
        raise ListenError(
            f"No podcast whose name matches '{show_name}'. Closest results were "
            f"{names}. Retry with a fuller, more exact show name."
        )

    scored.sort(key=lambda pair: pair[0], reverse=True)
    best = scored[0][1]

    return {
        "show": best.get("collectionName", show_name),
        "feed_url": best["feedUrl"],
        "artwork": best.get("artworkUrl600", ""),
        "genre": best.get("primaryGenreName", ""),
        "episode_count": best.get("trackCount", 0),
        "match_score": round(scored[0][0], 2),
        "other_matches": [r.get("collectionName", "") for _s, r in scored[1:4]],
    }


def _text(item, tag):
    return (item.findtext(tag) or "").strip()


def list_episodes(feed_url: str, limit: int = 40) -> list:
    """Parse an RSS feed into a list of episodes that have audio enclosures."""
    resp = requests.get(feed_url, headers=HEADERS, timeout=30)
    resp.raise_for_status()

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        raise ListenError(f"Could not parse RSS feed: {e}") from e

    episodes = []
    for item in root.iter("item"):
        enclosure = item.find("enclosure")
        if enclosure is None:
            continue
        url = enclosure.get("url")
        mime = enclosure.get("type") or ""
        if not url or "audio" not in mime:
            continue
        episodes.append({
            "title": _text(item, "title"),
            "published": _text(item, "pubDate"),
            "audio_url": url,
            "mime": mime,
        })
        if len(episodes) >= limit:
            break

    if not episodes:
        raise ListenError("RSS feed contained no audio enclosures")
    return episodes


def _score_match(hint: str, title: str) -> int:
    """Cheap word-overlap score so the agent can name an episode loosely."""
    hint_words = {w for w in re.findall(r"\w+", hint.lower()) if len(w) > 2}
    if not hint_words:
        return 0
    title_words = {w for w in re.findall(r"\w+", title.lower()) if len(w) > 2}
    overlap = len(hint_words & title_words)
    if hint.lower().strip() in title.lower():
        overlap += 10
    return overlap


def pick_episode(episodes: list, episode_hint: str = "") -> tuple:
    """Choose the episode best matching the hint, else the most recent.

    Returns (episode, hint_matched). Reporting a miss rather than silently
    substituting the latest episode lets the agent notice and retry with a real
    title instead of confidently describing the wrong audio.
    """
    if not episode_hint:
        return episodes[0], True

    ranked = sorted(
        episodes, key=lambda e: _score_match(episode_hint, e["title"]), reverse=True
    )
    best = ranked[0]
    if _score_match(episode_hint, best["title"]) == 0:
        return episodes[0], False
    return best, True


# --------------------------------------------------------------------------- #
# Step 3-4: pull a bounded slice of audio into S3
# --------------------------------------------------------------------------- #

def fetch_audio_to_s3(audio_url: str, bucket: str, key: str, minutes: int) -> int:
    """Range-GET the opening `minutes` of an MP3 and store it in S3.

    MP3 is frame-based, so a truncated file is still decodable - Transcribe simply
    transcribes the portion present.
    """
    max_bytes = int(minutes * KB_PER_MINUTE * 1024)

    resp = requests.get(
        audio_url,
        headers={**HEADERS, "Range": f"bytes=0-{max_bytes - 1}"},
        timeout=90,
        stream=True,
    )
    if resp.status_code not in (200, 206):
        raise ListenError(f"Audio fetch returned HTTP {resp.status_code}")

    chunks = []
    total = 0
    for chunk in resp.iter_content(chunk_size=256 * 1024):
        if not chunk:
            continue
        chunks.append(chunk)
        total += len(chunk)
        if total >= max_bytes:
            break

    if total == 0:
        raise ListenError("Audio fetch returned no data")

    boto3.client("s3").put_object(
        Bucket=bucket,
        Key=key,
        Body=b"".join(chunks),
        ContentType="audio/mpeg",
    )
    log.info("stored %d bytes of audio at s3://%s/%s", total, bucket, key)
    return total


# --------------------------------------------------------------------------- #
# Step 5-6: Amazon Transcribe
# --------------------------------------------------------------------------- #

def transcribe_s3_audio(bucket: str, key: str, region: str = "us-east-1") -> dict:
    """Run an Amazon Transcribe job over an S3 audio object and return the result."""
    transcribe = boto3.client("transcribe", region_name=region)
    job_name = f"spf-listen-{uuid.uuid4().hex[:12]}"
    output_key = f"transcripts/{job_name}.json"

    transcribe.start_transcription_job(
        TranscriptionJobName=job_name,
        Media={"MediaFileUri": f"s3://{bucket}/{key}"},
        MediaFormat="mp3",
        IdentifyLanguage=True,
        LanguageOptions=["en-US", "en-GB", "en-IN", "hi-IN"],
        OutputBucketName=bucket,
        OutputKey=output_key,
    )
    log.info("started Transcribe job %s", job_name)

    deadline = time.time() + TRANSCRIBE_TIMEOUT_SECONDS
    status = "IN_PROGRESS"
    while time.time() < deadline:
        job = transcribe.get_transcription_job(TranscriptionJobName=job_name)
        info = job["TranscriptionJob"]
        status = info["TranscriptionJobStatus"]
        if status in ("COMPLETED", "FAILED"):
            break
        time.sleep(TRANSCRIBE_POLL_SECONDS)

    if status != "COMPLETED":
        reason = info.get("FailureReason", f"status={status}")
        raise ListenError(f"Transcribe job did not complete: {reason}")

    body = boto3.client("s3").get_object(Bucket=bucket, Key=output_key)["Body"].read()
    data = json.loads(body)

    results = data.get("results", {})
    transcript_text = (results.get("transcripts") or [{}])[0].get("transcript", "")

    # Keep every segment: this is the verbatim record the UI displays.
    segments = []
    for item in results.get("audio_segments") or []:
        start = float(item.get("start_time", 0))
        segments.append({
            "timestamp": f"{int(start // 60)}:{int(start % 60):02d}",
            "text": item.get("transcript", ""),
        })

    return {
        "job_name": job_name,
        "language": info.get("LanguageCode", "unknown"),
        "transcript": transcript_text,
        "segments": segments,
    }


# --------------------------------------------------------------------------- #
# The tool entry point
# --------------------------------------------------------------------------- #

def listen_to_episode(
    show_name: str,
    episode_hint: str = "",
    minutes: int = DEFAULT_MINUTES,
    bucket: str = "spf-jobs-414691912352",
    region: str = "us-east-1",
) -> dict:
    """Listen to a podcast episode and return what was actually said.

    Finds the show's RSS feed, downloads the opening minutes of the episode audio,
    and runs Amazon Transcribe over it.
    """
    minutes = max(1, min(int(minutes), MAX_MINUTES))

    show = find_feed_url(show_name)
    episodes = list_episodes(show["feed_url"])
    episode, hint_matched = pick_episode(episodes, episode_hint)

    # Some podcast CDNs reject automated range requests for particular items. Try the
    # chosen episode first, then fall back to the next couple so one blocked file
    # does not make the whole show unlistenable.
    attempts = [episode] + [e for e in episodes[:3] if e is not episode]
    key = f"audio/{uuid.uuid4().hex[:12]}.mp3"
    audio_bytes = None
    errors = []

    for candidate in attempts:
        try:
            audio_bytes = fetch_audio_to_s3(candidate["audio_url"], bucket, key, minutes)
            if candidate is not episode:
                log.warning(
                    "falling back from %r to %r after fetch failure",
                    episode["title"][:50], candidate["title"][:50],
                )
                episode = candidate
                hint_matched = False
            break
        except (ListenError, requests.RequestException) as e:
            errors.append(f"{candidate['title'][:40]}: {e}")

    if audio_bytes is None:
        raise ListenError(
            f"Could not download audio for any recent episode of '{show['show']}'. "
            f"Tried {len(attempts)}. Details: {'; '.join(errors[:3])}"
        )

    result = transcribe_s3_audio(bucket, key, region)

    return {
        "show": show["show"],
        "show_resolved_from": show_name,
        "other_shows_matched": show.get("other_matches", []),
        "episode_title": episode["title"],
        "hint_matched": hint_matched,
        "available_episodes": [e["title"] for e in episodes[:12]],
        "published": episode["published"],
        "audio_url": episode["audio_url"],
        "audio_bytes_fetched": audio_bytes,
        "minutes_requested": minutes,
        "language_detected": result["language"],
        "transcript": result["transcript"],
        "segments": result["segments"],
        "word_count": len(result["transcript"].split()),
        "source": "amazon-transcribe",
    }
