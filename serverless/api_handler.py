"""API Lambda behind a Function URL (payload format 2.0).

Replaces app/routes.py. Hand-rolled routing rather than FastAPI + Mangum to keep
the deployment package small and pure-Python; the JSON contracts are identical so
static/index.html needs no changes.

Failure handling detail: the UI does `if (!res.ok) throw` when polling, so a
failed job must return a non-2xx status. Returning 200 with phase='failed' would
leave the UI spinning until MAX_POLLS.
"""
import base64
import json
import logging
import re
import uuid

import boto3

from core import jobs
from core.config import settings
from core.redact import redact

logging.getLogger().setLevel(logging.INFO)
log = logging.getLogger(__name__)

_sfn = None

VALID_PROVIDERS = ("youtube", "spotify")


def _sfn_client():
    global _sfn
    if _sfn is None:
        _sfn = boto3.client("stepfunctions")
    return _sfn


def _response(status: int, body, extra_headers=None):
    headers = {
        "Content-Type": "application/json",
        # Mirrors the old CORSMiddleware(allow_origins=["*"]) configuration.
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "*",
        "Access-Control-Allow-Headers": "*",
        "Cache-Control": "no-store",
    }
    if extra_headers:
        headers.update(extra_headers)
    return {
        "statusCode": status,
        "headers": headers,
        "body": json.dumps(body),
    }


def _error(status: int, message: str):
    return _response(status, {"detail": message})


def _parse_body(event) -> dict:
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError("Request body is not valid JSON")
    if not isinstance(data, dict):
        raise ValueError("Request body must be a JSON object")
    return data


# --- handlers ---

def _start_analysis(event):
    try:
        body = _parse_body(event)
    except ValueError as e:
        return _error(400, str(e))

    channel_query = str(body.get("channel_query") or "").strip()
    interests = str(body.get("interests") or "").strip()
    provider = str(body.get("provider") or "youtube")

    if not channel_query:
        return _error(422, "channel_query must not be empty")
    if not interests:
        return _error(422, "interests must not be empty")
    if provider not in VALID_PROVIDERS:
        return _error(422, "provider must be 'youtube' or 'spotify'")

    try:
        max_videos = int(body.get("max_videos", 10))
    except (TypeError, ValueError):
        return _error(422, "max_videos must be an integer")
    if not 3 <= max_videos <= 50:
        return _error(422, "max_videos must be between 3 and 50")

    job_id = f"insights-{uuid.uuid4().hex[:8]}"

    # Write state before starting the execution so the UI's first poll always
    # finds the job.
    jobs.set_state(
        job_id,
        jobs.PHASE_SEARCHING,
        "Starting analysis...",
        channel_query=channel_query,
        interests=interests,
        max_videos=max_videos,
        provider=provider,
        kind="insights",
    )

    _sfn_client().start_execution(
        stateMachineArn=settings.state_machine_arn,
        name=job_id,
        input=json.dumps({"job_id": job_id}),
    )

    return _response(200, {"workflow_id": job_id, "status": "started"})


def _start_deep_dive(event):
    try:
        body = _parse_body(event)
    except ValueError as e:
        return _error(400, str(e))

    video_url = str(body.get("video_url") or "").strip()
    video_title = str(body.get("video_title") or "").strip()
    interests = str(body.get("interests") or "")

    if not video_url:
        return _error(422, "video_url must not be empty")
    if not video_title:
        return _error(422, "video_title must not be empty")

    job_id = f"deepdive-{uuid.uuid4().hex[:8]}"

    jobs.set_state(
        job_id,
        "analyzing",
        f"Analyzing: {video_title[:50]}",
        video_url=video_url,
        video_title=video_title,
        interests=interests,
        kind="deep_dive",
    )

    _sfn_client().start_execution(
        stateMachineArn=settings.deep_dive_state_machine_arn,
        name=job_id,
        input=json.dumps({"job_id": job_id}),
    )

    return _response(200, {"workflow_id": job_id, "status": "started"})


def _get_status(job_id: str):
    state = jobs.get_state(job_id)
    if state is None:
        return _error(404, f"Unknown workflow '{job_id}'")

    phase = state.get("phase", "")
    detail = state.get("detail", "")

    if phase == jobs.PHASE_FAILED:
        # Non-2xx so the UI surfaces the error instead of polling to MAX_POLLS.
        # Redacted on read as well as on write, so older records are safe too.
        return _error(500, redact(state.get("error") or detail or "Workflow failed"))

    return _response(200, {
        "workflow_id": job_id,
        "phase": phase,
        "detail": detail,
    })


def _get_result(job_id: str):
    state = jobs.get_state(job_id)
    if state is None:
        return _error(404, f"Unknown workflow '{job_id}'")

    if state.get("phase") == jobs.PHASE_FAILED:
        return _error(400, redact(state.get("error") or "Workflow failed"))

    result = jobs.get_part(job_id, "result", default=None)
    if result is None:
        return _error(404, "Result is not ready yet")

    return _response(200, result)


def _health():
    ok = bool(settings.jobs_bucket and settings.state_machine_arn)
    return _response(200, {
        "status": "ok" if ok else "degraded",
        "orchestrator": "aws-stepfunctions",
        "jobs_bucket_configured": bool(settings.jobs_bucket),
        "state_machine_configured": bool(settings.state_machine_arn),
    })


# --- routing ---

ROUTES = [
    ("POST", re.compile(r"^/api/analyze/?$"),
     lambda ev, m: _start_analysis(ev)),
    ("POST", re.compile(r"^/api/deep-dive/?$"),
     lambda ev, m: _start_deep_dive(ev)),
    ("GET", re.compile(r"^/api/deep-dive/status/(?P<id>[^/]+)/?$"),
     lambda ev, m: _get_status(m.group("id"))),
    ("GET", re.compile(r"^/api/deep-dive/result/(?P<id>[^/]+)/?$"),
     lambda ev, m: _get_result(m.group("id"))),
    ("GET", re.compile(r"^/api/status/(?P<id>[^/]+)/?$"),
     lambda ev, m: _get_status(m.group("id"))),
    ("GET", re.compile(r"^/api/result/(?P<id>[^/]+)/?$"),
     lambda ev, m: _get_result(m.group("id"))),
    ("GET", re.compile(r"^/health/?$"), lambda ev, m: _health()),
    ("GET", re.compile(r"^/api/health/?$"), lambda ev, m: _health()),
]


def handler(event, context):
    ctx = (event.get("requestContext") or {}).get("http") or {}
    method = (ctx.get("method") or "GET").upper()
    path = event.get("rawPath") or ctx.get("path") or "/"

    log.info("%s %s", method, path)

    if method == "OPTIONS":
        return _response(204, {})

    for route_method, pattern, fn in ROUTES:
        m = pattern.match(path)
        if m:
            if route_method != method:
                return _error(405, f"{method} not allowed on {path}")
            try:
                return fn(event, m)
            except Exception as e:
                log.exception("handler failed for %s %s", method, path)
                # Redacted: upstream exception text can embed API keys from request URLs.
                return _error(500, redact(f"{type(e).__name__}: {e}"))

    return _error(404, f"No route for {method} {path}")
