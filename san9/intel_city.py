# -*- coding: utf-8 -*-
"""C22 city enemy-intelligence evidence normalizer.

This module is intentionally offline-only.  It accepts structured evidence
already read by the existing city/panel/intel layers and returns a guarded
assessment for expedition, tactic, or diplomacy planning.  It never opens a
screen, captures a frame, or performs input.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any


CITY_SCREENS = frozenset({
    "city_intel",
    "city_info",
    "city_target_intel",
    "enemy_city_intel",
})
CONTEXTS = frozenset({"expedition", "tactic", "diplomacy", "出征", "计略", "計略", "外交"})
NUMERIC_FIELDS = (
    "troops",
    "wounded",
    "morale",
    "durability",
    "population",
    "officers",
    "gold",
    "food",
)


def _fail(reason: str, *, evidence: Mapping[str, Any] | None = None, status: str = "invalid") -> dict:
    return {
        "ok": False,
        "status": status,
        "reason": reason,
        "city": None,
        "empty_city": False,
        "values": {},
        "unread": [],
        "evidence": deepcopy(dict(evidence or {})),
    }


def _screen_gate(observation: Mapping[str, Any]) -> tuple[bool, dict]:
    raw = observation.get("screen")
    if isinstance(raw, Mapping):
        screen = deepcopy(dict(raw))
        kind = screen.get("kind") or screen.get("id") or screen.get("name")
    else:
        kind = raw or observation.get("screen_kind")
        screen = {"kind": kind}
    identified = screen.get("identified", screen.get("ok", True)) is True
    return kind in CITY_SCREENS and identified, screen


def _normalize_numeric(field: str, raw: Any) -> dict:
    """Normalize one all-or-nothing numeric reading without inventing zero."""
    if isinstance(raw, Mapping):
        item = deepcopy(dict(raw))
        status = str(item.get("status") or "").lower()
        source = item.get("source")
        guessed = item.get("guessed") is True or status in {"guess", "guessed", "ocr_guess"}
        exact = item.get("exact", status in {"read", "matched", "exact", "verified"}) is True
        value = item.get("value")
        glyphs = item.get("chars", item.get("glyphs"))
        all_matched = item.get("all_matched")
        if all_matched is None and isinstance(glyphs, list):
            all_matched = bool(glyphs) and all(
                isinstance(char, Mapping) and char.get("status") == "matched" and char.get("digit") is not None
                for char in glyphs
            )
        if all_matched is False:
            exact = False
        if guessed or not exact or value is None:
            return {"field": field, "value": None, "status": "unread", "source": source,
                    "reason": "numeric evidence is guessed, incomplete, or has an unmatched glyph", "raw": item}
    else:
        return {"field": field, "value": None, "status": "unread", "source": None,
                "reason": "numeric evidence must include source and exactness", "raw": raw}

    if isinstance(value, bool):
        valid = False
    elif isinstance(value, int):
        valid = value >= 0
    elif isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
        valid = True
    else:
        valid = False
    if not valid:
        return {"field": field, "value": None, "status": "unread", "source": source,
                "reason": "numeric value is not a non-negative integer", "raw": item}
    return {"field": field, "value": value, "status": "read", "source": source,
            "exact": True, "raw": item}


def read_city_intel(observation: Mapping[str, Any] | None, *, context: str | None = None) -> dict:
    """Return a guarded city-intelligence result from structured evidence."""
    if not isinstance(observation, Mapping):
        return _fail("structured city evidence is missing", status="unread")
    gate_ok, screen = _screen_gate(observation)
    base_evidence = {"screen_gate": {"ok": gate_ok, "screen": screen}}
    if not gate_ok:
        return _fail("screen gate rejected: current screen is not city intelligence",
                     evidence=base_evidence, status="wrong_screen")
    if context is not None and context not in CONTEXTS:
        return _fail("unsupported city-intelligence context", evidence=base_evidence)

    city = observation.get("city")
    if isinstance(city, Mapping):
        city_item = deepcopy(dict(city))
        city_name = city_item.get("name")
        city_status = str(city_item.get("status") or "").lower()
    else:
        city_item = {"name": city, "status": observation.get("city_status")}
        city_name = city
        city_status = str(observation.get("city_status") or "").lower()
    base_evidence["city"] = city_item
    if not isinstance(city_name, str) or not city_name.strip() or city_status in {"candidate", "unknown", "unread"}:
        return _fail("city identity is not locked/readable", evidence=base_evidence, status="unread")

    empty = observation.get("empty_city")
    empty_evidence = observation.get("empty_evidence")
    if empty is True:
        valid_empty = isinstance(empty_evidence, Mapping) and (
            empty_evidence.get("confirmed") is True
            or empty_evidence.get("source") in {"game_hint", "locked_city_row", "structured_panel"}
        )
        if not valid_empty:
            return _fail("empty-city claim lacks explicit evidence", evidence=base_evidence, status="unread")
        return {
            "ok": True,
            "status": "empty",
            "reason": "",
            "context": context,
            "city": city_name.strip(),
            "empty_city": True,
            "values": {"officers": 0, "troops": 0},
            "unread": [],
            "evidence": {**base_evidence, "empty_city": deepcopy(dict(empty_evidence))},
        }

    raw_values = observation.get("values")
    if not isinstance(raw_values, Mapping):
        return _fail("city numeric values are missing", evidence=base_evidence, status="unread")
    values: dict[str, int] = {}
    unread: list[str] = []
    numeric_evidence: dict[str, dict] = {}
    for field in NUMERIC_FIELDS:
        if field not in raw_values:
            continue
        item = _normalize_numeric(field, raw_values[field])
        numeric_evidence[field] = item
        if item["status"] == "read":
            values[field] = item["value"]
        else:
            unread.append(field)
    base_evidence["numbers"] = numeric_evidence

    required = observation.get("required_fields")
    if required is None:
        required = ("troops", "officers")
    if not isinstance(required, (list, tuple)) or not required:
        return _fail("required_fields must be a non-empty list", evidence=base_evidence)
    missing = [field for field in required if field not in values]
    unread = sorted(set(unread + missing))
    if unread:
        result = _fail("required city numbers are unreadable; entire assessment is discarded",
                       evidence=base_evidence, status="unread")
        result.update({"city": city_name.strip(), "values": values, "unread": unread,
                       "context": context})
        return result

    return {
        "ok": True,
        "status": "read",
        "reason": "",
        "context": context,
        "city": city_name.strip(),
        "empty_city": False,
        "values": values,
        "unread": [],
        "evidence": base_evidence,
    }


parse_city_intel = read_city_intel

__all__ = ["CITY_SCREENS", "CONTEXTS", "NUMERIC_FIELDS", "parse_city_intel", "read_city_intel"]
