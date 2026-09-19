"""Offline planner implementation for E35 army-target selection."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

TARGET_TACTICS = ("伪报", "扰乱", "激励", "救援")
_FORBIDDEN_STATUS = {"candidate", "unknown", "unread"}
_LOCKED_STATUS = {"locked", "confirmed", "verified", "exact", "explicit"}
_SCREEN_NAMES = {"target_unit", "unit_target", "army_target", "command_target_unit", "tactic_target_unit"}


def _as_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _status(value: Any) -> str:
    return _as_text(value).lower()


def _first(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _screen_name(observation: dict[str, Any]) -> str:
    screen = observation.get("screen")
    if isinstance(screen, dict):
        screen = _first(screen, "name", "kind", "id", "screen_id")
    return _as_text(_first(observation, "screen_name", "screen_id")) or _as_text(screen)


def _rows(observation: dict[str, Any]) -> list[dict[str, Any]]:
    rows = _first(observation, "rows", "units", "candidates")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _explicit_identity(row: dict[str, Any]) -> bool:
    identity = _first(row, "identity", "unit_id", "army_id", "target_id")
    if identity in (None, "", [], {}):
        return False
    return _status(_first(row, "identity_status", "id_status")) not in _FORBIDDEN_STATUS


def _locked(row: dict[str, Any], keys: tuple[str, ...]) -> bool:
    value = _first(row, *keys)
    return _status(value) in _LOCKED_STATUS or _explicit_identity(row)


def _failure(reason: str, evidence: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {"ok": False, "reason": reason, "evidence": evidence, "plan": None, **extra}


def _gate(observation: dict[str, Any], spec: Any, label: str) -> str | None:
    if spec is None:
        return None
    if isinstance(spec, str):
        return None if _screen_name(observation) == spec else "%s screen mismatch" % label
    if not isinstance(spec, dict):
        return "%s must be a string or object" % label
    for key, expected in spec.items():
        actual = _screen_name(observation) if key in ("screen", "screen_name", "screen_id") else observation.get(key)
        if isinstance(expected, (list, tuple, set)):
            if actual not in expected:
                return "%s %s mismatch" % (label, key)
        elif actual != expected:
            return "%s %s mismatch" % (label, key)
    return None


def plan_target_unit(observation: dict[str, Any], *, target_name: str | None = None,
                     target_row: int | None = None, expect: Any = None,
                     precondition: Any = None, actions: list[dict[str, Any]] | None = None,
                     scroll_plan: list[dict[str, Any]] | None = None,
                     tactic: str | None = None) -> dict[str, Any]:
    evidence: list[dict[str, Any]] = []
    if not isinstance(observation, dict):
        return _failure("observation must be an object", evidence)
    if actions == []:
        return _failure("actions=[] is rejected: zero-action plan", evidence)
    if actions is not None and (not isinstance(actions, list) or not all(isinstance(x, dict) for x in actions)):
        return _failure("actions must be a list of objects", evidence)
    if scroll_plan is not None and (not isinstance(scroll_plan, list) or not all(isinstance(x, dict) for x in scroll_plan)):
        return _failure("scroll_plan must be a list of objects", evidence)
    if tactic is not None and tactic not in TARGET_TACTICS:
        return _failure("unsupported army-target tactic", evidence, tactic=tactic)
    screen = _screen_name(observation)
    ok = screen in _SCREEN_NAMES
    evidence.append({"kind": "screen_gate", "status": "pass" if ok else "fail", "observed": screen})
    if not ok:
        return _failure("所在屏不满足部队目标选择器门禁", evidence)
    for label, spec in (("expect", expect), ("precondition", precondition)):
        error = _gate(observation, spec, label)
        evidence.append({"kind": label, "status": "pass" if error is None else "fail", "detail": error})
        if error:
            return _failure(error, evidence)
    rows = _rows(observation)
    if target_name is None and target_row is None:
        return _failure("必须提供 target_name 或 target_row", evidence)
    matches = []
    for row in rows:
        name = _first(row, "name", "target_name", "unit_name")
        rv = _first(row, "row", "index", "row_index")
        if (target_name is None or name == target_name) and (target_row is None or rv == target_row):
            matches.append(row)
    if len(matches) != 1:
        return _failure("目标名/行未唯一锁定", evidence, match_count=len(matches))
    target = matches[0]
    statuses = {_status(_first(target, "name_status", "row_status", "target_status", "status")),
                _status(_first(target, "identity_status", "id_status"))}
    if statuses & _FORBIDDEN_STATUS:
        return _failure("candidate/unknown/unread 部队禁止选择", evidence, forbidden=sorted(statuses & _FORBIDDEN_STATUS))
    name_ok = target_name is None or _locked(target, ("name_status", "target_status", "status"))
    row_ok = target_row is None or _locked(target, ("row_status", "target_status", "status"))
    evidence.append({"kind": "identity", "status": "pass" if name_ok and row_ok else "fail",
                     "name_locked": name_ok, "row_locked": row_ok})
    if not name_ok or not row_ok:
        return _failure("目标名/行必须 locked 或有明确身份", evidence)
    scroll = [deepcopy(x) for x in (scroll_plan or [])]
    observed_page = _first(observation, "page", "current_page")
    target_page = _first(target, "page", "target_page")
    if not scroll and observed_page is not None and target_page is not None and observed_page != target_page:
        try:
            delta = int(target_page) - int(observed_page)
        except (TypeError, ValueError):
            return _failure("页码证据不可解析，拒绝猜测滚动方向", evidence)
        if delta:
            scroll.append({"op": "scroll", "direction": "next" if delta > 0 else "previous",
                           "pages": abs(delta), "from_page": observed_page, "to_page": target_page})
    plan = scroll + [{"op": "select_unit", "row": target.get("row", target.get("index")),
                      "name": _first(target, "name", "target_name", "unit_name")}]
    plan.extend(deepcopy(actions or []))
    evidence.append({"kind": "target_match", "status": "pass", "row": target.get("row", target.get("index"))})
    evidence.append({"kind": "scroll_plan", "status": "pass", "actions": scroll})
    return {"ok": True, "target": deepcopy(target), "plan": plan, "evidence": evidence, "tactic": tactic}


__all__ = ["TARGET_TACTICS", "plan_target_unit"]
