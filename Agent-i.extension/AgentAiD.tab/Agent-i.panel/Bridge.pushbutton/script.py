# -*- coding: utf-8 -*-
"""Agent I local HTTP bridge.

Mirrors Agent D's bridge architecture: a System.Net.HttpListener bound to
127.0.0.1 (preferred port 8201, falls back through 8210) that accepts JSON
POSTs from Agent A on /run and forwards them to the deployed Railway
service.

Currently only the `push_to_notion` action is supported. It:
  1. Collects the current Revit document's elements on the main thread
  2. POSTs the JSON to Railway's /upload-data endpoint
  3. Returns a success payload that points the user at the /chat UI where
     the actual Notion push happens.

The chosen port is written to %TEMP%/agenti_bridge.port so Agent A can
discover it without hard-coding the value.
"""

import os
import sys
import json
import tempfile
import threading
import traceback

import clr
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
clr.AddReference('System')

from Autodesk.Revit.UI import IExternalEventHandler, ExternalEvent

import System
from System.Net import HttpListener, WebClient, WebException
from System.Net.Sockets import TcpListener
from System.Net import IPAddress
from System.Text import Encoding
from System.Threading import Thread, ThreadStart, ManualResetEvent

from pyrevit import script, forms

# Make our own folder importable so we can load agenti_collect
_THIS_DIR = os.path.dirname(__file__)
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

# agenti_collect lives in the sibling Agent-i.pushbutton folder. Add that to
# the path so the import resolves without copying the file.
_SIBLING_DIR = os.path.join(os.path.dirname(_THIS_DIR), "Agent-i.pushbutton")
if _SIBLING_DIR not in sys.path:
    sys.path.insert(0, _SIBLING_DIR)

import agenti_collect

output = script.get_output()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PREFERRED_PORT = 8201
PORT_FALLBACK_MAX = 8210  # inclusive — tries 8201..8210
PORT_FILE = os.path.join(tempfile.gettempdir(), "agenti_bridge.port")

# The deployed Railway service. /upload-data is the only public write endpoint
# the bridge needs — once data is uploaded, the user opens /chat to do the
# actual Notion push using credentials that only live on the server side.
RAILWAY_BASE_URL = "https://problem-statement-2-production.up.railway.app"

# Actual port the listener bound to. Set by start_bridge() on success.
BRIDGE_PORT = None


# ---------------------------------------------------------------------------
# Logging — best-effort file logger so we can see what the bridge is doing
# without keeping a pyRevit output panel open. Path mirrors Agent A's
# convention (%APPDATA%\RevitMCP\logs\) so all three agents log in one place.
# ---------------------------------------------------------------------------

def _log_path():
    base = os.path.join(os.environ.get("APPDATA", tempfile.gettempdir()), "RevitMCP", "logs")
    try:
        if not os.path.isdir(base):
            os.makedirs(base)
    except Exception:
        pass
    return os.path.join(base, "agenti_bridge.log")


_LOG_FILE = _log_path()


