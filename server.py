from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Any
import requests

app = FastAPI()

_revit_data = []

class UploadPayload(BaseModel):
    elements: List[Any]


def format_uuid(uid):
    uid = uid.replace("-", "")
    return f"{uid[0:8]}-{uid[8:12]}-{uid[12:16]}-{uid[16:20]}-{uid[20:32]}"


@app.get("/")
def home():
    return {"status": "Revit Automation Server running"}


@app.post("/upload-data")
def upload_data(payload: UploadPayload):
    global _revit_data
    _revit_data = payload.elements
    summary = {}
    for el in _revit_data:
        cat = el.get("category", "Unknown")
        summary[cat] = summary.get(cat, 0) + 1
    return {"status": "success", "summary": summary}


@app.get("/get-data")
def get_data(category: str = None, keyword: str = None):
    data = _revit_data

    # Filter by category
    if category and category.lower() != "all":
        data = [e for e in data if e.get("category", "").lower() == category.lower()]

    # Filter by keyword (substring match on name, case-insensitive)
    if keyword:
        kw = keyword.lower()
        data = [e for e in data if kw in e.get("name", "").lower()]

    return {"status": "success", "elements": data, "count": len(data)}


@app.get("/get-summary")
def get_summary():
    summary = {}
    for el in _revit_data:
        cat = el.get("category", "Unknown")
        summary[cat] = summary.get(cat, 0) + 1
    return {"summary": summary, "total": len(_revit_data)}


import os
NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")

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

    # Parking: base props only (name, level, number, comments, phase)

    if version is not None:
        props["Version"] = num(version)
    if export_date:
        props["Export Date"] = {"date": {"start": export_date}}

    return props


@app.post("/create-database")
def create_database(payload: dict):
    parent_page_id = format_uuid(payload["parent_page_id"])
    title = payload["title"]
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }
    body = {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "title": [{"type": "text", "text": {"content": title}}],
        "properties": SCHEMA
    }
    res = requests.post("https://api.notion.com/v1/databases", headers=headers, json=body)
    data = res.json()
    if "id" in data:
        return {"status": "success", "database_id": format_uuid(data["id"])}
    return {"status": "error", "detail": data}


@app.post("/push-to-notion")
def push_to_notion(payload: dict):
    database_id = format_uuid(payload["database_id"])
    elements    = payload["elements"]
    version     = payload.get("version")
    export_date = payload.get("export_date")

    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }

    results = []
    for el in elements:
        props = build_props(el, version=version, export_date=export_date)
        body  = {"parent": {"database_id": database_id}, "properties": props}
        res   = requests.post("https://api.notion.com/v1/pages", headers=headers, json=body)
        results.append({"name": el.get("name"), "status": res.status_code})
        if res.status_code != 200:
            print(f"  FAILED {el.get('name')}: {res.text}")

    ok   = len([r for r in results if r["status"] == 200])
    fail = len([r for r in results if r["status"] != 200])
    return {"status": "completed", "pushed": ok, "failed": fail}


@app.get("/get-next-version")
def get_next_version(database_id: str):
    database_id = format_uuid(database_id)
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }
    res = requests.post(
        f"https://api.notion.com/v1/databases/{database_id}/query",
        headers=headers, json={}
    )
    data = res.json()
    versions = []
    for page in data.get("results", []):
        v = page["properties"].get("Version", {}).get("number")
        if v is not None:
            versions.append(v)
    return {"next_version": max(versions) + 1 if versions else 1}