# -*- coding: utf-8 -*-
"""
bot-i bridge — Agent A integration sidecar.

This file does NOT modify server.py. It imports the existing FastAPI `app`
from server.py and attaches one new route (`POST /run`) to it. Everything
else — /upload-data, /ask, /chat, the PyRevit flow — keeps working exactly
as before, because they're registered on the same `app` object.

To deploy: change the Railway Procfile to launch uvicorn against this module
instead of server.py. See §3.4 of the spec.
"""
from datetime import date, datetime
import json as _json
import logging
import os as _os
import time as _time
import traceback as _traceback
import uuid as _uuid
from fastapi import Request

# Import the existing app and re-export it so uvicorn can find it via `bridge:app`.
# Importing `server` also runs server.py top-to-bottom — same as today — so all
# existing routes get registered before we add ours.
import server
from server import app  # the same FastAPI instance the existing endpoints use


# --------------------------------------------------------------------------- #
# Logging — dedicated /run channel so we can debug Agent A integration without
# being drowned in the existing server logs. Writes to both stdout (visible in
# Railway logs) and a local file (handy when running locally).
# --------------------------------------------------------------------------- #

_LOG = logging.getLogger("agent_i.bridge")
if not _LOG.handlers:
    _LOG.setLevel(logging.DEBUG)
    _fmt = logging.Formatter("[%(asctime)s] [%(levelname)s] [bridge] %(message)s")
    _stream = logging.StreamHandler()
    _stream.setFormatter(_fmt)
    _LOG.addHandler(_stream)
    # File handler — best-effort; if the cwd is read-only (some Railway setups)
    # we silently skip it so logging never breaks the request path.
    try:
        _log_path = _os.environ.get("AGENT_I_BRIDGE_LOG", "agent_i_bridge.log")
        _fh = logging.FileHandler(_log_path, encoding="utf-8")
        _fh.setFormatter(_fmt)
        _LOG.addHandler(_fh)
    except Exception:
        pass
    _LOG.propagate = False


def _summarise(value, max_len=400):
    """Stringify a request/response body for log lines without flooding."""
    try:
        s = _json.dumps(value, default=str)
    except Exception:
        s = str(value)
    if len(s) > max_len:
        return s[:max_len] + "...(+{} chars)".format(len(s) - max_len)
    return s


# --------------------------------------------------------------------------- #
# Action handlers
# --------------------------------------------------------------------------- #

def _action_query_revit_data(body):
    """
    Natural-language query over the cloud-stored Revit data. Routes through
    the existing Claude agent loop — same one /ask uses internally.
    """
    query = (body.get("query") or "").strip()
    if not query:
        return {"status": "error", "reason": "missing_query",
                "message": "Provide a non-empty 'query' field."}

    if not getattr(server, "_revit_data", None):
        return {"status": "error", "reason": "no_data",
                "message": "No Revit data has been uploaded yet. Click the "
                           "PyRevit 'Export to Notion' button first."}

    reply = _call_existing_ask_loop(query)
    return {"status": "success", "reply": reply, "data": None}


def _action_push_to_notion(body):
    """
    Push filtered Revit elements to Notion. Backgrounded — returns a job_id.
    """
    mode = body.get("mode") or ""
    _LOG.info("push_to_notion: mode=%r category=%r keyword=%r level=%r",
              mode, body.get("category"), body.get("keyword"), body.get("level"))
    if mode not in ("new_database", "versioned"):
        _LOG.warning("push_to_notion: bad mode %r", mode)
        return {"status": "error", "reason": "bad_mode",
                "message": "mode must be 'new_database' or 'versioned'."}

    has_data = bool(getattr(server, "_revit_data", None))
    _LOG.info("push_to_notion: server._revit_data present=%s", has_data)
    if not has_data:
        _LOG.warning("push_to_notion: no Revit data uploaded yet")
        return {"status": "error", "reason": "no_data",
                "message": "No Revit data has been uploaded yet. Click the "
                           "PyRevit 'Export to Notion' button first."}

    elements = _call_existing_filter(
        category=body.get("category"),
        keyword=body.get("keyword"),
        level=body.get("level"),
    )
    _LOG.info("push_to_notion: filter matched %d elements", len(elements) if elements else 0)
    if not elements:
        return {"status": "error", "reason": "no_elements",
                "message": "Filter matched zero elements; nothing to push."}

    preview = {
        "count": len(elements),
        "samples": [e.get("name", "") for e in elements[:5]],
    }

    if mode == "new_database":
        title = body.get("title") or _default_db_title(body)
        _LOG.info("push_to_notion: starting new_database push, title=%r", title)
        job_id = _call_existing_push_new_database(elements, title=title)
    else:
        label = body.get("version_label") or ""
        _LOG.info("push_to_notion: starting versioned push, label=%r", label)
        job_id = _call_existing_push_versioned(elements, label)

    _LOG.info("push_to_notion: job_id=%s queued", job_id)
    return {"status": "success", "job_id": job_id, "mode": mode, "preview": preview}


