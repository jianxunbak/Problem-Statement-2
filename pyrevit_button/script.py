# -*- coding: utf-8 -*-
# ============================================================
#  Revit Data Exporter - PyRevit Script
#  IronPython 2 compatible (Revit 2026)
#
#  Mode 1: Auto-create new Notion Database (Snapshot)
#  Mode 2: Version tracking in fixed Notion Database
#  Mode 3: Upload to local server for Claude Agent
# ============================================================

import json
import clr

clr.AddReference("System.Net")
clr.AddReference("System")
clr.AddReference("System.IO")

import System
import System.IO

from System.Net import WebClient, WebException
from System.Text import Encoding
from System import DateTime

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    BuiltInCategory,
    BuiltInParameter,
)

from pyrevit import forms, script

# ============================================================
#  CONFIG
# ============================================================
NOTION_TOKEN = "your_notion_token_here" 
PARENT_PAGE_ID    = "your own database parent page id here"  # e.g. "1234567890abcdef1234567890abcdef"
FIXED_DATABASE_ID = "your_fixed_database_id_here"
AUTOMATION_SERVER = "http://127.0.0.1:8000"

doc    = __revit__.ActiveUIDocument.Document
logger = script.get_logger()


# ------------------------------------------------------------
#  Helpers
# ------------------------------------------------------------
def get_element_id(el):
    try:
        return int(el.Id.Value)
    except AttributeError:
        try:
            return int(el.Id.IntegerValue)
        except:
            return 0

def get_family_and_type(el):
    try:
        symbol = doc.GetElement(el.GetTypeId())
        if symbol is None:
            return "Unnamed"
        type_name   = ""
        family_name = ""
        p = symbol.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME)
        if p:
            type_name = p.AsString() or ""
        try:
            family_name = symbol.Family.Name or ""
        except:
            pass
        if family_name and type_name:
            return family_name + " " + type_name
        return type_name or family_name or "Unnamed"
    except:
        return "Unnamed"

def get_str(el, bip):
    try:
        p = el.get_Parameter(bip)
        v = p.AsString() if p else None
        return v if v else ""
    except:
        return ""

def get_val_str(el, bip):
    try:
        p = el.get_Parameter(bip)
        v = p.AsValueString() if p else None
        return v if v else ""
    except:
        return ""

def get_int(el, bip):
    try:
        p = el.get_Parameter(bip)
        return int(p.AsInteger()) if p else 0
    except:
        return 0

def get_double(el, bip):
    try:
        p = el.get_Parameter(bip)
        return p.AsDouble() if p else 0.0
    except:
        return 0.0

def get_level_name(el):
    for bip in [
        BuiltInParameter.FAMILY_LEVEL_PARAM,
        BuiltInParameter.LEVEL_NAME,
        BuiltInParameter.WALL_BASE_CONSTRAINT,
        BuiltInParameter.SCHEDULE_LEVEL_PARAM,
    ]:
        try:
            p = el.get_Parameter(bip)
            if p:
                v = p.AsValueString() or p.AsString()
                if v:
                    return v
        except:
            pass
    try:
        level_id = el.LevelId
        if level_id:
            level = doc.GetElement(level_id)
            if level:
                return level.Name
    except:
        pass
    return ""

def ft2_to_m2(val):
    return round(val * 0.0929, 3)

def ft_to_m(val):
    return round(val * 0.3048, 3)

def ft3_to_m3(val):
    return round(val * 0.0283168, 3)


# ------------------------------------------------------------
#  Collect Rooms
# ------------------------------------------------------------
def collect_rooms():
    collector = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Rooms)
        .WhereElementIsNotElementType()
    )
    elements = []
    for el in collector:
        try:
            elements.append({
                "unique_id":        el.UniqueId,
                "element_id":       get_element_id(el),
                "category":         "Room",
                "name":             get_str(el, BuiltInParameter.ROOM_NAME) or "Unnamed",
                "number":           get_str(el, BuiltInParameter.ROOM_NUMBER),
                "level":            get_str(el, BuiltInParameter.LEVEL_NAME),
                "area":             ft2_to_m2(get_double(el, BuiltInParameter.ROOM_AREA)),
                "perimeter":        ft_to_m(get_double(el, BuiltInParameter.ROOM_PERIMETER)),
                "height":           ft_to_m(get_double(el, BuiltInParameter.ROOM_HEIGHT)),
                "department":       get_str(el, BuiltInParameter.ROOM_DEPARTMENT),
                "occupancy":        get_str(el, BuiltInParameter.ROOM_OCCUPANCY),
                "comments":         get_str(el, BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS),
                "phase_created":    get_str(el, BuiltInParameter.PHASE_CREATED),
                "phase_demolished": get_str(el, BuiltInParameter.PHASE_DEMOLISHED),
            })
        except Exception as e:
            logger.warning("Skip Room " + str(el.Id) + ": " + str(e))
    return elements


