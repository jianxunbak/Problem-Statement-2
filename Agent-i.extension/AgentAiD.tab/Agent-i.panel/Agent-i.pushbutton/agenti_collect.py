# -*- coding: utf-8 -*-
"""Revit element collection — shared by the Export to Notion pushbutton and
the Bridge.pushbutton's headless /run handler.

IronPython 2 compatible (Revit 2026). Lifted out of the original pushbutton
script so it can be called from contexts that don't drive the pyRevit forms.
The `doc` argument is required everywhere so the headless caller can pass in
the right document without touching `__revit__` globals.
"""
import json

import clr
clr.AddReference("System")

import System
from System import DateTime

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    BuiltInCategory,
    BuiltInParameter,
)


def to_ascii(val):
    """Convert any string value to a pure ASCII string. IP2 UnicodeDecodeError fix."""
    if val is None:
        return ""
    try:
        if isinstance(val, unicode):  # noqa: F821  (IronPython 2)
            return val.encode("ascii", "replace").decode("ascii")
        if isinstance(val, str):
            return val.decode("utf-8", "replace").encode("ascii", "replace").decode("ascii")
        return str(val)
    except:
        return ""


def deep_ascii(data):
    if isinstance(data, dict):
        return {to_ascii(k): deep_ascii(v) for k, v in data.items()}
    if isinstance(data, list):
        return [deep_ascii(i) for i in data]
    if isinstance(data, (str, unicode)):  # noqa: F821
        return to_ascii(data)
    if isinstance(data, bool):
        return data
    if isinstance(data, (int, long, float)):  # noqa: F821
        return data
    return to_ascii(data)


def safe_json_dumps(obj):
    return json.dumps(deep_ascii(obj))


# ── Parameter helpers ────────────────────────────────────────────────────────

def _get_element_id(el):
    try:
        return int(el.Id.Value)
    except AttributeError:
        try:
            return int(el.Id.IntegerValue)
        except:
            return 0


def _get_family_and_type(doc, el):
    try:
        symbol = doc.GetElement(el.GetTypeId())
        if symbol is None:
            return "Unnamed"
        type_name = ""
        family_name = ""
        p = symbol.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME)
        if p and p.AsString():
            type_name = to_ascii(p.AsString())
        try:
            if symbol.Family:
                family_name = to_ascii(symbol.Family.Name)
        except:
            pass
        if family_name and type_name:
            return family_name + " " + type_name
        return type_name or family_name or "Unnamed"
    except:
        return "Unnamed"


def _get_type_description(doc, el):
    try:
        symbol = doc.GetElement(el.GetTypeId())
        if symbol:
            p = symbol.get_Parameter(BuiltInParameter.ALL_MODEL_DESCRIPTION)
            if p and p.AsString():
                return to_ascii(p.AsString())
    except:
        pass
    return ""


def _get_str(el, bip):
    try:
        p = el.get_Parameter(bip)
        v = p.AsString() if p else None
        return to_ascii(v) if v else ""
    except:
        return ""


def _get_val_str(el, bip):
    try:
        p = el.get_Parameter(bip)
        v = p.AsValueString() if p else None
        return to_ascii(v) if v else ""
    except:
        return ""


def _get_int(el, bip):
    try:
        p = el.get_Parameter(bip)
        return int(p.AsInteger()) if p else 0
    except:
        return 0


def _get_double(el, bip):
    try:
        p = el.get_Parameter(bip)
        return p.AsDouble() if p else 0.0
    except:
        return 0.0


def _get_level_name(doc, el):
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
                    return to_ascii(v)
        except:
            pass
    try:
        level_id = el.LevelId
        if level_id:
            level = doc.GetElement(level_id)
            if level:
                return to_ascii(level.Name)
    except:
        pass
    return ""


def _ft2_to_m2(v): return round(v * 0.0929, 3)
def _ft_to_m(v):   return round(v * 0.3048, 3)
def _ft3_to_m3(v): return round(v * 0.0283168, 3)


# ── Category collectors ──────────────────────────────────────────────────────

def _collect_rooms(doc):
    out = []
    for el in (FilteredElementCollector(doc)
               .OfCategory(BuiltInCategory.OST_Rooms)
               .WhereElementIsNotElementType()):
        try:
            out.append({
                "unique_id":        to_ascii(el.UniqueId),
                "element_id":       _get_element_id(el),
                "category":         "Room",
                "name":             _get_str(el, BuiltInParameter.ROOM_NAME) or "Unnamed",
                "number":           _get_str(el, BuiltInParameter.ROOM_NUMBER),
                "level":            _get_str(el, BuiltInParameter.LEVEL_NAME),
                "area":             _ft2_to_m2(_get_double(el, BuiltInParameter.ROOM_AREA)),
                "perimeter":        _ft_to_m(_get_double(el, BuiltInParameter.ROOM_PERIMETER)),
                "height":           _ft_to_m(_get_double(el, BuiltInParameter.ROOM_HEIGHT)),
                "department":       _get_str(el, BuiltInParameter.ROOM_DEPARTMENT),
                "occupancy":        _get_str(el, BuiltInParameter.ROOM_OCCUPANCY),
                "comments":         _get_str(el, BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS),
                "description":      _get_type_description(doc, el),
                "phase_created":    _get_str(el, BuiltInParameter.PHASE_CREATED),
                "phase_demolished": _get_str(el, BuiltInParameter.PHASE_DEMOLISHED),
            })
        except:
            pass
    return out


