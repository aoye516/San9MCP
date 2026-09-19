# -*- coding: utf-8 -*-
"""Offline planner for the shared force-target selector (E34).

This module deliberately has no game/runtime/input dependency. It consumes a
structured observation supplied by an outer reader and returns either a
selection plan or a refusal. Candidate/unknown/unread rows are never promoted
to selectable identities.

The module is intentionally not imported by ``san9mcp.tools``. E34 can be
tested and used as a pure planner without changing the public registry or
touching the game.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
import unicodedata


SUPPORTED_COMMANDS = frozenset({
    "rumor", "gift", "request", "return", "persuade",
    "\u6d41\u8a00", "\u8d08\u8207", "\u8d60\u4e0e", "\u8acb\u6c42", "\u8bf7\u6c42",
    "\u4ea4\u9084", "\u4ea4\u8fd8", "\u52f8\u964d", "\u52f8\u964d",
})
SCREEN_KINDS = frozenset({
    "force_list", "target_force", "\u52bf\u529b\u5217\u8868", "\u52e2\u529b\u5217\u8868",
})
UNSELECTABLE_STATUSES = frozenset({"candidate", "unknown", "unread"})
LOCKED_STATUS = "locked"
_MISSING = object()


def _norm(value: Any) -> str:
    if value is None:
        return ""
    value = unicodedata.normalize("NFKC", str(value))
    return "".join(value.split())


def _fail(reason: str, *, evidence: Mapping[str, Any] | None = None, **extra: Any) -> dict:
    result = {"ok": False, "complete": False, "actions": [], "reason": reason}
    result["evidence"] = dict(evidence or {})
    result.update(extra)
    return result


def _screen(observation: Mapping[str, Any]) -> dict:
    raw = observation.get("screen")
    if isinstance(raw, Mapping):
        screen = dict(raw)
    else:
        screen = {"kind": raw}
    kind = screen.get("kind") or screen.get("type") or screen.get("name")
    return {"kind": kind, "title": screen.get("title"), "raw": screen}


def _page_number(page: Mapping[str, Any], fallback: int) -> int:
    value = page.get("page", page.get("index", fallback))
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _candidate_rows(page: Mapping[str, Any]) -> list[dict]:
    raw = page.get("candidates", page.get("rows", []))
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return []
    rows: list[dict] = []
    for position, item in enumerate(raw):
        row = dict(item) if isinstance(item, Mapping) else {"name": item}
        row.setdefault("row", position)
        row["status"] = _norm(row.get("status")).lower() or "unknown"
        row["page"] = _page_number(page, 0)
        rows.append(row)
    return rows


def _pages(observation: Mapping[str, Any]) -> list[dict]:
    raw_pages = observation.get("pages")
    if raw_pages is None:
        return [{"page": observation.get("page", 0),
                 "candidates": observation.get("candidates", observation.get("rows", [])),
                 "has_next": bool(observation.get("has_next", False))}]
    if isinstance(raw_pages, Mapping):
        raw_pages = [raw_pages[key] for key in sorted(raw_pages, key=str)]
    if not isinstance(raw_pages, Sequence) or isinstance(raw_pages, (str, bytes, bytearray)):
        return []
    result: list[dict] = []
    for index, raw in enumerate(raw_pages):
        page = dict(raw) if isinstance(raw, Mapping) else {"candidates": raw}
        page.setdefault("page", index)
        result.append(page)
    return result


def _current_page(observation: Mapping[str, Any], pages: list[dict]) -> int:
    value = observation.get("page", observation.get("current_page", 0))
    try:
        wanted = int(value)
    except (TypeError, ValueError):
        wanted = 0
    for index, page in enumerate(pages):
        if _page_number(page, index) == wanted:
            return index
    return -1


def _matches_expect(expect: Any, screen: dict, page: dict, rows: list[dict]) -> tuple[bool, str]:
    if expect is None:
        return True, "not_requested"
    if isinstance(expect, str):
        wanted = _norm(expect)
        actual = _norm(screen.get("kind") or screen.get("title"))
        return actual == wanted, "screen=%r actual=%r" % (wanted, actual)
    if not isinstance(expect, Mapping):
        return False, "expect must be a string or object"
    allowed = {"screen", "screen_kind", "page", "target", "target_name", "row"}
    unknown = sorted(set(expect) - allowed)
    if unknown:
        return False, "unsupported expect fields: %s" % ", ".join(unknown)
    checks: list[tuple[str, bool]] = []
    wanted_screen = expect.get("screen", expect.get("screen_kind", _MISSING))
    if wanted_screen is not _MISSING:
        actual = screen.get("kind") or screen.get("title")
        checks.append(("screen", _norm(actual) == _norm(wanted_screen)))
    if "page" in expect:
        expected_page = _safe_int(expect["page"])
        checks.append(("page", expected_page is not None and _page_number(page, 0) == expected_page))
    if "row" in expect:
        expected_row = _safe_int(expect["row"])
        checks.append(("row", expected_row is not None and any(_safe_int(r.get("row")) == expected_row for r in rows)))
    wanted_target = expect.get("target", expect.get("target_name", _MISSING))
    if wanted_target is not _MISSING:
        checks.append(("target", any(_norm(r.get("name")) == _norm(wanted_target) for r in rows)))
    failed = [name for name, passed in checks if not passed]
    return not failed, "ok" if not failed else "failed: %s" % ", ".join(failed)


def _check_precondition(precondition: Any, screen: dict, page: dict,
                        rows: list[dict]) -> tuple[bool, str]:
    if precondition is None:
        return True, "not_requested"
    if not isinstance(precondition, Mapping):
        return False, "precondition must be an object"
    allowed = {"screen", "screen_kind", "page", "nonempty", "min_candidates", "max_candidates"}
    unknown = sorted(set(precondition) - allowed)
    if unknown:
        return False, "unsupported precondition fields: %s" % ", ".join(unknown)
    checks: list[tuple[str, bool]] = []
    expected_screen = precondition.get("screen", precondition.get("screen_kind", _MISSING))
    if expected_screen is not _MISSING:
        checks.append(("screen", _norm(screen.get("kind") or screen.get("title")) == _norm(expected_screen)))
    if "page" in precondition:
        expected_page = _safe_int(precondition["page"])
        checks.append(("page", expected_page is not None and _page_number(page, 0) == expected_page))
    if "nonempty" in precondition:
        checks.append(("nonempty", bool(rows) == bool(precondition["nonempty"])))
    if "min_candidates" in precondition:
        checks.append(("min_candidates", len(rows) >= int(precondition["min_candidates"])))
    if "max_candidates" in precondition:
        checks.append(("max_candidates", len(rows) <= int(precondition["max_candidates"])))
    failed = [name for name, passed in checks if not passed]
    return not failed, "ok" if not failed else "failed: %s" % ", ".join(failed)


def _target_value(target: Any, row: int | None) -> tuple[str, Any]:
    if row is not None:
        return "row", row
    if isinstance(target, Mapping):
        if "row" in target:
            return "row", target["row"]
        if "name" in target:
            return "name", target["name"]
    if isinstance(target, bool):
        return "invalid", target
    if isinstance(target, int):
        return "row", target
    if isinstance(target, str):
        return "name", target
    return "invalid", target


def plan_target_force(command: str, observation: Mapping[str, Any], target: Any = None, *,
                      row: int | None = None, expect: Any = None,
                      precondition: Any = None, actions: Any = _MISSING) -> dict:
    """Plan one force selection without performing any input."""
    if not isinstance(observation, Mapping):
        return _fail("observation must be an object")
    if _norm(command) not in {_norm(x) for x in SUPPORTED_COMMANDS}:
        return _fail("unsupported command", command=command,
                     supported=sorted(SUPPORTED_COMMANDS))
    if actions is not _MISSING and (not isinstance(actions, Sequence) or not actions):
        return _fail("actions=[] is rejected: a target plan must contain at least one action",
                     command=command)
    screen = _screen(observation)
    evidence: dict[str, Any] = {"screen": screen, "command": command}
    if screen["kind"] not in SCREEN_KINDS:
        return _fail("screen gate rejected: current screen is not a force list",
                     evidence=evidence, expected_screens=sorted(SCREEN_KINDS))
    pages = _pages(observation)
    current_index = _current_page(observation, pages)
    if current_index < 0 or not pages:
        return _fail("current page is not present in the supplied observation", evidence=evidence)
    current_page = pages[current_index]
    current_rows = _candidate_rows(current_page)
    evidence["current_page"] = _page_number(current_page, current_index)
    evidence["current_rows"] = [dict(r) for r in current_rows]
    pre_ok, pre_detail = _check_precondition(precondition, screen, current_page, current_rows)
    evidence["precondition"] = {"requested": precondition is not None, "passed": pre_ok, "detail": pre_detail}
    if not pre_ok:
        return _fail("precondition rejected: %s" % pre_detail, evidence=evidence)
    exp_ok, exp_detail = _matches_expect(expect, screen, current_page, current_rows)
    evidence["expect"] = {"requested": expect is not None, "passed": exp_ok, "detail": exp_detail}
    if not exp_ok:
        return _fail("expect rejected: %s" % exp_detail, evidence=evidence)

    kind, value = _target_value(target, row)
    if kind == "invalid":
        return _fail("target must be a locked force name or an explicit row number", evidence=evidence)
    if kind == "name" and not _norm(value):
        return _fail("target force name must not be empty", evidence=evidence)
    try:
        requested_row = int(value) if kind == "row" else None
    except (TypeError, ValueError):
        return _fail("row target must be an integer", evidence=evidence)
    if requested_row is not None and requested_row < 0:
        return _fail("row target must be non-negative", evidence=evidence)

    found: dict | None = None
    found_index = -1
    for index, raw_page in enumerate(pages):
        rows = _candidate_rows(raw_page)
        for candidate in rows:
            candidate_row = _safe_int(candidate.get("row"))
            same = (candidate_row == requested_row) if requested_row is not None else (
                _norm(candidate.get("name")) == _norm(value))
            if same:
                if found is not None:
                    return _fail("target is ambiguous across supplied pages", evidence=evidence)
                found, found_index = candidate, index
    if found is None:
        return _fail("target is not present in the supplied force-list pages", evidence=evidence)
    evidence["target_candidate"] = dict(found)
    status = _norm(found.get("status")).lower()
    if status != LOCKED_STATUS:
        if status in UNSELECTABLE_STATUSES:
            why = "target status %r is not selectable" % status
        else:
            why = "target has no locked status; refusing to guess"
        return _fail(why, evidence=evidence, selectable_status=LOCKED_STATUS)

    actions_out: list[dict] = []
    if found_index != current_index:
        direction = "next" if found_index > current_index else "previous"
        step = 1 if found_index > current_index else -1
        for page_index in range(current_index + step, found_index + step, step):
            page = pages[page_index]
            actions_out.append({"op": "scroll_force_list", "direction": direction,
                                "from_page": _page_number(pages[page_index - step], page_index - step),
                                "to_page": _page_number(page, page_index)})
    actions_out.append({"op": "select_force", "page": _page_number(pages[found_index], found_index),
                        "row": int(found["row"]), "name": found.get("name"),
                        "status": LOCKED_STATUS})
    evidence["selection"] = {"method": kind, "locked": True,
                              "page": _page_number(pages[found_index], found_index),
                              "row": int(found["row"]), "name": found.get("name")}
    return {"ok": True, "complete": True, "command": command,
            "target": {"name": found.get("name"), "row": int(found["row"]),
                        "page": _page_number(pages[found_index], found_index)},
            "actions": actions_out, "evidence": evidence}


def san9_target_force(command: str, observation: Mapping[str, Any], target: Any = None, *,
                      row: int | None = None, expect: Any = None,
                      precondition: Any = None, actions: Any = _MISSING) -> dict:
    """Stable tool-shaped alias; remains offline-only and unregistered."""
    return plan_target_force(command, observation, target, row=row, expect=expect,
                             precondition=precondition, actions=actions)


__all__ = ["SUPPORTED_COMMANDS", "plan_target_force", "san9_target_force"]