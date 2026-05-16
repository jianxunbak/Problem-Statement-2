from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List, Any, Optional
import requests
import anthropic
import json
import os
import threading
from datetime import date, datetime
from fastapi.background import BackgroundTasks
import threading

app = FastAPI()

# ----------------------------------------------------------------
# Config
# ----------------------------------------------------------------
NOTION_TOKEN      = os.environ.get("NOTION_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
PARENT_PAGE_ID    = "3492bc72289b8081970ac57e2816e0c5"
FIXED_DATABASE_ID = "3492bc72289b80dda791c15cf5a575e4"
SNAPSHOT_DIR      = "/app/snapshots"  # Railway filesystem

# ----------------------------------------------------------------
# In-memory store
# ----------------------------------------------------------------
_revit_data = []
_jobs       = {}

class UploadPayload(BaseModel):
    elements: List[Any]


# ----------------------------------------------------------------
# Snapshot helpers
# ----------------------------------------------------------------
def ensure_snapshot_dir():
    if not os.path.exists(SNAPSHOT_DIR):
        os.makedirs(SNAPSHOT_DIR)

def save_snapshot(elements):
    """Save a timestamped snapshot of the uploaded data."""
    ensure_snapshot_dir()
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    path      = os.path.join(SNAPSHOT_DIR, f"snapshot_{timestamp}.json")
    with open(path, "w") as f:
        json.dump({
            "timestamp": timestamp,
            "elements":  elements
        }, f)
    # Keep only the latest 10 snapshots to save disk space
    all_snapshots = sorted([
        f for f in os.listdir(SNAPSHOT_DIR) if f.startswith("snapshot_")
    ])
    while len(all_snapshots) > 10:
        os.remove(os.path.join(SNAPSHOT_DIR, all_snapshots.pop(0)))
    print(f"Snapshot saved: {path}")

def load_snapshots():
    """Return list of (timestamp, elements) sorted oldest → newest."""
    ensure_snapshot_dir()
    snapshots = []
    for fname in sorted(os.listdir(SNAPSHOT_DIR)):
        if not fname.startswith("snapshot_"):
            continue
        path = os.path.join(SNAPSHOT_DIR, fname)
        try:
            with open(path) as f:
                data = json.load(f)
            snapshots.append(data)
        except Exception as e:
            print(f"Could not load snapshot {fname}: {e}")
    return snapshots


# ----------------------------------------------------------------
# Compare logic
# ----------------------------------------------------------------
COMPARE_FIELDS = ["name", "level", "area", "number", "width", "height",
                  "length", "volume", "thickness", "fire_rating", "function"]

def compare_two_snapshots(old_elements, new_elements):
    """
    Compare two lists of elements by unique_id.
    Returns dict with added, deleted, modified lists.
    """
    old_map = {e["unique_id"]: e for e in old_elements if e.get("unique_id")}
    new_map = {e["unique_id"]: e for e in new_elements if e.get("unique_id")}

    old_ids = set(old_map.keys())
    new_ids = set(new_map.keys())

    added_ids   = new_ids - old_ids
    deleted_ids = old_ids - new_ids
    common_ids  = old_ids & new_ids

    added   = []
    deleted = []
    modified = []

    for uid in added_ids:
        el = new_map[uid]
        added.append({
            "category":   el.get("category", ""),
            "element_id": el.get("element_id", ""),
            "name":       el.get("name", ""),
            "level":      el.get("level", ""),
        })

    for uid in deleted_ids:
        el = old_map[uid]
        deleted.append({
            "category":   el.get("category", ""),
            "element_id": el.get("element_id", ""),
            "name":       el.get("name", ""),
            "level":      el.get("level", ""),
        })

    for uid in common_ids:
        old_el = old_map[uid]
        new_el = new_map[uid]
        changes = []
        for field in COMPARE_FIELDS:
            old_val = old_el.get(field)
            new_val = new_el.get(field)
            if old_val != new_val and (old_val or new_val):
                changes.append({
                    "field":     field,
                    "old_value": old_val,
                    "new_value": new_val,
                })
        if changes:
            modified.append({
                "category":   new_el.get("category", ""),
                "element_id": new_el.get("element_id", ""),
                "name":       new_el.get("name", ""),
                "level":      new_el.get("level", ""),
                "changes":    changes,
            })

    # Sort by category for readability
    added.sort(key=lambda x: x["category"])
    deleted.sort(key=lambda x: x["category"])
    modified.sort(key=lambda x: x["category"])

    return {
        "added":    added,
        "deleted":  deleted,
        "modified": modified,
    }


def format_compare_result(result, old_ts, new_ts):
    """Format comparison result into a readable string for Claude to present."""
    added    = result["added"]
    deleted  = result["deleted"]
    modified = result["modified"]

    lines = [
        f"📊 Change Summary",
        f"   Snapshot 1: {old_ts}",
        f"   Snapshot 2: {new_ts}",
        "",
    ]

    if not added and not deleted and not modified:
        lines.append("✅ No changes detected between the two snapshots.")
        return "\n".join(lines)

    if added:
        lines.append(f"➕ Added:    {len(added)} elements")
        for el in added:
            lines.append(
                f"   - {el['category']} | ID: {el['element_id']} | {el['name']} | {el['level']}"
            )
        lines.append("")

    if deleted:
        lines.append(f"➖ Deleted:  {len(deleted)} elements")
        for el in deleted:
            lines.append(
                f"   - {el['category']} | ID: {el['element_id']} | {el['name']} | {el['level']}"
            )
        lines.append("")

    if modified:
        lines.append(f"✏️  Modified: {len(modified)} elements")
        for el in modified:
            for ch in el["changes"]:
                lines.append(
                    f"   - {el['category']} | ID: {el['element_id']} | {el['name']} "
                    f"| {ch['field']}: {ch['old_value']} → {ch['new_value']}"
                )
        lines.append("")

    lines.append("💡 Want me to push this change report to Notion?")
    return "\n".join(lines)


# ----------------------------------------------------------------
# UUID / Notion helpers
# ----------------------------------------------------------------
def format_uuid(uid):
    uid = uid.replace("-", "")
    return f"{uid[0:8]}-{uid[8:12]}-{uid[12:16]}-{uid[16:20]}-{uid[20:32]}"

def notion_headers():
    return {
        "Authorization":  f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type":   "application/json"
    }


# ----------------------------------------------------------------
# Basic endpoints
# ----------------------------------------------------------------
@app.get("/")
def home():
    return {"status": "Revit Automation Server running"}


@app.post("/upload-data")
def upload_data(payload: UploadPayload):
    global _revit_data
    _revit_data = payload.elements

    # Auto-save snapshot
    threading.Thread(target=save_snapshot, args=(payload.elements,), daemon=True).start()

    summary = {}
    for el in _revit_data:
        cat = el.get("category", "Unknown")
        summary[cat] = summary.get(cat, 0) + 1

    snapshots     = load_snapshots()
    snapshot_note = ""
    if len(snapshots) >= 2:
        snapshot_note = "Previous snapshot available for comparison."

    return {
        "status":        "success",
        "summary":       summary,
        "snapshot_note": snapshot_note
    }


@app.get("/get-data")
def get_data(category: str = None, keyword: str = None, level: str = None):
    data = _revit_data
    if category and category.lower() != "all":
        data = [e for e in data if e.get("category", "").lower() == category.lower()]
    if keyword:
        kw   = keyword.lower()
        data = [e for e in data if kw in e.get("name", "").lower()]
    if level:
        lv   = level.lower()
        data = [e for e in data if lv in e.get("level", "").lower()]
    return {"status": "success", "elements": data, "count": len(data)}


@app.get("/get-summary")
def get_summary():
    summary = {}
    for el in _revit_data:
        cat = el.get("category", "Unknown")
        summary[cat] = summary.get(cat, 0) + 1
    return {"summary": summary, "total": len(_revit_data)}


@app.get("/job-status/{job_id}")
def job_status(job_id: str):
    return _jobs.get(job_id, {"status": "not_found"})


# ----------------------------------------------------------------
# Notion schema & helpers
# ----------------------------------------------------------------
SCHEMA = {
    "Name":                   {"title": {}},
    "Category":               {"rich_text": {}},
    "UniqueId":               {"rich_text": {}},
    "Element ID":             {"number": {"format": "number"}},
    "Level":                  {"rich_text": {}},
    "Number":                 {"rich_text": {}},
    "Comments":               {"rich_text": {}},
    "Phase Created":          {"rich_text": {}},
    "Phase Demolished":       {"rich_text": {}},
    "Area (m2)":              {"number": {"format": "number"}},
    "Perimeter (m)":          {"number": {"format": "number"}},
    "Height (m)":             {"number": {"format": "number"}},
    "Department":             {"rich_text": {}},
    "Occupancy":              {"rich_text": {}},
    "Width (m)":              {"number": {"format": "number"}},
    "Fire Rating":            {"rich_text": {}},
    "Frame Material":         {"rich_text": {}},
    "Length (m)":             {"number": {"format": "number"}},
    "Volume (m3)":            {"number": {"format": "number"}},
    "Base Constraint":        {"rich_text": {}},
    "Top Constraint":         {"rich_text": {}},
    "Unconnected Height (m)": {"number": {"format": "number"}},
    "Function":               {"rich_text": {}},
    "Thickness (m)":          {"number": {"format": "number"}},
    "Structural":             {"number": {"format": "number"}},
    "Version":                {"number": {"format": "number"}},
    "Export Date":            {"date": {}},
}

CHANGE_REPORT_SCHEMA = {
    "Name":       {"title": {}},
    "Category":   {"rich_text": {}},
    "Element ID": {"number": {"format": "number"}},
    "Level":      {"rich_text": {}},
    "Change":     {"rich_text": {}},
    "Field":      {"rich_text": {}},
    "Old Value":  {"rich_text": {}},
    "New Value":  {"rich_text": {}},
    "Snapshot 1": {"rich_text": {}},
    "Snapshot 2": {"rich_text": {}},
}


def build_props(el, version=None, export_date=None):
    cat = el.get("category", "")

    def rt(val):
        return {"rich_text": [{"text": {"content": str(val) if val else ""}}]}
    def num(val):
        return {"number": val if val is not None else 0}

    props = {
        "Name":             {"title": [{"text": {"content": el.get("name", "")}}]},
        "Category":         rt(cat),
        "UniqueId":         rt(el.get("unique_id", "")),
        "Element ID":       num(el.get("element_id", 0)),
        "Level":            rt(el.get("level", "")),
        "Number":           rt(el.get("number", "")),
        "Comments":         rt(el.get("comments", "")),
        "Phase Created":    rt(el.get("phase_created", "")),
        "Phase Demolished": rt(el.get("phase_demolished", "")),
    }

    if cat == "Room":
        props["Area (m2)"]     = num(el.get("area", 0))
        props["Perimeter (m)"] = num(el.get("perimeter", 0))
        props["Height (m)"]    = num(el.get("height", 0))
        props["Department"]    = rt(el.get("department", ""))
        props["Occupancy"]     = rt(el.get("occupancy", ""))
    elif cat == "Door":
        props["Width (m)"]      = num(el.get("width", 0))
        props["Height (m)"]     = num(el.get("height", 0))
        props["Fire Rating"]    = rt(el.get("fire_rating", ""))
        props["Frame Material"] = rt(el.get("frame_material", ""))
    elif cat == "Wall":
        props["Length (m)"]             = num(el.get("length", 0))
        props["Area (m2)"]              = num(el.get("area", 0))
        props["Volume (m3)"]            = num(el.get("volume", 0))
        props["Base Constraint"]        = rt(el.get("base_constraint", ""))
        props["Top Constraint"]         = rt(el.get("top_constraint", ""))
        props["Unconnected Height (m)"] = num(el.get("unconnected_height", 0))
        props["Function"]               = rt(el.get("function", ""))
    elif cat == "Floor":
        props["Area (m2)"]     = num(el.get("area", 0))
        props["Volume (m3)"]   = num(el.get("volume", 0))
        props["Thickness (m)"] = num(el.get("thickness", 0))
        props["Structural"]    = num(el.get("structural", 0))

    if version is not None:
        props["Version"] = num(version)
    if export_date:
        props["Export Date"] = {"date": {"start": export_date}}

    return props


def notion_create_database(title, schema=None):
    if schema is None:
        schema = SCHEMA
    body = {
        "parent":     {"type": "page_id", "page_id": format_uuid(PARENT_PAGE_ID)},
        "title":      [{"type": "text", "text": {"content": title}}],
        "properties": schema
    }
    res  = requests.post("https://api.notion.com/v1/databases",
                         headers=notion_headers(), json=body)
    data = res.json()
    if "id" in data:
        return format_uuid(data["id"]), None
    return None, str(data)


def notion_push_elements(db_id, elements, version=None, export_date=None):
    ok, fail = 0, 0
    for el in elements:
        props = build_props(el, version=version, export_date=export_date)
        body  = {"parent": {"database_id": db_id}, "properties": props}
        res   = requests.post("https://api.notion.com/v1/pages",
                              headers=notion_headers(), json=body)
        if res.status_code == 200:
            ok += 1
        else:
            fail += 1
            print(f"FAILED {el.get('name')}: {res.text}")
    return ok, fail


def notion_get_next_version(db_id):
    res = requests.post(
        f"https://api.notion.com/v1/databases/{db_id}/query",
        headers=notion_headers(), json={}
    )
    versions = []
    for page in res.json().get("results", []):
        v = page["properties"].get("Version", {}).get("number")
        if v is not None:
            versions.append(v)
    return max(versions) + 1 if versions else 1


# ----------------------------------------------------------------
# Background push functions
# ----------------------------------------------------------------
def bg_create_new_database(job_id, title, elements):
    try:
        _jobs[job_id] = {"status": "running", "message": f"Creating database '{title}'..."}
        db_id, err    = notion_create_database(title)
        if err:
            _jobs[job_id] = {"status": "error", "message": f"Failed to create database: {err}"}
            return

        total = len(elements)
        ok, fail = 0, 0
        for i, el in enumerate(elements):
            props = build_props(el, export_date=str(date.today()))
            body  = {"parent": {"database_id": db_id}, "properties": props}
            res   = requests.post("https://api.notion.com/v1/pages",
                                  headers=notion_headers(), json=body)
            if res.status_code == 200:
                ok += 1
            else:
                fail += 1
            if (i + 1) % 10 == 0:
                _jobs[job_id]["message"] = f"Pushing {i+1} / {total} records..."

        _jobs[job_id] = {
            "status":  "done",
            "message": f"✅ Done! '{title}' created with {ok} records. {fail} failed."
        }
    except Exception as e:
        _jobs[job_id] = {"status": "error", "message": f"Error: {str(e)}"}


def bg_push_versioned(job_id, elements):
    try:
        db_id   = format_uuid(FIXED_DATABASE_ID)
        version = notion_get_next_version(db_id)
        total   = len(elements)
        _jobs[job_id] = {"status": "running", "message": f"Pushing v{version}: 0/{total}..."}

        ok, fail = 0, 0
        for i, el in enumerate(elements):
            props = build_props(el, version=version, export_date=str(date.today()))
            body  = {"parent": {"database_id": db_id}, "properties": props}
            res   = requests.post("https://api.notion.com/v1/pages",
                                  headers=notion_headers(), json=body)
            if res.status_code == 200:
                ok += 1
            else:
                fail += 1
            if (i + 1) % 10 == 0:
                _jobs[job_id]["message"] = f"Pushing v{version}: {i+1}/{total}..."

        _jobs[job_id] = {
            "status":  "done",
            "message": f"✅ Done! v{version} pushed with {ok} records. {fail} failed."
        }
    except Exception as e:
        _jobs[job_id] = {"status": "error", "message": f"Error: {str(e)}"}


def bg_push_change_report(job_id, title, result, old_ts, new_ts):
    try:
        _jobs[job_id] = {"status": "running", "message": "Creating change report database..."}
        db_id, err    = notion_create_database(title, schema=CHANGE_REPORT_SCHEMA)
        if err:
            _jobs[job_id] = {"status": "error", "message": f"Failed to create database: {err}"}
            return

        def rt(val):
            return {"rich_text": [{"text": {"content": str(val) if val else ""}}]}
        def num(val):
            return {"number": val if val is not None else 0}

        rows = []
        for el in result["added"]:
            rows.append({
                "name": el["name"], "category": el["category"],
                "element_id": el["element_id"], "level": el["level"],
                "change": "Added", "field": "", "old": "", "new": ""
            })
        for el in result["deleted"]:
            rows.append({
                "name": el["name"], "category": el["category"],
                "element_id": el["element_id"], "level": el["level"],
                "change": "Deleted", "field": "", "old": "", "new": ""
            })
        for el in result["modified"]:
            for ch in el["changes"]:
                rows.append({
                    "name": el["name"], "category": el["category"],
                    "element_id": el["element_id"], "level": el["level"],
                    "change": "Modified", "field": ch["field"],
                    "old": str(ch["old_value"]), "new": str(ch["new_value"])
                })

        total = len(rows)
        ok, fail = 0, 0
        for i, row in enumerate(rows):
            props = {
                "Name":       {"title": [{"text": {"content": row["name"]}}]},
                "Category":   rt(row["category"]),
                "Element ID": num(row["element_id"]),
                "Level":      rt(row["level"]),
                "Change":     rt(row["change"]),
                "Field":      rt(row["field"]),
                "Old Value":  rt(row["old"]),
                "New Value":  rt(row["new"]),
                "Snapshot 1": rt(old_ts),
                "Snapshot 2": rt(new_ts),
            }
            body = {"parent": {"database_id": db_id}, "properties": props}
            res  = requests.post("https://api.notion.com/v1/pages",
                                 headers=notion_headers(), json=body)
            if res.status_code == 200:
                ok += 1
            else:
                fail += 1
            if (i + 1) % 10 == 0:
                _jobs[job_id]["message"] = f"Pushing {i+1}/{total} rows..."

        _jobs[job_id] = {
            "status":  "done",
            "message": f"✅ Change report '{title}' pushed with {ok} rows. {fail} failed."
        }
    except Exception as e:
        _jobs[job_id] = {"status": "error", "message": f"Error: {str(e)}"}


# ----------------------------------------------------------------
# Claude Agent tools
# ----------------------------------------------------------------
TOOLS = [
    {
        "name": "get_revit_summary",
        "description": "Get a count of all Revit elements by category. Call this first to see what data is available.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "get_stats_by_level",
        "description": "Get element counts broken down by level. Optionally filter by category.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["Room", "Door", "Wall", "Floor", "Parking"],
                    "description": "Optional. Filter by category."
                }
            },
            "required": []
        }
    },
    {
        "name": "preview_filtered_data",
        "description": "Preview how many elements match a category, keyword, and/or level before pushing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": ["Room", "Door", "Wall", "Floor", "Parking"]},
                "keyword":  {"type": "string", "description": "Filter by keyword in element name."},
                "level":    {"type": "string", "description": "Filter by level name (partial match)."}
            },
            "required": []
        }
    },
    {
        "name": "create_new_database",
        "description": "Push filtered Revit elements into a new Notion database (background job). Returns job_id immediately.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title":    {"type": "string", "description": "Name for the new Notion database."},
                "category": {"type": "string", "enum": ["Room", "Door", "Wall", "Floor", "Parking"]},
                "keyword":  {"type": "string"},
                "level":    {"type": "string", "description": "Filter by level name (partial match)."}
            },
            "required": ["title"]
        }
    },
    {
        "name": "push_versioned",
        "description": "Push filtered elements into the fixed Notion database with auto version number (background job).",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": ["Room", "Door", "Wall", "Floor", "Parking"]},
                "keyword":  {"type": "string"},
                "level":    {"type": "string"}
            },
            "required": []
        }
    },
    {
        "name": "compare_snapshots",
        "description": (
            "Compare the two most recent uploaded snapshots and show what was added, deleted, or modified. "
            "Use this when the user asks 'what changed', 'show differences', or 'compare uploads'."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "push_change_report",
        "description": "Push the change report (result of compare_snapshots) into a new Notion database.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Name for the change report database."}
            },
            "required": ["title"]
        }
    },
    {
        "name": "check_job_status",
        "description": "Check the status of a background push job using its job_id.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"}
            },
            "required": ["job_id"]
        }
    }
]

