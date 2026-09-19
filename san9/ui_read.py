# -*- coding: utf-8 -*-
"""Pure structured UI reading.

This module only normalizes evidence supplied by an upstream reader. It does not
capture frames, run OCR, access a window, or produce click coordinates. Missing
evidence is reported as ``unread`` or ``uncertain``; nothing is guessed.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any


TARGET_SCREENS = frozenset({
    "command_menu",
    "command_screen",
    "dialog",
    "input_panel",
    "panel",
    "selection_list",
    "ui_panel",
    "officer_picker",
})


def _result(status: str = "unread", why: str = "") -> dict:
    return {
        "ok": status == "ok",
        "status": status,
        "screen": None,
        "buttons": [],
        "lists": [],
        "inputs": [],
        "highlight": None,
        "hint": None,
        "unread": [],
        "uncertain": [],
        "why": why,
    }


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _screen_gate(evidence: Mapping[str, Any]) -> tuple[str | None, dict | None, str | None]:
    raw = evidence.get("screen")
    if isinstance(raw, str):
        screen_id = _text(raw)
        screen = {"id": screen_id, "ok": True}
    elif isinstance(raw, Mapping):
        screen = dict(raw)
        screen_id = _text(screen.get("id") or screen.get("name") or screen.get("kind"))
    else:
        screen = {}
        screen_id = _text(evidence.get("screen_id") or evidence.get("screen_name"))
        if screen_id:
            screen["id"] = screen_id

    if not screen_id:
        return None, None, "missing_screen_gate"
    if screen_id not in TARGET_SCREENS:
        return screen_id, screen, "screen_not_allowed"
    if screen.get("ok") is False or screen.get("present") is False:
        return screen_id, screen, "screen_gate_failed"
    if "ok" in screen and screen["ok"] is not True:
        return screen_id, screen, "screen_gate_uncertain"
    confidence = screen.get("confidence")
    if confidence is not None:
        try:
            if float(confidence) < 0.8:
                return screen_id, screen, "screen_gate_uncertain"
        except (TypeError, ValueError):
            return screen_id, screen, "screen_gate_uncertain"
    return screen_id, screen, None


def _as_records(value: Any, field: str) -> tuple[list[dict], str | None]:
    if value is None:
        return [], field
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return [], field
    records: list[dict] = []
    for item in value:
        if not isinstance(item, Mapping):
            return [], field
        records.append(dict(item))
    return records, None


def _normalize_button(item: Mapping[str, Any], index: int) -> tuple[dict | None, str | None]:
    label = _text(item.get("label"))
    raw = _text(item.get("raw") or item.get("text"))
    if label is None and raw is None:
        return None, "buttons[%d].label" % index
    out = deepcopy(dict(item))
    if label is not None:
        out["label"] = label
    elif "label" in out:
        out["label"] = None
    if raw is not None:
        out["raw"] = raw
    if "enabled" in item:
        out["enabled"] = bool(item["enabled"])
    elif "disabled" in item:
        out["enabled"] = not bool(item["disabled"])
    else:
        out["enabled"] = None
    if "highlight" in item:
        out["highlight"] = bool(item["highlight"])
    if "x" in item or "y" in item:
        out["coordinate_source"] = "provided"
    return out, None


def _normalize_list(item: Mapping[str, Any], index: int) -> tuple[dict | None, list[str]]:
    name = _text(item.get("id") or item.get("name"))
    if name is None:
        return None, ["lists[%d].id" % index]
    raw_items = item.get("items")
    if raw_items is None:
        return None, ["lists[%d].items" % index]
    if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes, bytearray)):
        return None, ["lists[%d].items" % index]
    normalized: list[dict] = []
    unread: list[str] = []
    for item_index, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, Mapping):
            unread.append("lists[%d].items[%d]" % (index, item_index))
            continue
        row = deepcopy(dict(raw_item))
        label = _text(row.get("label"))
        raw = _text(row.get("raw") or row.get("text"))
        if label is None and raw is None:
            unread.append("lists[%d].items[%d].label" % (index, item_index))
            continue
        if label is not None:
            row["label"] = label
        if raw is not None:
            row["raw"] = raw
        if "index" not in row:
            unread.append("lists[%d].items[%d].index" % (index, item_index))
        if "selected" in row:
            row["selected"] = bool(row["selected"])
        normalized.append(row)
    out = deepcopy(dict(item))
    out["id"] = name
    out["items"] = normalized
    if "selected_index" in item:
        out["selected_index"] = item["selected_index"]
    if "complete" in item:
        out["complete"] = bool(item["complete"])
    return out, unread


def _normalize_highlight(value: Any) -> tuple[Any, list[str]]:
    if value is None:
        return None, ["highlight"]
    if isinstance(value, Mapping):
        out = deepcopy(dict(value))
        if not any(k in out for k in ("button", "list", "item", "index", "target")):
            return None, ["highlight.target"]
        return out, []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return deepcopy(list(value)), []
    return None, ["highlight"]


def _check_highlight(highlight: Any, buttons: list[dict], lists: list[dict]) -> list[str]:
    if highlight is None:
        return []
    matches: list[bool] = []
    if isinstance(highlight, Mapping):
        button = highlight.get("button")
        if button is not None:
            if not buttons:
                matches.append(False)
            else:
                matches.extend(bool(b.get("highlight")) and b.get("label") == button
                               for b in buttons)
        list_id = highlight.get("list")
        item_index = highlight.get("index")
        if list_id is not None and item_index is not None:
            found_list = False
            for list_item in lists:
                if list_item.get("id") == list_id:
                    found_list = True
                    matches.append(any(row.get("selected") is True and row.get("index") == item_index
                                       for row in list_item.get("items", [])))
            if not found_list:
                matches.append(False)
    if matches and not any(matches):
        return ["highlight_conflict"]
    return []


def read_ui(evidence: Mapping[str, Any] | None) -> dict:
    """Read one structured UI evidence packet."""
    out = _result()
    if not isinstance(evidence, Mapping):
        out["unread"] = ["evidence"]
        out["why"] = "missing_structured_evidence"
        return out

    screen_id, screen, gate_error = _screen_gate(evidence)
    out["screen"] = screen
    if gate_error:
        out["unread"] = ["screen"]
        out["why"] = gate_error
        return out

    out["screen"] = dict(screen or {}, id=screen_id)
    buttons, missing = _as_records(evidence.get("buttons"), "buttons")
    if missing and not evidence.get("buttons_present", False):
        out["unread"].append(missing)
    for index, button in enumerate(buttons):
        normalized, error = _normalize_button(button, index)
        if normalized is None:
            out["unread"].append(error)
        else:
            out["buttons"].append(normalized)

    lists, missing = _as_records(evidence.get("lists"), "lists")
    if missing and not evidence.get("lists_present", False):
        out["unread"].append(missing)
    for index, list_item in enumerate(lists):
        normalized, errors = _normalize_list(list_item, index)
        if normalized is None:
            out["unread"].extend(errors)
        else:
            out["lists"].append(normalized)
            out["unread"].extend(errors)

    inputs, missing = _as_records(evidence.get("inputs"), "inputs")
    if missing and not evidence.get("inputs_present", False):
        out["unread"].append(missing)
    out["inputs"] = deepcopy(inputs)

    highlight, errors = _normalize_highlight(evidence.get("highlight"))
    out["highlight"] = highlight
    out["unread"].extend(errors)
    out["uncertain"].extend(_check_highlight(highlight, out["buttons"], out["lists"]))

    if "hint" not in evidence:
        out["unread"].append("hint")
    else:
        hint = evidence.get("hint")
        if isinstance(hint, Mapping):
            out["hint"] = deepcopy(dict(hint))
            if _text(hint.get("text")) is None and hint.get("text") != "":
                out["uncertain"].append("hint.text")
        elif hint is None:
            out["hint"] = None
            out["uncertain"].append("hint")
        elif isinstance(hint, str):
            out["hint"] = hint
        else:
            out["uncertain"].append("hint")

    out["unread"] = [item for item in out["unread"] if item]
    out["uncertain"] = [item for item in out["uncertain"] if item]
    out["status"] = "unread" if out["unread"] else ("uncertain" if out["uncertain"] else "ok")
    out["ok"] = out["status"] == "ok"
    out["why"] = "" if out["status"] == "ok" else out["status"]
    return out


read_current_panel = read_ui
parse_ui = read_ui


__all__ = ["TARGET_SCREENS", "parse_ui", "read_current_panel", "read_ui"]