def _action_compare_snapshots(body):
    """
    Diff the two most recent uploaded snapshots. Optionally push the diff to
    Notion as a change report.
    """
    result = _call_existing_compare()
    if result is None:
        return {"status": "error", "reason": "not_enough_snapshots",
                "message": "Need at least 2 snapshots to compare. Upload again."}

    out = {
        "status": "success",
        "added":    result.get("added", []),
        "deleted":  result.get("deleted", []),
        "modified": result.get("modified", []),
        "totals":   {
            "added":    len(result.get("added", [])),
            "deleted":  len(result.get("deleted", [])),
            "modified": len(result.get("modified", [])),
        },
    }
    if body.get("push_report"):
        out["report_job_id"] = _call_existing_push_change_report(result)
    return out


def _action_check_job_status(body):
    """
    Poll a background job started by push_to_notion or compare_snapshots.
    """
    job_id = body.get("job_id") or ""
    if not job_id:
        return {"status": "error", "reason": "missing_job_id",
                "message": "Provide a 'job_id' field."}

    jobs = getattr(server, "_jobs", {}) or {}
    job = jobs.get(job_id)
    if job is None:
        return {"status": "error", "reason": "job_not_found",
                "message": "No job with id '{}'.".format(job_id)}
    return {"status": "success", "job": job}


# --------------------------------------------------------------------------- #
# Adapter shims — thin wrappers over server.py's existing surface.
# --------------------------------------------------------------------------- #

def _call_existing_ask_loop(query):
    """Drive server.py's Claude agent loop for one user message; return reply text."""
    resp = server.ask(server.ChatRequest(message=query, history=[]))
    return resp.get("reply", "")


def _call_existing_filter(category=None, keyword=None, level=None):
    """Apply category/keyword/level filter against server._revit_data."""
    return server.get_filtered_elements(category=category, keyword=keyword, level=level)


def _default_db_title(body):
    parts = []
    if body.get("category"):
        parts.append(str(body["category"]))
    if body.get("level"):
        parts.append(str(body["level"]))
    if body.get("keyword"):
        parts.append(str(body["keyword"]))
    suffix = " - ".join(parts) if parts else "Export"
    return "Revit Export - {} ({})".format(suffix, date.today())


def _call_existing_push_new_database(elements, title):
    """Kick off the new-database push; return job_id."""
    # Mirrors run_tool("create_new_database", ...) in server.py — same pattern,
    # but we already have the filtered elements so we skip the filter step.
    from datetime import datetime
    import threading
    job_id = "job_{}_{}".format(
        datetime.utcnow().strftime("%Y%m%d_%H%M%S"), len(server._jobs)
    )
    server._jobs[job_id] = {"status": "starting", "message": "Starting push..."}
    t = threading.Thread(
        target=server.bg_create_new_database,
        args=(job_id, title, elements),
    )
    t.daemon = True
    t.start()
    return job_id


def _call_existing_push_versioned(elements, label):
    """Kick off the versioned push; return job_id."""
    # Mirrors run_tool("push_versioned", ...) in server.py. The existing
    # bg_push_versioned does not accept a label — server.py auto-numbers
    # versions — so `label` is accepted for API parity but not forwarded.
    from datetime import datetime
    import threading
    job_id = "job_{}_{}".format(
        datetime.utcnow().strftime("%Y%m%d_%H%M%S"), len(server._jobs)
    )
    server._jobs[job_id] = {"status": "starting", "message": "Starting push..."}
    t = threading.Thread(
        target=server.bg_push_versioned,
        args=(job_id, elements),
    )
    t.daemon = True
    t.start()
    return job_id