def _collect_doors(doc):
    out = []
    for el in (FilteredElementCollector(doc)
               .OfCategory(BuiltInCategory.OST_Doors)
               .WhereElementIsNotElementType()):
        try:
            out.append({
                "unique_id":        to_ascii(el.UniqueId),
                "element_id":       _get_element_id(el),
                "category":         "Door",
                "name":             _get_family_and_type(doc, el),
                "number":           _get_str(el, BuiltInParameter.ALL_MODEL_MARK),
                "level":            _get_level_name(doc, el),
                "width":            _ft_to_m(_get_double(el, BuiltInParameter.DOOR_WIDTH)),
                "height":           _ft_to_m(_get_double(el, BuiltInParameter.DOOR_HEIGHT)),
                "fire_rating":      _get_str(el, BuiltInParameter.DOOR_FIRE_RATING),
                "frame_material":   _get_str(el, BuiltInParameter.DOOR_FRAME_MATERIAL),
                "comments":         _get_str(el, BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS),
                "description":      _get_type_description(doc, el),
                "phase_created":    _get_str(el, BuiltInParameter.PHASE_CREATED),
                "phase_demolished": _get_str(el, BuiltInParameter.PHASE_DEMOLISHED),
            })
        except:
            pass
    return out


def _collect_walls(doc):
    out = []
    for el in (FilteredElementCollector(doc)
               .OfCategory(BuiltInCategory.OST_Walls)
               .WhereElementIsNotElementType()):
        try:
            out.append({
                "unique_id":          to_ascii(el.UniqueId),
                "element_id":         _get_element_id(el),
                "category":           "Wall",
                "name":               _get_family_and_type(doc, el),
                "number":             _get_str(el, BuiltInParameter.ALL_MODEL_MARK),
                "level":              _get_level_name(doc, el),
                "length":             _ft_to_m(_get_double(el, BuiltInParameter.CURVE_ELEM_LENGTH)),
                "area":               _ft2_to_m2(_get_double(el, BuiltInParameter.HOST_AREA_COMPUTED)),
                "volume":             _ft3_to_m3(_get_double(el, BuiltInParameter.HOST_VOLUME_COMPUTED)),
                "base_constraint":    _get_val_str(el, BuiltInParameter.WALL_BASE_CONSTRAINT),
                "top_constraint":     _get_val_str(el, BuiltInParameter.WALL_HEIGHT_TYPE),
                "unconnected_height": _ft_to_m(_get_double(el, BuiltInParameter.WALL_USER_HEIGHT_PARAM)),
                "function":           _get_val_str(el, BuiltInParameter.FUNCTION_PARAM),
                "comments":           _get_str(el, BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS),
                "description":        _get_type_description(doc, el),
                "phase_created":      _get_str(el, BuiltInParameter.PHASE_CREATED),
                "phase_demolished":   _get_str(el, BuiltInParameter.PHASE_DEMOLISHED),
            })
        except:
            pass
    return out


def _collect_floors(doc):
    out = []
    for el in (FilteredElementCollector(doc)
               .OfCategory(BuiltInCategory.OST_Floors)
               .WhereElementIsNotElementType()):
        try:
            out.append({
                "unique_id":        to_ascii(el.UniqueId),
                "element_id":       _get_element_id(el),
                "category":         "Floor",
                "name":             _get_family_and_type(doc, el),
                "number":           _get_str(el, BuiltInParameter.ALL_MODEL_MARK),
                "level":            _get_level_name(doc, el),
                "area":             _ft2_to_m2(_get_double(el, BuiltInParameter.HOST_AREA_COMPUTED)),
                "volume":           _ft3_to_m3(_get_double(el, BuiltInParameter.HOST_VOLUME_COMPUTED)),
                "thickness":        _ft_to_m(_get_double(el, BuiltInParameter.FLOOR_ATTR_THICKNESS_PARAM)),
                "structural":       _get_int(el, BuiltInParameter.FLOOR_PARAM_IS_STRUCTURAL),
                "comments":         _get_str(el, BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS),
                "description":      _get_type_description(doc, el),
                "phase_created":    _get_str(el, BuiltInParameter.PHASE_CREATED),
                "phase_demolished": _get_str(el, BuiltInParameter.PHASE_DEMOLISHED),
            })
        except:
            pass
    return out


def _collect_parking(doc):
    out = []
    for el in (FilteredElementCollector(doc)
               .OfCategory(BuiltInCategory.OST_Parking)
               .WhereElementIsNotElementType()):
        try:
            out.append({
                "unique_id":        to_ascii(el.UniqueId),
                "element_id":       _get_element_id(el),
                "category":         "Parking",
                "name":             _get_family_and_type(doc, el),
                "number":           _get_str(el, BuiltInParameter.ALL_MODEL_MARK),
                "level":            _get_level_name(doc, el),
                "comments":         _get_str(el, BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS),
                "description":      _get_type_description(doc, el),
                "phase_created":    _get_str(el, BuiltInParameter.PHASE_CREATED),
                "phase_demolished": _get_str(el, BuiltInParameter.PHASE_DEMOLISHED),
            })
        except:
            pass
    return out


def collect_all_elements(doc):
    """Return the full list of elements across all supported categories."""
    elements = []
    elements += _collect_rooms(doc)
    elements += _collect_doors(doc)
    elements += _collect_walls(doc)
    elements += _collect_floors(doc)
    elements += _collect_parking(doc)
    return elements