SYSTEM_PROMPT = """You are a helpful BIM assistant for an architectural firm.
You have access to Revit model data (Rooms, Doors, Walls, Floors, Parking) uploaded via PyRevit.

When the user asks to export data to Notion:
1. Call get_revit_summary to see what's available.
2. If filtering, call preview_filtered_data first to confirm the count.
3. Ask: "Would you like to create a new database, or add as a new version?"
4. Call create_new_database or push_versioned — these run in the background and return a job_id.
5. Tell the user the push has started and give the job_id. They can ask "check status" to see progress.

When the user asks about counts per level (e.g. "how many doors on each level"):
- Use get_stats_by_level

When the user asks "what changed", "show differences", "compare uploads":
- Use compare_snapshots
- Present the result clearly with the formatted text
- Ask if they want to push the change report to Notion

When the user asks to push the change report to Notion:
- Use push_change_report with a sensible title like "Change Report 2026-05-14"

Never call create_new_database or push_versioned more than once per user request.
Keep responses concise and friendly."""


def get_filtered_elements(category=None, keyword=None, level=None):
    data = _revit_data
    if category:
        data = [e for e in data if e.get("category", "").lower() == category.lower()]
    if keyword:
        kw   = keyword.lower()
        data = [e for e in data if kw in e.get("name", "").lower()]
    if level:
        lv   = level.lower()
        data = [e for e in data if lv in e.get("level", "").lower()]
    return data


