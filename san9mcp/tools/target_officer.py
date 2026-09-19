# -*- coding: utf-8 -*-
"""E33 officer target selector: pure parsing and declarative planning only."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

import importlib.util
from pathlib import Path
_path = Path(__file__).resolve().parents[2] / "san9" / "namelock.py"
_spec = importlib.util.spec_from_file_location("san9_namelock", _path)
namelock = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(namelock)

COMMANDS = {
    "登庸": "recruit", "登用": "recruit", "recruit": "recruit",
    "离间": "disaffect", "離間": "disaffect", "disaffect": "disaffect",
    "召来": "summon", "召來": "summon", "summon": "summon",
}
UNSAFE = frozenset(("candidate", "unknown", "unread"))


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _locked_source(items: Iterable[Any] | None):
    candidates, allowed, source = [], set(), "missing"
    for item in items or ():
        if isinstance(item, Mapping):
            status = _text(item.get("status")).lower()
            if "status" in item:
                source = "namelock_results"
                if status != "locked":
                    continue
                name = _text(item.get("locked") or item.get("name"))
                raw = _text(item.get("raw"))
                candidate = {"name": name, "aka": [raw] if raw else []}
            else:
                source = "locked_names"
                name = _text(item.get("name"))
                candidate = dict(item)
        else:
            source, name, candidate = "locked_names", _text(item), _text(item)
        if name:
            candidates.append(candidate)
            allowed.add(namelock.norm(name))
    return candidates, allowed, source

def parse_target_rows(rows, locked_names=None, *, current_page: int = 0):
    """Parse rows; only explicit namelock locked names are selectable."""
    candidates, allowed, source = _locked_source(locked_names)
    result = []
    for position, original in enumerate(rows or ()):
        if not isinstance(original, Mapping):
            continue
        row = deepcopy(dict(original))
        raw = _text(row.get("name_raw") or row.get("raw") or row.get("name"))
        declared = _text(row.get("name_status") or row.get("status")).lower()
        if row.get("unread") or declared == "unread" or not raw:
            lock = {"status": "unread", "locked": None, "raw": raw or None,
                    "why": "row name is unreadable"}
        elif declared in UNSAFE:
            lock = {"status": declared, "locked": None, "raw": raw,
                    "why": "row status is not selectable"}
        elif not candidates:
            lock = {"status": "unknown", "locked": None, "raw": raw,
                    "why": "namelock locked list is missing"}
        else:
            lock = namelock.lock(raw, candidates)
            resolved = namelock.norm(_text(lock.get("locked")))
            if lock.get("status") == "locked" and resolved not in allowed:
                lock = dict(lock, status="unknown", locked=None,
                            why="resolved name is absent from locked list")
        status = lock.get("status") or "unknown"
        name = lock.get("locked") if status == "locked" else None
        row.update({"row": _int(row.get("row", row.get("index")), position),
                    "page": _int(row.get("page", row.get("page_index")), current_page),
                    "raw": raw or None, "name_raw": raw or None,
                    "name": name, "name_status": status,
                    "selectable": bool(name and namelock.norm(_text(name)) in allowed),
                    "lock": lock, "namelock_source": source})
        result.append(row)
    return result


def _path_get(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if isinstance(current, Mapping):
            current = current.get(part)
        elif isinstance(current, (list, tuple)) and part.isdigit():
            index = int(part)
            current = current[index] if index < len(current) else None
        else:
            return None
    return current


def _matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, Mapping):
        return isinstance(actual, Mapping) and all(
            _matches(actual.get(key), value) for key, value in expected.items())
    if isinstance(expected, (list, tuple, set, frozenset)):
        return actual in expected
    return actual == expected


def _gate(gate: Any, state: Mapping[str, Any]):
    if gate is None:
        return True, "not supplied"
    if callable(gate):
        try:
            answer = bool(gate(state))
            return answer, "callable=%s" % answer
        except Exception as exc:
            return False, "%s: %s" % (type(exc).__name__, exc)
    if isinstance(gate, Mapping):
        failures = []
        for path, expected in gate.items():
            actual = _path_get(state, str(path))
            if not _matches(actual, expected):
                failures.append({"path": path, "expected": expected, "actual": actual})
        return not failures, failures or "matched"
    return bool(gate), "boolean=%s" % bool(gate)

def _scroll_plan(scroll: Any, current_page: int, target_page: int):
    if target_page == current_page:
        return [], None
    distance = abs(target_page - current_page)
    direction = "next" if target_page > current_page else "previous"
    if scroll is None:
        return None, "target page requires a declared scroll plan"
    if isinstance(scroll, Mapping):
        given = _text(scroll.get("direction") or direction).lower()
        aliases = {"next": "next", "forward": "next", "+": "next",
                   "previous": "previous", "prev": "previous", "back": "previous", "-": "previous"}
        if aliases.get(given) != direction:
            return None, "declared scroll direction cannot reach target page"
        maximum = _int(scroll.get("max", scroll.get("max_pages", distance)), -1)
        if maximum < distance:
            return None, "target page exceeds declared scroll limit"
        action = dict(scroll)
        action.pop("max", None)
        action.pop("max_pages", None)
        action.update({"action": "scroll", "direction": direction, "count": distance})
        return [action], None
    if isinstance(scroll, (list, tuple)):
        actions = [deepcopy(item) for item in scroll if isinstance(item, Mapping)]
        if len(actions) < distance:
            return None, "declared scroll plan is too short"
        return actions[:distance], None
    return None, "scroll must be a mapping or action list"


@dataclass
class OfficerTargetState:
    phase: str = "idle"
    current_page: int = 0
    rows: list[dict[str, Any]] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    def inspect(self, rows, locked_names):
        self.rows = parse_target_rows(rows, locked_names, current_page=self.current_page)
        self.phase = "inspected"
        self.evidence = {"rows": deepcopy(self.rows),
                         "pages": sorted({row["page"] for row in self.rows})}
        return deepcopy(self.evidence)

    def reject(self, reason: str, failed_at: str):
        self.phase, self.actions = "rejected", []
        self.evidence["failed_at"] = failed_at
        return {"ok": False, "why": reason, "failed_at": failed_at,
                "actions": [], "action_count": 0,
                "evidence": deepcopy(self.evidence)}


def select_officer_target(command: str, target: str, rows, *,
                          locked_names=None, namelock_locked=None,
                          current_page: int = 0, scroll=None,
                          expect=None, precondition=None):
    """Return a safe plan for 登庸/离间/召来, never perform input."""
    if locked_names is None:
        locked_names = namelock_locked
    state = OfficerTargetState(current_page=_int(current_page, 0))
    evidence = state.inspect(rows or (), locked_names)
    kind = COMMANDS.get(_text(command))
    target_text = _text(target)
    context = {"command": command, "command_kind": kind, "target": target_text,
               "current_page": state.current_page, "rows": evidence["rows"],
               "pages": evidence["pages"]}
    state.evidence.update({"command": command, "command_kind": kind,
                           "target": target_text})
    if kind is None:
        return state.reject("unsupported officer target command", "command")
    if not target_text:
        return state.reject("target must not be empty", "target")
    for label, gate in (("expect", expect), ("precondition", precondition)):
        ok, detail = _gate(gate, context)
        state.evidence[label] = {"ok": ok, "detail": detail}
        if not ok:
            return state.reject("%s gate rejected target plan" % label, label)
    wanted = namelock.norm(target_text)
    matches = [row for row in evidence["rows"]
               if row.get("selectable") and namelock.norm(_text(row.get("name"))) == wanted]
    if not matches:
        return state.reject("target is not a locked namelock row", "target_row")
    if len(matches) != 1:
        return state.reject("target has multiple locked rows", "target_row")
    row = matches[0]
    actions, why = _scroll_plan(scroll, state.current_page, row["page"])
    if actions is None:
        return state.reject(why, "scroll")
    actions.append({"action": "click_row", "command": kind,
                    "page": row["page"], "row": row["row"], "target": row["name"]})
    if not actions:
        return state.reject("zero-action plan rejected", "actions")
    state.phase, state.actions = "planned", actions
    state.evidence.update({"selected_row": deepcopy(row), "target_page": row["page"],
                           "action_count": len(actions), "source": "namelock"})
    return {"ok": True, "command": command, "command_kind": kind,
            "target": row["name"], "actions": deepcopy(actions),
            "action_count": len(actions), "evidence": deepcopy(state.evidence)}


def san9_target_officer(command: str, target: str, rows, **kwargs):
    return select_officer_target(command, target, rows, **kwargs)


__all__ = ["COMMANDS", "OfficerTargetState", "parse_target_rows",
           "select_officer_target", "san9_target_officer"]