# ------------------------------------------------------------
#  Collect Doors
# ------------------------------------------------------------
def collect_doors():
    collector = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Doors)
        .WhereElementIsNotElementType()
    )
    elements = []
    for el in collector:
        try:
            elements.append({
                "unique_id":        el.UniqueId,
                "element_id":       get_element_id(el),
                "category":         "Door",
                "name":             get_family_and_type(el),
                "number":           get_str(el, BuiltInParameter.ALL_MODEL_MARK),
                "level":            get_level_name(el),
                "width":            ft_to_m(get_double(el, BuiltInParameter.DOOR_WIDTH)),
                "height":           ft_to_m(get_double(el, BuiltInParameter.DOOR_HEIGHT)),
                "fire_rating":      get_str(el, BuiltInParameter.DOOR_FIRE_RATING),
                "frame_material":   get_str(el, BuiltInParameter.DOOR_FRAME_MATERIAL),
                "comments":         get_str(el, BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS),
                "phase_created":    get_str(el, BuiltInParameter.PHASE_CREATED),
                "phase_demolished": get_str(el, BuiltInParameter.PHASE_DEMOLISHED),
            })
        except Exception as e:
            logger.warning("Skip Door " + str(el.Id) + ": " + str(e))
    return elements


# ------------------------------------------------------------
#  Collect Walls
# ------------------------------------------------------------
def collect_walls():
    collector = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Walls)
        .WhereElementIsNotElementType()
    )
    elements = []
    for el in collector:
        try:
            elements.append({
                "unique_id":          el.UniqueId,
                "element_id":         get_element_id(el),
                "category":           "Wall",
                "name":               get_family_and_type(el),
                "number":             get_str(el, BuiltInParameter.ALL_MODEL_MARK),
                "level":              get_level_name(el),
                "length":             ft_to_m(get_double(el, BuiltInParameter.CURVE_ELEM_LENGTH)),
                "area":               ft2_to_m2(get_double(el, BuiltInParameter.HOST_AREA_COMPUTED)),
                "volume":             ft3_to_m3(get_double(el, BuiltInParameter.HOST_VOLUME_COMPUTED)),
                "base_constraint":    get_val_str(el, BuiltInParameter.WALL_BASE_CONSTRAINT),
                "top_constraint":     get_val_str(el, BuiltInParameter.WALL_HEIGHT_TYPE),
                "unconnected_height": ft_to_m(get_double(el, BuiltInParameter.WALL_USER_HEIGHT_PARAM)),
                "function":           get_val_str(el, BuiltInParameter.FUNCTION_PARAM),
                "phase_created":      get_str(el, BuiltInParameter.PHASE_CREATED),
                "phase_demolished":   get_str(el, BuiltInParameter.PHASE_DEMOLISHED),
            })
        except Exception as e:
            logger.warning("Skip Wall " + str(el.Id) + ": " + str(e))
    return elements


# ------------------------------------------------------------
#  Collect Floors
# ------------------------------------------------------------
def collect_floors():
    collector = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Floors)
        .WhereElementIsNotElementType()
    )
    elements = []
    for el in collector:
        try:
            elements.append({
                "unique_id":        el.UniqueId,
                "element_id":       get_element_id(el),
                "category":         "Floor",
                "name":             get_family_and_type(el),
                "number":           get_str(el, BuiltInParameter.ALL_MODEL_MARK),
                "level":            get_level_name(el),
                "area":             ft2_to_m2(get_double(el, BuiltInParameter.HOST_AREA_COMPUTED)),
                "volume":           ft3_to_m3(get_double(el, BuiltInParameter.HOST_VOLUME_COMPUTED)),
                "thickness":        ft_to_m(get_double(el, BuiltInParameter.FLOOR_ATTR_THICKNESS_PARAM)),
                "structural":       get_int(el, BuiltInParameter.FLOOR_PARAM_IS_STRUCTURAL),
                "comments":         get_str(el, BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS),
                "phase_created":    get_str(el, BuiltInParameter.PHASE_CREATED),
                "phase_demolished": get_str(el, BuiltInParameter.PHASE_DEMOLISHED),
            })
        except Exception as e:
            logger.warning("Skip Floor " + str(el.Id) + ": " + str(e))
    return elements


