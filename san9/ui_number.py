"""Safe, adapter-driven state machine for numeric game UI fields.

This module deliberately contains no game I/O.  A real adapter must provide
screen evidence, a keyboard replacement operation, and a second evidence read
for exact verification.  Until those facts are proven, the state machine
stops without attempting a fallback click or +/- sequence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any, Mapping, Protocol


class NumberInputError(ValueError):
    """Raised when a number-input request or its evidence is unsafe."""


class NumberInputState(str, Enum):
    INIT = "init"
    SCREEN_IDENTIFIED = "screen_identified"
    READY = "ready"
    INPUT_SENT = "input_sent"
    VERIFIED = "verified"
    STOPPED = "stopped"


_SCREEN_IDS = {
    "number_input",
    "numeric_input",
    "quantity_input",
    "amount_input",
}
_GUESS_MARKERS = (
    "guess",
    "guessed",
    "estimate",
    "estimated",
    "inferred",
    "猜",
    "推测",
    "估",
)
_INTEGER = re.compile(r"^[+-]?\d+$")


def _int_value(value: Any, field_name: str) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and _INTEGER.fullmatch(value.strip()):
        return int(value.strip())
    raise NumberInputError("%s 必须是整数证据" % field_name)


def _source_is_guess(source: Any, guessed: Any) -> bool:
    if guessed is True:
        return True
    if not isinstance(source, str):
        return False
    lowered = source.strip().lower()
    return any(marker in lowered for marker in _GUESS_MARKERS)


def _screen_is_identified(raw: Mapping[str, Any]) -> bool:
    if raw.get("screen_identified") is True:
        return True
    screen = raw.get("screen")
    if isinstance(screen, Mapping):
        if screen.get("identified") is True:
            return True
        screen = screen.get("kind") or screen.get("id")
    if not isinstance(screen, str):
        return False
    return screen.strip().lower() in _SCREEN_IDS


@dataclass(frozen=True)
class NumberEvidence:
    """One complete, exact observation of a numeric input screen."""

    screen_identified: bool
    current: int | None
    minimum: int | None
    maximum: int | None
    source: str | None
    exact: bool
    guessed: bool = False
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @property
    def complete(self) -> bool:
        return (
            self.screen_identified
            and self.current is not None
            and self.minimum is not None
            and self.maximum is not None
            and self.source is not None
            and self.exact
            and not self.guessed
        )


def read_number_evidence(value: NumberEvidence | Mapping[str, Any]) -> NumberEvidence:
    """Normalize adapter evidence without filling in missing facts.

    Aliases are accepted for adapter convenience, but absent values stay
    absent.  In particular, no OCR result is promoted to an exact value here.
    """
    if isinstance(value, NumberEvidence):
        return value
    if not isinstance(value, Mapping):
        raise NumberInputError("数字输入证据必须是对象")

    source = value.get("source")
    guessed = bool(value.get("guessed", False)) or _source_is_guess(source, False)
    current = _int_value(value.get("current", value.get("value")), "current")
    minimum = _int_value(value.get("minimum", value.get("min")), "minimum")
    maximum = _int_value(value.get("maximum", value.get("max")), "maximum")
    exact = bool(value.get("exact", value.get("verified", False)))
    complete_flag = value.get("complete")
    if complete_flag is False:
        exact = False
    evidence = NumberEvidence(
        screen_identified=_screen_is_identified(value),
        current=current,
        minimum=minimum,
        maximum=maximum,
        source=source if isinstance(source, str) and source.strip() else None,
        exact=exact,
        guessed=guessed,
        raw=value,
    )
    return evidence


def validate_evidence(evidence: NumberEvidence) -> None:
    """Reject anything that cannot support a safe numeric edit."""
    if not evidence.screen_identified:
        raise NumberInputError("未识别到数字输入屏，拒绝输入")
    if evidence.guessed:
        raise NumberInputError("证据来源是 OCR 猜值，拒绝输入")
    if not evidence.exact:
        raise NumberInputError("数字证据不完整或未精确确认，拒绝输入")
    if evidence.source is None:
        raise NumberInputError("缺少数字证据来源，拒绝输入")
    if evidence.current is None or evidence.minimum is None or evidence.maximum is None:
        raise NumberInputError("当前值或上下限证据不完整，拒绝输入")
    if evidence.minimum > evidence.maximum:
        raise NumberInputError("上下限证据无效，拒绝输入")
    if not evidence.minimum <= evidence.current <= evidence.maximum:
        raise NumberInputError("当前值超出已读上下限，拒绝输入")


def validate_target(target: Any) -> int:
    target_value = _int_value(target, "target")
    if target_value is None:
        raise NumberInputError("target 必须是整数")
    return target_value


@dataclass(frozen=True)
class NumberInputPlan:
    target: int
    evidence: NumberEvidence
    already_set: bool = False


def make_number_input_plan(
    evidence: NumberEvidence | Mapping[str, Any], target: Any
) -> NumberInputPlan:
    """Pure preflight: identify the screen and validate every numeric fact."""
    normalized = read_number_evidence(evidence)
    validate_evidence(normalized)
    target_value = validate_target(target)
    if not normalized.minimum <= target_value <= normalized.maximum:
        raise NumberInputError(
            "target=%d 超出范围 [%d, %d]" %
            (target_value, normalized.minimum, normalized.maximum)
        )
    return NumberInputPlan(
        target=target_value,
        evidence=normalized,
        already_set=normalized.current == target_value,
    )


class NumberInputAdapter(Protocol):
    """The only capabilities a proven real adapter must expose."""

    def read_number_evidence(self) -> NumberEvidence | Mapping[str, Any]:
        ...

    def keyboard_replace_number(self, value: int) -> None:
        ...


@dataclass
class NumberInputResult:
    ok: bool
    state: NumberInputState
    reason: str | None = None
    target: int | None = None
    before: NumberEvidence | None = None
    after: NumberEvidence | None = None
    method: str | None = None
    attempts: int = 0
    events: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        def evidence_dict(evidence: NumberEvidence | None) -> dict[str, Any] | None:
            if evidence is None:
                return None
            return {
                "screen_identified": evidence.screen_identified,
                "current": evidence.current,
                "minimum": evidence.minimum,
                "maximum": evidence.maximum,
                "source": evidence.source,
                "exact": evidence.exact,
                "guessed": evidence.guessed,
            }

        return {
            "ok": self.ok,
            "state": self.state.value,
            "reason": self.reason,
            "target": self.target,
            "before": evidence_dict(self.before),
            "after": evidence_dict(self.after),
            "method": self.method,
            "attempts": self.attempts,
            "events": list(self.events),
        }


def _stopped(
    reason: str,
    *,
    target: int | None = None,
    before: NumberEvidence | None = None,
    after: NumberEvidence | None = None,
    method: str | None = None,
    attempts: int = 0,
    events: list[str] | None = None,
) -> NumberInputResult:
    return NumberInputResult(
        ok=False,
        state=NumberInputState.STOPPED,
        reason=reason,
        target=target,
        before=before,
        after=after,
        method=method,
        attempts=attempts,
        events=events or [],
    )


def run_number_input(adapter: NumberInputAdapter, target: Any) -> NumberInputResult:
    """Execute one keyboard-first edit with exactly one post-write read.

    This function never retries.  If the adapter raises after dispatching a
    key, the result is still stopped because the actual UI state is unknown.
    """
    try:
        target_value = validate_target(target)
    except NumberInputError as exc:
        return _stopped(str(exc))

    events = [NumberInputState.INIT.value]
    try:
        before = read_number_evidence(adapter.read_number_evidence())
        validate_evidence(before)
    except (NumberInputError, AttributeError, TypeError, Exception) as exc:
        return _stopped("输入前证据拒绝：%s" % exc, target=target_value, events=events)

    events.append(NumberInputState.SCREEN_IDENTIFIED.value)
    try:
        plan = make_number_input_plan(before, target_value)
    except NumberInputError as exc:
        return _stopped(str(exc), target=target_value, before=before, events=events)

    events.append(NumberInputState.READY.value)
    if plan.already_set:
        events.append(NumberInputState.VERIFIED.value)
        return NumberInputResult(
            ok=True,
            state=NumberInputState.VERIFIED,
            target=target_value,
            before=before,
            after=before,
            method="none",
            attempts=0,
            events=events,
        )

    keyboard = getattr(adapter, "keyboard_replace_number", None)
    if not callable(keyboard):
        return _stopped(
            "没有已证明的键盘数字输入适配器，拒绝猜测或回退 +/-",
            target=target_value,
            before=before,
            events=events,
        )

    try:
        keyboard(target_value)
    except Exception as exc:
        return _stopped(
            "键盘输入失败，已停手且不重试：%s" % exc,
            target=target_value,
            before=before,
            method="keyboard",
            attempts=1,
            events=events + [NumberInputState.INPUT_SENT.value],
        )

    events.append(NumberInputState.INPUT_SENT.value)
    try:
        after = read_number_evidence(adapter.read_number_evidence())
        validate_evidence(after)
    except Exception as exc:
        return _stopped(
            "输入后精确复核失败，已停手且不重试：%s" % exc,
            target=target_value,
            before=before,
            method="keyboard",
            attempts=1,
            events=events,
        )

    if after.current != target_value:
        return _stopped(
            "输入后值为 %s，不等于目标 %d；已停手且不重试"
            % (after.current, target_value),
            target=target_value,
            before=before,
            after=after,
            method="keyboard",
            attempts=1,
            events=events,
        )

    events.append(NumberInputState.VERIFIED.value)
    return NumberInputResult(
        ok=True,
        state=NumberInputState.VERIFIED,
        target=target_value,
        before=before,
        after=after,
        method="keyboard",
        attempts=1,
        events=events,
    )


NumberInputStateMachine = run_number_input


__all__ = [
    "NumberEvidence",
    "NumberInputAdapter",
    "NumberInputError",
    "NumberInputPlan",
    "NumberInputResult",
    "NumberInputState",
    "NumberInputStateMachine",
    "make_number_input_plan",
    "read_number_evidence",
    "run_number_input",
    "validate_evidence",
    "validate_target",
]