def _call_existing_compare():
    """Diff the last two snapshots; return None if <2 snapshots."""
    snapshots = server.load_snapshots()
    if len(snapshots) < 2:
        return None
    old_snap = snapshots[-2]
    new_snap = snapshots[-1]
    result = server.compare_two_snapshots(old_snap["elements"], new_snap["elements"])
    # Cache for a follow-up push_change_report call, matching server.run_tool's
    # behaviour with its module-level _last_compare dict.
    server._last_compare = {
        "result": result,
        "old_ts": old_snap["timestamp"],
        "new_ts": new_snap["timestamp"],
    }
    return result


def _call_existing_push_change_report(result):
    """Kick off pushing a change-report DB to Notion; return job_id."""
    from datetime import datetime
    import threading
    last = getattr(server, "_last_compare", {}) or {}
    old_ts = last.get("old_ts", "")
    new_ts = last.get("new_ts", "")
    title = "Change Report {}".format(date.today())
    job_id = "job_{}_{}".format(
        datetime.utcnow().strftime("%Y%m%d_%H%M%S"), len(server._jobs)
    )
    server._jobs[job_id] = {"status": "starting", "message": "Starting change report push..."}
    t = threading.Thread(
        target=server.bg_push_change_report,
        args=(job_id, title, result, old_ts, new_ts),
    )
    t.daemon = True
    t.start()
    return job_id


# --------------------------------------------------------------------------- #
# The route — attached to the existing app
# --------------------------------------------------------------------------- #

@app.post("/run")
async def run_action(req: Request):
    """
    Agent A integration endpoint. Accepts {"action": "...", ...} payloads and
    returns a structured JSON result. Always returns HTTP 200 — the caller
    keys off the "status" field, not the HTTP code.
    """
    req_id = _uuid.uuid4().hex[:8]
    started = _time.time()
    client_host = req.client.host if req.client else "?"
    _LOG.info("[req=%s] /run inbound from %s", req_id, client_host)

    try:
        body = await req.json()
        _LOG.info("[req=%s] body parsed: %s", req_id, _summarise(body))
    except Exception as e:
        _LOG.warning("[req=%s] invalid JSON body: %s: %s", req_id, type(e).__name__, e)
        return {"status": "error", "reason": "invalid_json",
                "message": "Request body was not valid JSON."}

    action = (body or {}).get("action") or ""
    _LOG.info("[req=%s] dispatching action=%r", req_id, action)
    try:
        if action == "query_revit_data":
            result = _action_query_revit_data(body)
        elif action == "push_to_notion":
            result = _action_push_to_notion(body)
        elif action == "compare_snapshots":
            result = _action_compare_snapshots(body)
        elif action == "check_job_status":
            result = _action_check_job_status(body)
        else:
            _LOG.warning("[req=%s] unknown action %r", req_id, action)
            return {"status": "error", "reason": "unknown_action",
                    "message": "Unknown action '{}'. Known: query_revit_data, "
                               "push_to_notion, compare_snapshots, "
                               "check_job_status.".format(action)}
        elapsed = _time.time() - started
        _LOG.info("[req=%s] action=%s completed in %.2fs status=%s preview=%s",
                  req_id, action, elapsed,
                  (result or {}).get("status") if isinstance(result, dict) else "?",
                  _summarise(result))
        return result
    except NotImplementedError as e:
        _LOG.warning("[req=%s] action=%s NotImplemented: %s", req_id, action, e)
        return {"status": "error", "reason": "not_implemented", "message": str(e)}
    except Exception as e:
        tb = _traceback.format_exc()
        _LOG.error("[req=%s] action=%s CRASHED after %.2fs: %s: %s\n%s",
                   req_id, action, _time.time() - started, type(e).__name__, e, tb)
        # Include the traceback in the response so Agent A's chat surfaces it
        # without needing access to Railway's log stream. Truncated to keep the
        # payload sane.
        return {"status": "error", "reason": "internal_error",
                "message": "{}: {}".format(type(e).__name__, e),
                "trace": tb[-2000:],
                "req_id": req_id}
