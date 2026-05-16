# Agent I Bridge — Integration Spec

> Drop this file into the Bot-i repo (suggested path: `docs/bridge_spec.md` at the repo root). It is self-contained — anyone (or any AI agent) reading only this file should be able to implement Agent I's side of the integration **without modifying any existing Bot-i code**.

---

## 1. Why This Exists

Agent A (a pyRevit extension running in CPython 3.12 inside Revit 2026) hosts a Gemini-powered chat window. When a user types something like *"summarise the doors in the cloud model"* or *"push the current model to Notion"*, Agent A's intent classifier needs to delegate that request to Agent I (Bot-i) and render the result back into its own chat window.

Agent A does not (and should not) know anything about Notion, Claude tools, snapshots, or `/ask`. All it knows is: *"there is an HTTPS endpoint at `https://<bot-i-host>/run`, I POST a JSON payload of shape `{action, ...}`, I get a JSON object back."*

Your job in this repo: stand up that `/run` endpoint **as a sidecar file** that imports the existing Bot-i app and attaches a new route to it. **No edits to `server.py` or any existing endpoint.**

---

## 2. Hard Constraints

1. **Do not modify any existing file.** `server.py`, `requirements.txt`, `Procfile`, `script.py` — none of these get touched. The bridge lives in a brand-new file (see §3) and is wired in through a one-line change to the Railway start command (see §3.4) — that's the only configuration touch outside the new file.
2. **Do not touch the existing endpoints.** `/upload-data`, `/ask`, `/chat`, `/get-data`, `/get-summary`, `/job-status/{job_id}` must keep working exactly as today. The PyRevit "Export to Notion" button and the `/chat` web UI must continue to behave identically.
3. **Reuse — don't refactor.** The bridge calls into Bot-i's existing functions and reads its existing module-level state (`_revit_data`, `_jobs`, snapshot helpers, the Claude agent loop, the Notion push functions). It imports them; it does not move them or wrap them in new abstractions.
4. **Same auth, same hosting, same env vars.** No new auth, same `NOTION_TOKEN` + `ANTHROPIC_API_KEY` Railway env vars, same Railway deployment. No new process, no new port.
5. **One request, one response.** v1 returns a single JSON blob when the operation finishes. Streaming is an optional v2 — see §4.3.
6. **Wire protocol is `{action, ...}` — NOT `{message, history}`.** Agent A speaks the same shape it already speaks to Agent D. Conversation history is Agent A's job to maintain; you don't see it.

---

## 3. Deliverable: One New File

Create **one** new file at the repo root:

```
bot-i/
├── server.py             # ← UNCHANGED
├── bridge.py             # ← NEW — the only file you create
├── requirements.txt      # ← UNCHANGED
├── Procfile              # ← updated to point uvicorn at bridge:app (see §3.4)
└── ...
```

`bridge.py` works by **importing** Bot-i's existing FastAPI app and attaching the new `/run` route to it. The existing routes come along for free because they're already registered on `app`. Nothing in `server.py` needs to know `bridge.py` exists.

### 3.1 `bridge.py` — full template

Copy-paste this file as-is, then fill in the four `TODO` markers where the handlers call into Bot-i's existing functions. **Do not edit `server.py` to make these imports work** — if a name you need isn't already module-level in `server.py`, either reference it via `server.<name>` (most Python attributes are accessible even if "internal") or extract it inside the handler from whatever public path already exposes it.