# Store last compare result for push_change_report
_last_compare = {}

def run_tool(name, input_data):
    global _last_compare

    if name == "get_revit_summary":
        summary = {}
        for el in _revit_data:
            cat = el.get("category", "Unknown")
            summary[cat] = summary.get(cat, 0) + 1
        return {"summary": summary, "total": len(_revit_data)}

    elif name == "get_stats_by_level":
        category = input_data.get("category")
        data     = _revit_data
        if category:
            data = [e for e in data if e.get("category", "").lower() == category.lower()]
        stats = {}
        for el in data:
            level = el.get("level", "Unknown")
            cat   = el.get("category", "Unknown")
            if level not in stats:
                stats[level] = {}
            stats[level][cat] = stats[level].get(cat, 0) + 1
        # Sort by level name
        stats = dict(sorted(stats.items()))
        return {"stats_by_level": stats}

    elif name == "preview_filtered_data":
        elements = get_filtered_elements(
            input_data.get("category"),
            input_data.get("keyword"),
            input_data.get("level")
        )
        sample = [
            f"{e.get('category')} | ID: {e.get('element_id')} | {e.get('name')} | {e.get('level')}"
            for e in elements[:5]
        ]
        return {"count": len(elements), "sample": sample}

    elif name == "create_new_database":
        title    = input_data["title"]
        elements = get_filtered_elements(
            input_data.get("category"),
            input_data.get("keyword"),
            input_data.get("level")
        )
        if not elements:
            return {"error": "No elements found matching the filters."}

        job_id = f"job_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{len(_jobs)}"
        _jobs[job_id] = {"status": "starting", "message": "Starting push..."}
        t = threading.Thread(target=bg_create_new_database, args=(job_id, title, elements))
        t.daemon = True
        t.start()
        return {
            "status":  "started",
            "job_id":  job_id,
            "total":   len(elements),
            "message": f"Push started for '{title}' with {len(elements)} elements."
        }

    elif name == "push_versioned":
        elements = get_filtered_elements(
            input_data.get("category"),
            input_data.get("keyword"),
            input_data.get("level")
        )
        if not elements:
            return {"error": "No elements found matching the filters."}

        job_id = f"job_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{len(_jobs)}"
        _jobs[job_id] = {"status": "starting", "message": "Starting push..."}
        t = threading.Thread(target=bg_push_versioned, args=(job_id, elements))
        t.daemon = True
        t.start()
        return {
            "status":  "started",
            "job_id":  job_id,
            "total":   len(elements),
            "message": f"Versioned push started with {len(elements)} elements."
        }

    elif name == "compare_snapshots":
        snapshots = load_snapshots()
        if len(snapshots) < 2:
            return {
                "error": (
                    "Not enough snapshots to compare. "
                    "Please upload Revit data at least twice using the PyRevit button."
                )
            }
        old_snap   = snapshots[-2]
        new_snap   = snapshots[-1]
        old_ts     = old_snap["timestamp"]
        new_ts     = new_snap["timestamp"]
        result     = compare_two_snapshots(old_snap["elements"], new_snap["elements"])
        formatted  = format_compare_result(result, old_ts, new_ts)

        # Store for push_change_report
        _last_compare = {"result": result, "old_ts": old_ts, "new_ts": new_ts}

        return {"report": formatted}

    elif name == "push_change_report":
        if not _last_compare:
            return {"error": "No comparison available. Please run compare_snapshots first."}
        title  = input_data.get("title", f"Change Report {date.today()}")
        job_id = f"job_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{len(_jobs)}"
        _jobs[job_id] = {"status": "starting", "message": "Starting change report push..."}
        t = threading.Thread(
            target=bg_push_change_report,
            args=(job_id, title, _last_compare["result"],
                  _last_compare["old_ts"], _last_compare["new_ts"])
        )
        t.daemon = True
        t.start()
        return {
            "status":  "started",
            "job_id":  job_id,
            "message": f"Change report push started: '{title}'"
        }

    elif name == "check_job_status":
        job_id = input_data.get("job_id")
        return _jobs.get(job_id, {"status": "not_found", "message": "Job not found."})

    return {"error": f"Unknown tool: {name}"}