# ------------------------------------------------------------
#  Collect Parking
# ------------------------------------------------------------
def collect_parking():
    collector = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Parking)
        .WhereElementIsNotElementType()
    )
    elements = []
    for el in collector:
        try:
            elements.append({
                "unique_id":        el.UniqueId,
                "element_id":       get_element_id(el),
                "category":         "Parking",
                "name":             get_family_and_type(el),
                "number":           get_str(el, BuiltInParameter.ALL_MODEL_MARK),
                "level":            get_level_name(el),
                "comments":         get_str(el, BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS),
                "phase_created":    get_str(el, BuiltInParameter.PHASE_CREATED),
                "phase_demolished": get_str(el, BuiltInParameter.PHASE_DEMOLISHED),
            })
        except Exception as e:
            logger.warning("Skip Parking " + str(el.Id) + ": " + str(e))
    return elements


def collect_elements():
    elements = []
    elements += collect_rooms()
    elements += collect_doors()
    elements += collect_walls()
    elements += collect_floors()
    elements += collect_parking()
    print("  Rooms:   " + str(len([e for e in elements if e["category"] == "Room"])))
    print("  Doors:   " + str(len([e for e in elements if e["category"] == "Door"])))
    print("  Walls:   " + str(len([e for e in elements if e["category"] == "Wall"])))
    print("  Floors:  " + str(len([e for e in elements if e["category"] == "Floor"])))
    print("  Parking: " + str(len([e for e in elements if e["category"] == "Parking"])))
    return elements


# ------------------------------------------------------------
#  Notion HTTP helpers
# ------------------------------------------------------------
def make_webclient():
    wc = WebClient()
    wc.Headers.Add("Authorization", "Bearer " + NOTION_TOKEN)
    wc.Headers.Add("Notion-Version", "2022-06-28")
    wc.Headers.Add("Content-Type", "application/json")
    wc.Encoding = Encoding.UTF8
    return wc

def notion_post(url, payload_dict):
    wc   = make_webclient()
    body = json.dumps(payload_dict)
    try:
        result = wc.UploadString(url, "POST", body)
        return "OK", json.loads(result)
    except WebException as e:
        stream = e.Response.GetResponseStream()
        reader = System.IO.StreamReader(stream)
        logger.error("POST error: " + reader.ReadToEnd())
        return "Error", {}
    finally:
        wc.Dispose()

def notion_query(url, payload_dict=None):
    if payload_dict is None:
        payload_dict = {}
    return notion_post(url, payload_dict)


# ------------------------------------------------------------
#  Build Notion page payload
# ------------------------------------------------------------
def build_notion_payload(database_id, el):
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

    # Parking has no extra numeric fields, base props are sufficient

    return {"parent": {"database_id": database_id}, "properties": props}


# ------------------------------------------------------------
#  Mode 1: Create new Database (Snapshot)
# ------------------------------------------------------------
def create_snapshot_database(date_str):
    url = "https://api.notion.com/v1/databases"
    payload = {
        "parent": {"type": "page_id", "page_id": PARENT_PAGE_ID},
        "title": [{"type": "text", "text": {"content": "Revit Export " + date_str}}],
        "properties": {
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
        },
    }
    status, data = notion_post(url, payload)
    if "id" not in data:
        raise Exception("Failed to create database: " + str(data))
    new_id = data["id"]
    print("Created Database: Revit Export " + date_str + " | ID: " + new_id)
    return new_id

def export_all_as_new(database_id, elements):
    url = "https://api.notion.com/v1/pages"
    for el in elements:
        payload = build_notion_payload(database_id, el)
        status, data = notion_post(url, payload)
        if "id" in data:
            print("  + " + el["category"] + ": " + el["name"])
        else:
            logger.error("  x Failed: " + el["name"] + " | " + str(data))