def _log(msg):
    line = "[{}] [bridge] {}\n".format(System.DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss"), msg)
    try:
        with open(_LOG_FILE, "a") as f:
            f.write(line)
    except Exception:
        pass
    try:
        print(line.rstrip())
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Main-thread marshalling — Revit's Document API is main-thread-only, so we
# queue work and let an ExternalEvent drain it. Same pattern as Agent D.
# ---------------------------------------------------------------------------

class _MainThreadQueue(object):
    def __init__(self):
        self._lock = threading.Lock()
        self._items = []

    def put(self, item):
        with self._lock:
            self._items.append(item)

    def drain(self):
        with self._lock:
            items = self._items
            self._items = []
        return items


class BridgeEventHandler(IExternalEventHandler):
    def __init__(self, queue):
        self._queue = queue

    def Execute(self, uiapp):
        try:
            doc = uiapp.ActiveUIDocument.Document if uiapp.ActiveUIDocument else None
        except Exception:
            doc = None

        for item in self._queue.drain():
            fn, args, result_holder, done_event = item
            try:
                if doc is None:
                    result_holder["data"] = {
                        "status": "error",
                        "reason": "no_active_document",
                        "message": "No active Revit document.",
                    }
                else:
                    result_holder["data"] = fn(doc, *args)
            except Exception as e:
                result_holder["data"] = {
                    "status": "error",
                    "reason": "internal_error",
                    "message": str(e),
                    "trace": traceback.format_exc(),
                }
            try:
                done_event.Set()
            except Exception:
                pass

    def GetName(self):
        return "AgentI Bridge Handler"


_QUEUE = _MainThreadQueue()
_HANDLER = BridgeEventHandler(_QUEUE)
_EXTERNAL_EVENT = ExternalEvent.Create(_HANDLER)


def run_on_main_thread(fn, args=(), timeout_ms=600000):
    """Push a job onto the queue, raise the event, block until it returns."""
    done = ManualResetEvent(False)
    result_holder = {"data": None}
    _QUEUE.put((fn, args, result_holder, done))
    _EXTERNAL_EVENT.Raise()
    signalled = done.WaitOne(timeout_ms)
    if not signalled:
        return {"status": "error", "reason": "internal_error",
                "message": "Main-thread call timed out after {} ms".format(timeout_ms)}
    return result_holder["data"]


# ---------------------------------------------------------------------------
# Element collection on main thread → upload to Railway on worker thread
# ---------------------------------------------------------------------------

def _gather_elements(doc):
    """Main-thread function. Returns a JSON-safe payload of all elements."""
    try:
        elements = agenti_collect.collect_all_elements(doc)
    except Exception as e:
        return {"status": "error", "reason": "collect_failed",
                "message": str(e), "trace": traceback.format_exc()}

    # Bucket counts for the reply — handy for the user to confirm the upload
    # looks roughly right ("400 walls, 30 rooms" vs surprise empty result).
    counts = {}
    for el in elements:
        c = el.get("category", "?")
        counts[c] = counts.get(c, 0) + 1

    return {
        "status": "ready",
        "elements": elements,
        "counts": counts,
        "total": len(elements),
    }


def _upload_to_railway(elements):
    """Worker-thread function. POST elements to Railway /upload-data. Uses
    System.Net.WebClient to stay consistent with the existing pushbutton —
    pyRevit's bundled httpx is fine too, but WebClient is already imported."""
    url = RAILWAY_BASE_URL + "/upload-data"
    body = agenti_collect.safe_json_dumps({"elements": elements})

    wc = WebClient()
    wc.Headers.Add("Content-Type", "application/json")
    wc.Encoding = Encoding.UTF8

    try:
        result_str = wc.UploadString(url, "POST", body)
        try:
            data = json.loads(result_str)
        except Exception:
            data = {"raw": result_str}
        return {"status": "ok", "response": data}
    except WebException as e:
        body_text = ""
        if e.Response:
            try:
                import System.IO as _sio
                stream = e.Response.GetResponseStream()
                reader = _sio.StreamReader(stream)
                body_text = reader.ReadToEnd()
            except Exception:
                pass
        return {"status": "http_error", "message": str(e.Message), "body": body_text}
    except Exception as e:
        return {"status": "error", "message": str(e), "trace": traceback.format_exc()}
    finally:
        try:
            wc.Dispose()
        except Exception:
            pass


def _do_push_to_notion(body):
    """Action handler. Collects current Revit data on the main thread, then
    uploads to Railway on this worker thread. Returns the shape Agent A
    expects (status + statistics)."""
    _log("push_to_notion: starting (mode={!r})".format(body.get("mode")))

    snap = run_on_main_thread(_gather_elements)
    if not snap or snap.get("status") != "ready":
        _log("push_to_notion: element collection failed: {}".format(snap))
        return snap or {"status": "error", "reason": "internal_error",
                        "message": "Element collection returned nothing."}

    total = snap["total"]
    counts = snap["counts"]
    elements = snap["elements"]
    _log("push_to_notion: collected {} elements: {}".format(total, counts))

    if total == 0:
        return {"status": "error", "reason": "no_elements",
                "message": "Active Revit document has no elements to upload."}

    upload = _upload_to_railway(elements)
    _log("push_to_notion: upload result={}".format(upload.get("status")))

    if upload["status"] != "ok":
        return {"status": "error", "reason": "upload_failed",
                "message": upload.get("message", "Upload to Railway failed."),
                "details": upload.get("body", "")[:1000]}

    return {
        "status": "success",
        "target_parameter": "(none)",
        "statistics": {
            "total_elements": total,
            "rooms":   counts.get("Room", 0),
            "doors":   counts.get("Door", 0),
            "walls":   counts.get("Wall", 0),
            "floors":  counts.get("Floor", 0),
            "parking": counts.get("Parking", 0),
        },
        "ai_sanity_check_insights": [
            "Uploaded {} elements to Railway.".format(total),
            "Open {}/chat to push the snapshot into Notion.".format(RAILWAY_BASE_URL),
        ],
    }


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------

_ACTIONS = {"push_to_notion": _do_push_to_notion}


def _write_json(ctx, payload, status_code=200):
    resp = ctx.Response
    try:
        body_str = json.dumps(payload)
    except Exception as e:
        body_str = json.dumps({"status": "error", "reason": "internal_error",
                               "message": "JSON serialization failed: " + str(e)})
    data = Encoding.UTF8.GetBytes(body_str)
    resp.StatusCode = status_code
    resp.ContentType = "application/json"
    resp.ContentLength64 = data.Length
    try:
        resp.OutputStream.Write(data, 0, data.Length)
    finally:
        try:
            resp.OutputStream.Close()
        except Exception:
            pass
        try:
            resp.Close()
        except Exception:
            pass


def _read_request_body(ctx):
    req = ctx.Request
    if not req.HasEntityBody:
        return ""
    encoding = req.ContentEncoding if req.ContentEncoding else Encoding.UTF8
    from System.IO import StreamReader
    reader = StreamReader(req.InputStream, encoding)
    try:
        return reader.ReadToEnd()
    finally:
        try:
            reader.Close()
        except Exception:
            pass


def _handle_request(ctx):
    req = ctx.Request
    method = req.HttpMethod.upper() if req.HttpMethod else ""
    path = req.Url.AbsolutePath

    # HEAD on any path → 200 OK for Agent A's health_check probe
    if method == "HEAD":
        resp = ctx.Response
        resp.StatusCode = 200
        try:
            resp.Close()
        except Exception:
            pass
        return

    if path != "/run" or method != "POST":
        _write_json(ctx, {"status": "error", "reason": "unknown_action",
                          "message": "POST JSON to /run. Got {} {}.".format(method, path)})
        return

    body_str = _read_request_body(ctx)
    try:
        payload = json.loads(body_str) if body_str else {}
    except Exception as e:
        _log("invalid JSON body: {}".format(e))
        _write_json(ctx, {"status": "error", "reason": "invalid_json",
                          "message": "Could not parse request body as JSON: " + str(e)})
        return

    action = payload.get("action")
    _log("inbound /run action={!r} payload={}".format(action, json.dumps(payload)[:300]))

    handler = _ACTIONS.get(action)
    if handler is None:
        _write_json(ctx, {"status": "error", "reason": "unknown_action",
                          "message": "Unknown action {!r}. Supported: {}.".format(
                              action, ", ".join(sorted(_ACTIONS.keys())))})
        return

    try:
        result = handler(payload)
    except Exception as e:
        _log("handler {} CRASHED: {}\n{}".format(action, e, traceback.format_exc()))
        result = {"status": "error", "reason": "internal_error",
                  "message": "{}: {}".format(type(e).__name__, e),
                  "trace": traceback.format_exc()[-2000:]}

    _write_json(ctx, result)


def _accept_loop(listener):
    while True:
        try:
            ctx = listener.GetContext()
        except Exception:
            return
        try:
            _handle_request(ctx)
        except Exception as e:
            try:
                _write_json(ctx, {"status": "error", "reason": "internal_error",
                                  "message": str(e), "trace": traceback.format_exc()})
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Port-in-use detection + singleton guard
# ---------------------------------------------------------------------------

_BRIDGE_LISTENER = None


def _port_in_use(port):
    try:
        probe = TcpListener(IPAddress.Loopback, port)
        probe.Start()
        probe.Stop()
        return False
    except Exception:
        return True


def _is_listening():
    if _BRIDGE_LISTENER is None:
        return False
    try:
        return bool(_BRIDGE_LISTENER.IsListening)
    except Exception:
        return False


def _try_bind(port):
    listener = HttpListener()
    listener.Prefixes.Add("http://127.0.0.1:{}/".format(port))
    try:
        listener.Start()
        return listener
    except Exception:
        try:
            listener.Close()
        except Exception:
            pass
        return None


def _write_port_file(port):
    try:
        with open(PORT_FILE, "w") as f:
            f.write(str(port))
    except Exception as e:
        _log("could not write port file {}: {}".format(PORT_FILE, e))


# ---------------------------------------------------------------------------
# Entry — callable from ribbon click OR startup.py
# ---------------------------------------------------------------------------

def start_bridge(uiapp=None):
    """Idempotent bridge starter with port fallback. Returns True on success
    (already-running counts), False if every candidate port is taken."""
    global _BRIDGE_LISTENER, BRIDGE_PORT

    if _is_listening():
        _log("bridge already listening on http://127.0.0.1:{}".format(BRIDGE_PORT))
        return True

    listener = None
    chosen_port = None
    for port in range(PREFERRED_PORT, PORT_FALLBACK_MAX + 1):
        if _port_in_use(port):
            continue
        listener = _try_bind(port)
        if listener is not None:
            chosen_port = port
            break

    if listener is None:
        _log("FAILED — no free port in {}-{}".format(PREFERRED_PORT, PORT_FALLBACK_MAX))
        return False

    _BRIDGE_LISTENER = listener
    BRIDGE_PORT = chosen_port
    _write_port_file(chosen_port)

    def _runner():
        _accept_loop(listener)

    t = Thread(ThreadStart(_runner))
    t.IsBackground = True
    t.Start()

    if chosen_port == PREFERRED_PORT:
        _log("listening on http://127.0.0.1:{}".format(chosen_port))
    else:
        _log("listening on http://127.0.0.1:{} (preferred {} was busy)".format(chosen_port, PREFERRED_PORT))
    return True


def main():
    """Ribbon button entry point."""
    if _is_listening():
        forms.alert("Bridge already running on port {}".format(BRIDGE_PORT),
                    title="Agent I Bridge")
        return

    ok = start_bridge(globals().get("__revit__"))
    if ok:
        return

    forms.alert(
        "Failed to start bridge: ports {}-{} are all in use.\n\n"
        "Check the pyRevit output panel for details.".format(
            PREFERRED_PORT, PORT_FALLBACK_MAX),
        title="Agent I Bridge")


if __name__ == "__main__":
    main()
