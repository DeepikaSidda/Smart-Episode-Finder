"""S3-backed job state.

Why S3 rather than DynamoDB: the working set (video metadata plus transcript
excerpts, up to max_videos=50) can exceed both the Step Functions 256 KB state
payload limit and the DynamoDB 400 KB item limit. Keeping the payload in S3 and
passing only a job_id between states sidesteps both.

Concurrency rule: state.json is written ONLY by sequential steps. The two
Parallel branches each write their own part key, so there is no
read-modify-write race between them.

Layout:
    jobs/{job_id}/state.json        phase, detail, provider, input, error
    jobs/{job_id}/search.json       {channel_name, videos[]}
    jobs/{job_id}/interests.json    {keywords[], topics[]}
    jobs/{job_id}/transcripts.json  {url: excerpt}
    jobs/{job_id}/rank.json         [recommendation, ...]
    jobs/{job_id}/summary.json      {summary, key_insights[], tone}
    jobs/{job_id}/result.json       final API payload
"""
import json
import logging
import time

import boto3
from botocore.exceptions import ClientError

from .config import settings

log = logging.getLogger(__name__)

_s3 = None

PHASE_QUEUED = "queued"
PHASE_SEARCHING = "searching"
PHASE_PARSING = "parsing"
PHASE_RANKING = "ranking_summarizing"
PHASE_COMPLETED = "completed"
PHASE_FAILED = "failed"


def _client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def _key(job_id: str, name: str) -> str:
    return f"jobs/{job_id}/{name}.json"


def put_part(job_id: str, name: str, data) -> None:
    """Write one part of a job's working set."""
    _client().put_object(
        Bucket=settings.jobs_bucket,
        Key=_key(job_id, name),
        Body=json.dumps(data).encode("utf-8"),
        ContentType="application/json",
    )


def get_part(job_id: str, name: str, default=None):
    """Read one part of a job's working set, returning default when absent."""
    try:
        obj = _client().get_object(Bucket=settings.jobs_bucket, Key=_key(job_id, name))
        return json.loads(obj["Body"].read())
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            return default
        raise


def set_state(job_id: str, phase: str, detail: str = "", **extra) -> dict:
    """Write the job's status record. Sequential steps only — never from a
    Parallel branch, to avoid clobbering a concurrent write."""
    state = get_part(job_id, "state", default={}) or {}
    state.update({
        "job_id": job_id,
        "phase": phase,
        "detail": detail,
        "updated_at": int(time.time()),
    })
    state.update(extra)
    if "created_at" not in state:
        state["created_at"] = int(time.time())
    put_part(job_id, "state", state)
    log.info("job %s -> %s (%s)", job_id, phase, detail)
    return state


def get_state(job_id: str):
    """Return the job status record, or None when the job does not exist."""
    return get_part(job_id, "state", default=None)