```python
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
from fastapi import Request

# Import the existing app and re-export it so uvicorn can find it via `bridge:app`.
# Importing `server` also runs server.py top-to-bottom — same as today — so all
# existing routes get registered before we add ours.
import server
from server import app  # the same FastAPI instance the existing endpoints use


# --------------------------------------------------------------------------- #
# Action handlers
# --------------------------------------------------------------------------- #
# Each handler is a thin wrapper over logic that already lives in server.py.
# We reach into server's module-level state via the `server` import so we never
# have to edit server.py to expose anything.

def _action_query_revit_data(body):
    """
    Natural-language query over the cloud-stored Revit data. Routes through
    the existing Claude agent loop — same one /ask uses internally.

    Input:
        query: string (required) — the natural-language question

    Output:
        { "status": "success", "reply": "...", "data": {...}|null }
    """
    query = (body.get("query") or "").strip()
    if not query:
        return {"status": "error", "reason": "missing_query",
                "message": "Provide a non-empty 'query' field."}

    if not getattr(server, "_revit_data", None):
        return {"status": "error", "reason": "no_data",
                "message": "No Revit data has been uploaded yet. Click the "
                           "PyRevit 'Export to Notion' button first."}

    # TODO 1: call whatever function in server.py drives the /ask Claude loop.
    # If today the loop is inlined in the /ask handler, the smallest possible
    # change is: call the /ask handler's underlying function directly from
    # here. If that means *no* server.py edit is possible, fall back to:
    #
    #     import asyncio
    #     resp = asyncio.run(server.ask(server.AskRequest(message=query, history=[])))
    #     reply = resp["reply"]
    #
    # using whatever the actual route handler + request model are named in
    # server.py. The point: we call into server's existing surface; we do not
    # duplicate its Claude loop here.
    reply = _call_existing_ask_loop(query)
    return {"status": "success", "reply": reply, "data": None}


def _action_push_to_notion(body):
    """
    Push filtered Revit elements to Notion. Backgrounded — returns a job_id.

    Input:
        mode:          "new_database" | "versioned"   (required)
        category:      string (optional)
        keyword:       string (optional)
        level:         string (optional)
        version_label: string (optional, mode=versioned only)

    Output:
        { "status": "success", "job_id": "...", "mode": "...",
          "preview": { "count": N, "samples": [...] } }
    """
    mode = body.get("mode") or ""
    if mode not in ("new_database", "versioned"):
        return {"status": "error", "reason": "bad_mode",
                "message": "mode must be 'new_database' or 'versioned'."}

    # TODO 2: reuse server.py's existing filter helper and one of the two
    # existing push starters. Whatever the names are in server.py — e.g.
    # server.filter_elements(...), server.start_push_new_database(...),
    # server.start_push_versioned(...) — call them through the `server`
    # module. Do NOT reimplement the filtering or the Notion push.
    elements = _call_existing_filter(
        category=body.get("category"),
        keyword=body.get("keyword"),
        level=body.get("level"),
    )
    if not elements:
        return {"status": "error", "reason": "no_elements",
                "message": "Filter matched zero elements; nothing to push."}

    preview = {
        "count": len(elements),
        "samples": [e.get("name", "") for e in elements[:5]],
    }

    if mode == "new_database":
        job_id = _call_existing_push_new_database(elements)
    else:
        job_id = _call_existing_push_versioned(elements, body.get("version_label") or "")

    return {"status": "success", "job_id": job_id, "mode": mode, "preview": preview}


def _action_compare_snapshots(body):
    """
    Diff the two most recent uploaded snapshots. Optionally push the diff to
    Notion as a change report.

    Input:
        push_report: bool (optional, default false)

    Output:
        { "status": "success",
          "added": [...], "deleted": [...], "modified": [...],
          "totals": {"added": N, "deleted": N, "modified": N},
          "report_job_id": "..."  # only when push_report=true
        }
    """
    # TODO 3: reuse server.py's existing snapshot-compare function (the same
    # one its Claude tool `compare_snapshots` already calls). Returns None or
    # the diff dict.
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
        # TODO 4: reuse server.py's existing push_change_report starter.
        out["report_job_id"] = _call_existing_push_change_report(result)
    return out


def _action_check_job_status(body):
    """
    Poll a background job started by push_to_notion or compare_snapshots.

    Input:
        job_id: string (required)

    Output:
        { "status": "success", "job": {"status": "...", "message": "..."} }
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
# Adapter shims — replace each body with a call into server.py's existing fn.
# Keeping them as one-liners here makes it obvious where the integration
# touches server's surface area, and makes it easy to update if server.py's
# internal function names ever change.
# --------------------------------------------------------------------------- #

def _call_existing_ask_loop(query):
    """Drive server.py's Claude agent loop for one user message; return reply text."""
    # Example shapes — pick the one that matches server.py's actual structure:
    #   return server.run_claude_turn(query, history=[])
    #   return server.ask_internal(message=query, history=[])
    # If only the route handler exists today:
    #   import asyncio
    #   resp = asyncio.run(server.ask(server.AskRequest(message=query, history=[])))
    #   return resp["reply"]
    raise NotImplementedError("Wire this to server.py's Claude turn function.")


def _call_existing_filter(category=None, keyword=None, level=None):
    """Apply category/keyword/level filter against server._revit_data."""
    #   return server.filter_elements(category=category, keyword=keyword, level=level)
    raise NotImplementedError("Wire this to server.py's filter helper.")


def _call_existing_push_new_database(elements):
    """Kick off the new-database push; return job_id."""
    #   return server.start_push_new_database(elements)
    raise NotImplementedError("Wire this to server.py's new-database push starter.")


def _call_existing_push_versioned(elements, label):
    """Kick off the versioned push; return job_id."""
    #   return server.start_push_versioned(elements, label=label)
    raise NotImplementedError("Wire this to server.py's versioned push starter.")


def _call_existing_compare():
    """Diff the last two snapshots; return None if <2 snapshots."""
    #   return server.compare_last_two_snapshots()
    raise NotImplementedError("Wire this to server.py's snapshot comparator.")


def _call_existing_push_change_report(result):
    """Kick off pushing a change-report DB to Notion; return job_id."""
    #   return server.start_push_change_report(result)
    raise NotImplementedError("Wire this to server.py's change-report push starter.")


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
    try:
        body = await req.json()
    except Exception:
        return {"status": "error", "reason": "invalid_json",
                "message": "Request body was not valid JSON."}

    action = (body or {}).get("action") or ""
    try:
        if action == "query_revit_data":
            return _action_query_revit_data(body)
        if action == "push_to_notion":
            return _action_push_to_notion(body)
        if action == "compare_snapshots":
            return _action_compare_snapshots(body)
        if action == "check_job_status":
            return _action_check_job_status(body)
        return {"status": "error", "reason": "unknown_action",
                "message": "Unknown action '{}'. Known: query_revit_data, "
                           "push_to_notion, compare_snapshots, "
                           "check_job_status.".format(action)}
    except NotImplementedError as e:
        return {"status": "error", "reason": "not_implemented", "message": str(e)}
    except Exception as e:
        return {"status": "error", "reason": "internal_error",
                "message": "{}: {}".format(type(e).__name__, e)}
```

