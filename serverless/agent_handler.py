"""Lambda handler that runs the Strands agent on AWS.

Why asynchronous: one `listen_to_episode` call starts an Amazon Transcribe job that
takes 30-60 seconds, while API Gateway caps a response at 30 seconds. So the HTTP
request only enqueues work:

    POST /api/agent                  -> writes a job record, self-invokes
                                        asynchronously, returns a job_id immediately
    GET  /api/agent/status/{job_id}  -> reads the job record from S3

The same function serves both the HTTP request and the background run; the async
invocation is distinguished by `mode: "run"` in the event. One function, no extra
queue, and it reuses the S3 job store the rest of the app already uses.
"""
import json
import logging
import os
import uuid

import boto3

from agent.podcast_agent import ask
from core import jobs
from core.redact import redact

logging.getLogger().setLevel(logging.INFO)
log = logging.getLogger(__name__)

SELF_FUNCTION_NAME = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "spf-agent")

_lambda = None


def _lambda_client():
    global _lambda
    if _lambda is None:
        _lambda = boto3.client("lambda")
    return _lambda


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "no-store",
        },
        "body": json.dumps(body),
    }


# --------------------------------------------------------------------------- #
# background run
# --------------------------------------------------------------------------- #

def _run_job(job_id: str, question: str, with_ears: bool) -> dict:
    """Execute the agent and persist the outcome."""
    try:
        jobs.set_state(job_id, "thinking", "Agent is choosing tools...")
        result = ask(question, with_ears=with_ears)

        jobs.put_part(job_id, "result", {
            "question": question,
            "with_ears": with_ears,
            "answer": result["answer"],
            "tools_available": result["tools_available"],
            "tools_used": [t["tool"] for t in result["tools_used"]],
            "tool_calls": result["tools_used"],
            "transcript": result.get("transcript"),
            "model": result["model"],
        })
        tools = [t["tool"] for t in result["tools_used"]] or ["none"]
        jobs.set_state(job_id, jobs.PHASE_COMPLETED, f"Used: {', '.join(tools)}")
        return {"job_id": job_id, "ok": True}

    except Exception as e:
        safe = redact(f"{type(e).__name__}: {e}")[:400]
        log.exception("agent job %s failed", job_id)
        jobs.set_state(job_id, jobs.PHASE_FAILED, safe, error=safe)
        return {"job_id": job_id, "ok": False, "error": safe}


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

def _start(event):
    raw = event.get("body") or "{}"
    try:
        body = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return _response(400, {"detail": "Body is not valid JSON"})

    # Two ways in: a free-form question, or a show + episode the user picked from
    # the list. The picker is the primary UI path, so build the question for it.
    question = str(body.get("question") or "").strip()
    show_name = str(body.get("show_name") or "").strip()
    episode_title = str(body.get("episode_title") or "").strip()

    try:
        minutes = int(body.get("minutes", 10))
    except (TypeError, ValueError):
        minutes = 10
    minutes = max(1, min(minutes, 45))

    if not question:
        if not (show_name and episode_title):
            return _response(
                422,
                {"detail": "Provide either 'question', or both 'show_name' and 'episode_title'"},
            )
        question = (
            f"Listen to the first {minutes} minutes of the episode titled "
            f"\"{episode_title}\" from the podcast \"{show_name}\" by calling "
            f"listen_to_episode with minutes={minutes}. Then, using only what is "
            "actually said in the transcript, tell me: what this episode covers, the "
            "specific topics, products, tools and names mentioned, and two or three "
            "direct quotes with their timestamps. Be concrete and never speculate "
            "beyond the transcript."
        )

    # Default True; pass with_ears=false to reproduce the pre-tool behaviour.
    with_ears = bool(body.get("with_ears", True))

    job_id = f"agent-{uuid.uuid4().hex[:8]}"
    jobs.set_state(
        job_id, "queued", "Queued",
        question=question, with_ears=with_ears, minutes=minutes, kind="agent",
    )

    _lambda_client().invoke(
        FunctionName=SELF_FUNCTION_NAME,
        InvocationType="Event",
        Payload=json.dumps({
            "mode": "run",
            "job_id": job_id,
            "question": question,
            "with_ears": with_ears,
        }).encode(),
    )

    return _response(200, {
        "job_id": job_id,
        "status": "started",
        "with_ears": with_ears,
        "minutes": minutes,
    })


def _episodes(show_query: str):
    """List a show's recent episodes so the user can pick one.

    Synchronous and cheap: this only resolves the RSS feed and parses it. No audio
    is downloaded and Amazon Transcribe is not involved, so it returns well inside
    API Gateway's 30 second limit.
    """
    from agent.listen_tool import ListenError, find_feed_url, list_episodes

    show_query = (show_query or "").strip()
    if not show_query:
        return _response(422, {"detail": "Provide a show name"})

    try:
        show = find_feed_url(show_query)
        episodes = list_episodes(show["feed_url"], limit=15)
    except ListenError as e:
        return _response(404, {"detail": redact(str(e))})
    except Exception as e:
        return _response(502, {"detail": redact(f"{type(e).__name__}: {e}")})

    return _response(200, {
        "show": show["show"],
        "genre": show.get("genre", ""),
        "artwork": show.get("artwork", ""),
        "episode_count": show.get("episode_count", 0),
        "other_matches": show.get("other_matches", []),
        "episodes": [
            {"title": e["title"], "published": e.get("published", "")}
            for e in episodes
        ],
    })


def _status(job_id):
    state = jobs.get_state(job_id)
    if state is None:
        return _response(404, {"detail": f"Unknown job '{job_id}'"})

    phase = state.get("phase", "")
    payload = {
        "job_id": job_id,
        "phase": phase,
        "detail": redact(state.get("detail", "")),
        "question": state.get("question", ""),
        "with_ears": state.get("with_ears", True),
    }

    if phase == jobs.PHASE_FAILED:
        payload["error"] = redact(state.get("error", ""))
        return _response(500, payload)

    if phase == jobs.PHASE_COMPLETED:
        payload["result"] = jobs.get_part(job_id, "result", default={})

    return _response(200, payload)


def handler(event, context):
    # Background invocation from _start().
    if event.get("mode") == "run":
        return _run_job(
            event["job_id"], event["question"], event.get("with_ears", True)
        )

    http = (event.get("requestContext") or {}).get("http") or {}
    method = (http.get("method") or "GET").upper()
    path = event.get("rawPath") or ""
    log.info("%s %s", method, path)

    if method == "OPTIONS":
        return _response(204, {})

    if method == "POST":
        return _start(event)

    if method == "GET":
        if path.rstrip("/").endswith("/episodes"):
            params = event.get("queryStringParameters") or {}
            return _episodes(params.get("show", ""))

        job_id = path.rstrip("/").rsplit("/", 1)[-1]
        if job_id and job_id != "agent":
            return _status(job_id)
        return _response(400, {"detail": "Provide a job id: /api/agent/status/{job_id}"})

    return _response(405, {"detail": f"{method} not allowed"})