# ----------------------------------------------------------------
# Chat endpoint
# ----------------------------------------------------------------
class ChatRequest(BaseModel):
    message: str
    history: Optional[List[dict]] = []


@app.post("/ask")
def ask(req: ChatRequest):
    client   = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    messages = req.history + [{"role": "user", "content": req.message}]

    while True:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=messages,
            tools=TOOLS,
        )

        messages.append({"role": "assistant", "content": response.content})

        tool_uses  = [b for b in response.content if b.type == "tool_use"]
        text_parts = [b.text for b in response.content if b.type == "text"]

        if not tool_uses:
            return {"reply": " ".join(text_parts), "history": messages}

        tool_results = []
        for tool_block in tool_uses:
            result = run_tool(tool_block.name, tool_block.input)
            tool_results.append({
                "type":        "tool_result",
                "tool_use_id": tool_block.id,
                "content":     json.dumps(result)
            })
        messages.append({"role": "user", "content": tool_results})


# ----------------------------------------------------------------
# Chat UI
# ----------------------------------------------------------------
@app.get("/chat", response_class=HTMLResponse)
def chat_ui():
    return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Revit → Notion BIM Assistant</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #f5f5f5; height: 100vh;
            display: flex; flex-direction: column;
        }
        header {
            background: #1a1a2e; color: white;
            padding: 16px 24px; display: flex; align-items: center; gap: 12px;
        }
        header h1 { font-size: 18px; font-weight: 600; }
        #status-bar {
            background: #16213e; color: #aaa; font-size: 12px;
            padding: 6px 24px; display: flex; align-items: center; gap: 8px;
        }
        #status-dot { width: 8px; height: 8px; border-radius: 50%; background: #888; }
        #status-dot.online { background: #4caf50; }
        #chat-container {
            flex: 1; overflow-y: auto; padding: 24px;
            display: flex; flex-direction: column; gap: 16px;
        }
        .message {
            max-width: 75%; padding: 12px 16px; border-radius: 12px;
            line-height: 1.5; font-size: 14px; white-space: pre-wrap;
        }
        .message.user {
            background: #1a1a2e; color: white;
            align-self: flex-end; border-bottom-right-radius: 4px;
        }
        .message.assistant {
            background: white; color: #333;
            align-self: flex-start; border-bottom-left-radius: 4px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }
        .message.thinking {
            background: white; color: #999; align-self: flex-start;
            font-style: italic; box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }
        #input-area {
            background: white; border-top: 1px solid #e0e0e0;
            padding: 16px 24px; display: flex; gap: 12px;
        }
        #user-input {
            flex: 1; border: 1px solid #ddd; border-radius: 8px;
            padding: 10px 14px; font-size: 14px; outline: none;
            resize: none; height: 44px;
        }
        #user-input:focus { border-color: #1a1a2e; }
        #send-btn {
            background: #1a1a2e; color: white; border: none;
            border-radius: 8px; padding: 0 20px;
            font-size: 14px; cursor: pointer; height: 44px;
        }
        #send-btn:hover { background: #16213e; }
        #send-btn:disabled { background: #999; cursor: not-allowed; }
        .welcome {
            text-align: center; color: #999; font-size: 14px;
            margin: auto; padding: 40px;
        }
        .welcome h2 { font-size: 20px; color: #555; margin-bottom: 8px; }
        .suggestions {
            display: flex; flex-wrap: wrap; gap: 8px;
            justify-content: center; margin-top: 16px;
        }
        .suggestion {
            background: white; border: 1px solid #ddd; border-radius: 20px;
            padding: 6px 14px; font-size: 13px; cursor: pointer; color: #555;
        }
        .suggestion:hover { border-color: #1a1a2e; color: #1a1a2e; }
    </style>
</head>
<body>
<header>
    <span>🏗️</span>
    <h1>Revit → Notion BIM Assistant</h1>
</header>
<div id="status-bar">
    <div id="status-dot"></div>
    <span id="status-text">Checking server...</span>
</div>
<div id="chat-container">
    <div class="welcome">
        <h2>Hi! I'm your BIM Assistant</h2>
        <p>I can help you export Revit model data to Notion.<br>
        Click the PyRevit button first to upload your model data.</p>
        <div class="suggestions">
            <div class="suggestion" onclick="sendSuggestion(this)">What data is available?</div>
            <div class="suggestion" onclick="sendSuggestion(this)">How many doors on each level?</div>
            <div class="suggestion" onclick="sendSuggestion(this)">Export all doors to a new database</div>
            <div class="suggestion" onclick="sendSuggestion(this)">Export doors on Level 2 only</div>
            <div class="suggestion" onclick="sendSuggestion(this)">What changed since last upload?</div>
            <div class="suggestion" onclick="sendSuggestion(this)">Check status</div>
        </div>
    </div>
</div>
<div id="input-area">
    <textarea id="user-input" placeholder="Ask me to export data, check counts, or compare changes..."></textarea>
    <button id="send-btn" onclick="sendMessage()">Send</button>
</div>
<script>
    let history = [];
    fetch('/get-summary')
        .then(r => r.json())
        .then(data => {
            const dot  = document.getElementById('status-dot');
            const text = document.getElementById('status-text');
            dot.classList.add('online');
            const total = data.total || 0;
            if (total > 0) {
                const parts = Object.entries(data.summary).map(([k,v]) => v + ' ' + k + 's');
                text.textContent = 'Model loaded: ' + parts.join(', ');
            } else {
                text.textContent = 'Server online — no model data yet. Click the PyRevit button first.';
            }
        })
        .catch(() => {
            document.getElementById('status-text').textContent = 'Server offline';
        });

    function sendSuggestion(el) {
        document.getElementById('user-input').value = el.textContent;
        sendMessage();
    }

    function addMessage(text, role) {
        const welcome = document.querySelector('.welcome');
        if (welcome) welcome.remove();
        const container = document.getElementById('chat-container');
        const div = document.createElement('div');
        div.className = 'message ' + role;
        div.textContent = text;
        container.appendChild(div);
        container.scrollTop = container.scrollHeight;
        return div;
    }

    async function sendMessage() {
        const input = document.getElementById('user-input');
        const btn   = document.getElementById('send-btn');
        const text  = input.value.trim();
        if (!text) return;
        input.value  = '';
        btn.disabled = true;
        addMessage(text, 'user');
        const thinking = addMessage('Thinking...', 'thinking');
        try {
            const res = await fetch('/ask', {
                method:  'POST',
                headers: {'Content-Type': 'application/json'},
                body:    JSON.stringify({message: text, history: history})
            });
            const data = await res.json();
            thinking.remove();
            addMessage(data.reply, 'assistant');
            history = data.history;
        } catch(e) {
            thinking.remove();
            addMessage('Error connecting to server. Please try again.', 'assistant');
        }
        btn.disabled = false;
        input.focus();
    }

    document.getElementById('user-input').addEventListener('keydown', function(e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });
</script>
</body>
</html>
"""


# Agent A integration — registers POST /run on `app`. Must stay at the very
# bottom so all existing routes are registered first. See bridge.py.
import bridge  # noqa: E402,F401