# ------------------------------------------------------------
#  Mode 2: Version tracking
# ------------------------------------------------------------
def get_next_version(database_id):
    url = "https://api.notion.com/v1/databases/" + database_id + "/query"
    status, data = notion_query(url, {})
    versions = []
    for page in data.get("results", []):
        v = page["properties"].get("Version", {}).get("number")
        if v is not None:
            versions.append(v)
    return max(versions) + 1 if versions else 1

def export_with_version(database_id, version, elements):
    url         = "https://api.notion.com/v1/pages"
    today       = DateTime.Today
    export_date = str(today.Year) + "-" + str(today.Month).zfill(2) + "-" + str(today.Day).zfill(2)
    for el in elements:
        payload = build_notion_payload(database_id, el)
        payload["properties"]["Version"]     = {"number": version}
        payload["properties"]["Export Date"] = {"date": {"start": export_date}}
        status, data = notion_post(url, payload)
        if "id" in data:
            print("  + v" + str(version) + " | " + el["category"] + ": " + el["name"])
        else:
            logger.error("  x Failed: " + el["name"] + " | " + str(data))


# ------------------------------------------------------------
#  Upload to Server (for Claude Agent)
# ------------------------------------------------------------
def upload_to_server(elements):
    url  = AUTOMATION_SERVER + "/upload-data"
    body = json.dumps({"elements": elements})

    wc = WebClient()
    wc.Headers.Add("Content-Type", "application/json")
    wc.Encoding = Encoding.UTF8

    try:
        result  = wc.UploadString(url, "POST", body)
        data    = json.loads(result)
        summary = data.get("summary", {})
        print("  Server upload OK: " + str(summary))
        return True
    except WebException as e:
        stream = e.Response.GetResponseStream() if e.Response else None
        if stream:
            reader = System.IO.StreamReader(stream)
            print("  Server upload failed: " + reader.ReadToEnd())
        else:
            print("  Server upload failed: " + str(e.Message))
        return False
    except Exception as e:
        print("  Warning: Could not reach server: " + str(e))
        return False
    finally:
        wc.Dispose()


# ============================================================
#  Main
# ============================================================
def main():
    print("Collecting data from Revit...")
    elements = collect_elements()
    print("Found " + str(len(elements)) + " elements total.")

    if not elements:
        forms.alert("No elements found in model.", title="Export Failed")
        script.exit()

    export_mode = forms.ask_for_one_item(
        [
            "Mode 1: Create new Notion Database (Snapshot)",
            "Mode 2: Add to existing Database with version number",
            "Upload to Server (for Claude Agent)",
        ],
        default="Upload to Server (for Claude Agent)",
        prompt="Select export mode:",
        title="Revit Data Export",
    )

    if export_mode is None:
        print("Export cancelled.")
        script.exit()

    if "Mode 1" in export_mode:
        print("\nMode 1: Creating new snapshot Database...")
        today    = DateTime.Today
        date_str = str(today.Year) + "-" + str(today.Month).zfill(2) + "-" + str(today.Day).zfill(2)
        database_id = create_snapshot_database(date_str)
        export_all_as_new(database_id, elements)
        msg = "Mode 1 complete! " + str(len(elements)) + " records exported."
        print(msg)
        forms.alert(msg, title="Export Complete")

    elif "Mode 2" in export_mode:
        print("\nMode 2: Writing versioned data...")
        version = get_next_version(FIXED_DATABASE_ID)
        print("Version: v" + str(version))
        export_with_version(FIXED_DATABASE_ID, version, elements)
        msg = "Mode 2 complete! " + str(len(elements)) + " records as v" + str(version) + "."
        print(msg)
        forms.alert(msg, title="Export Complete")

    elif "Upload to Server" in export_mode:
        print("\nUploading to server...")
        ok = upload_to_server(elements)
        if ok:
            forms.alert(
                "Done! " + str(len(elements)) + " elements uploaded.\n\n"
                "Run agent.py in your terminal\n"
                "and tell Claude what to push to Notion.",
                title="Upload Successful"
            )
        else:
            forms.alert(
                "Could not reach the server.\n\n"
                "Make sure server.py is running:\n"
                "uvicorn server:app --reload",
                title="Server Not Found"
            )

if __name__ == "__main__":
    main()