### 3.2 Why this works without touching `server.py`

- FastAPI's `@app.post(...)` mutates the existing `app` object. Importing `server` runs every existing `@app.post / @app.get` decorator (because they're all module-level), so by the time `bridge.py` reaches `@app.post("/run")`, all of Bot-i's existing routes are already registered. Adding one more route is a no-op for the others.
- Python's module system lets `bridge.py` read `server._revit_data`, `server._jobs`, `server.filter_elements`, etc. — even if those names were never explicitly meant as a public API. They're already importable; we're just being honest about which ones we depend on.
- If a function you need genuinely isn't extractable (e.g. the Claude loop is buried inside the `/ask` route handler), the fallback documented in TODO 1 — call the FastAPI route handler directly via `asyncio.run(server.ask(...))` — works without any source edit.

### 3.3 What if some bit of `server.py` really can't be reused without a refactor?

If TODO 1 / 2 / 3 / 4 prove genuinely unreachable from outside `server.py` (e.g. the Claude loop is built inside the request handler in a way that can't be called twice), the spec-compliant move is to **copy the small fragment you need into `bridge.py`** rather than refactor `server.py`. Duplication of ~30 lines is preferable to changing the existing file. Document the copy with a comment like `# Mirrors the loop in server.ask — keep in sync if that one changes.`

### 3.4 Procfile / start command

Currently the Railway Procfile reads:

```
web: uvicorn server:app --host 0.0.0.0 --port $PORT
```

Change it to:

```
web: uvicorn bridge:app --host 0.0.0.0 --port $PORT
```

This is the **only configuration change** outside `bridge.py`. `bridge:app` is literally the same `app` object as `server:app` — just reached via the module that also added `/run`. Every existing endpoint still works on the same URLs.

(If for some reason you can't change the Procfile, a one-line workaround is to add `import bridge` to the very bottom of `server.py` — but that *would* count as editing `server.py`, so prefer the Procfile route.)

---

## 4. Wire Protocol — Exact Contract With Agent A

### 4.1 Request

```http
POST /run HTTP/1.1
Host: problem-statement-2-production.up.railway.app
Content-Type: application/json

{
  "action": "query_revit_data",
  "query":  "How many doors on each level?"
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `action` | string | yes | One of `query_revit_data`, `push_to_notion`, `compare_snapshots`, `check_job_status`. Anything else → `{"status":"error","reason":"unknown_action"}`. |
| (per-action fields) | — | — | See §3.1 docstrings for each action's input shape. |

### 4.2 Response — v1 (plain JSON)

```http
HTTP/1.1 200 OK
Content-Type: application/json

{ "status": "success", ... }
```

One JSON object written when the operation completes. Agent A shows a spinner during the request and renders the final payload as markdown.

### 4.3 Response — v2 (streaming, optional upgrade)

If `push_to_notion` for a large filter takes long enough that the user wonders what's happening, switch the response to `text/event-stream`:

```
HTTP/1.1 200 OK
Content-Type: text/event-stream
Cache-Control: no-cache

data: {"type":"status","text":"Filtering elements..."}

data: {"type":"status","text":"Creating Notion database..."}

data: {"type":"status","text":"Pushing 50 / 366 records..."}

data: {"type":"result","payload":{"status":"success","job_id":"abc","preview":{...}}}

```

Rules (identical to Agent D):
- Each message is a single line `data: <json>\n\n` (blank line terminator).
- `{"type":"status","text":"..."}` updates the chat spinner. Prefix `text` with `\x00STATUS\x00` if you want it treated as transient (replace in place) rather than appended.
- Exactly one `{"type":"result","payload":...}` per request, sent last, then close the connection.

Ship v1 first. Most calls finish in <5s — only worry about streaming if `push_to_notion` UX feels slow.

### 4.4 Error responses

Always return **HTTP 200** with a JSON body — never 4xx/5xx. Agent A keys off the `status` field. Status enum:

| `status` | When |
|---|---|
| `success` | Operation completed. |
| `error` | Hard failure. Always include `reason` (machine-readable enum) and `message` (human string). |

Reason enum (extend as needed): `invalid_json`, `unknown_action`, `missing_query`, `no_data`, `bad_mode`, `no_elements`, `not_enough_snapshots`, `missing_job_id`, `job_not_found`, `notion_api_error`, `claude_api_error`, `not_implemented`, `internal_error`.

---

## 5. CORS / Network

No CORS preflight expected — Agent A is a Python `httpx` client, not a browser. Existing CORS config stays as-is.

The Railway URL is the public URL Agent A already has in its `external_agents.json`. If you ever change the Railway hostname, tell whoever owns Agent A to update one line in that file.

---

## 6. Health Check

Agent A probes the URL at chat-window startup with a `HEAD /run` call to decide whether to show a "Bot-i: offline" hint. FastAPI returns its default `405 Method Not Allowed` for `HEAD /run` — Agent A treats *any* response (including 405) as "alive." No action needed.

---

## 7. Smoke Test (no Agent A required)

You can validate the bridge before Agent A is ready:

1. Deploy with the new `bridge.py` and updated Procfile.
2. From your local terminal:

   ```powershell
   $body = '{"action":"query_revit_data","query":"how many doors total"}'
   Invoke-RestMethod -Uri https://problem-statement-2-production.up.railway.app/run `
       -Method Post -Body $body -ContentType 'application/json'
   ```

3. Expected: `{ "status": "success", "reply": "...", "data": ... }`.
4. Try `{"action":"banana"}` → expect `{"status":"error","reason":"unknown_action", ...}`.
5. Try `push_to_notion` with `mode=new_database` and no upload yet → expect `{"status":"error","reason":"no_data"}`.
6. After a PyRevit upload, try `compare_snapshots` with only 1 snapshot → expect `{"status":"error","reason":"not_enough_snapshots"}`.
7. With 2+ snapshots → expect `{"status":"success","added":[...], "totals":{...}}`.
8. **Critical regression check:** confirm `/ask`, `/chat`, `/upload-data`, `/get-data`, `/get-summary`, `/job-status/...` all still work exactly as before — `/run` is purely additive, and switching the Procfile from `server:app` to `bridge:app` must change nothing about their behaviour.

If all 8 pass, Agent I's side is done.

---

## 8. Things NOT To Do

- **Don't edit `server.py`.** That's the whole point of this spec. If you find yourself wanting to, pause and re-read §3.3 — copying ~30 lines into `bridge.py` is preferable to a refactor.
- **Don't move state.** `_revit_data`, `_jobs`, `_last_compare`, snapshot files all stay where they are in `server.py`.
- **Don't add new env vars or new auth.** Same trust model as today.
- **Don't break existing endpoints.** The PyRevit button and the `/chat` web UI must keep working. The Procfile switch from `server:app` to `bridge:app` is the only deployment change.
- **Don't require Agent A to send `history`** — Agent A keeps its own history. You only see one self-contained `query` per call.
- **Don't return HTTP 4xx/5xx for business errors** — always 200 + `{status:"error",reason:"...",message:"..."}`. The HTTP code is reserved for transport failures (network drop, Railway 502).
- **Don't invent new field names.** `query`, `mode`, `job_id`, `push_report` are the names Agent A's classifier has been prompted with. Renaming them silently breaks the integration.
- **Don't change the action names** (`query_revit_data`, `push_to_notion`, `compare_snapshots`, `check_job_status`) — they're hard-coded in Agent A's `external_agents.json`. Adding new actions is fine; renaming existing ones is not.

---

## 9. Versioning / Future-Proofing

Agent A reads `external_agents.json` listing the URL and supported actions. If you ever:

- Change the Railway hostname → one-line edit in Agent A's `external_agents.json`.
- Add a new action → document its request/response shape here, add a new `_action_<name>` function in `bridge.py`, append it to the `if action == ...` chain. Then ask Agent A's owner to append it to the registry's `actions` list (no Agent A code changes needed — the classifier picks it up).
- Want to deprecate an action → keep it routing for at least one release; coordinate the cutover.

---

## 10. Quick Reference Card

| Thing | Value |
|---|---|
| Endpoint URL | `https://problem-statement-2-production.up.railway.app/run` |
| Method | `POST` |
| Request content-type | `application/json` |
| Response content-type | `application/json` (v1) or `text/event-stream` (v2) |
| Actions | `query_revit_data`, `push_to_notion`, `compare_snapshots`, `check_job_status` |
| HTTP status | Always 200 (errors go in body via `status:"error"`) |
| Auth | None (same as existing endpoints) |
| Hosting | Same Railway deployment as today |
| **Files created** | `bridge.py` (new) |
| **Files edited** | `Procfile` (one line: `server:app` → `bridge:app`) |
| **Files untouched** | `server.py`, `requirements.txt`, `script.py`, every existing endpoint |
| Where the route lives | `@app.post("/run")` inside `bridge.py`, attached to the imported `app` |
| What Agent A expects on first failed call | `{"status":"error","reason":"...","message":"..."}` — never a stack trace, never a 500 |
