"""MCP registration for the offline E35 army-target planner."""
from __future__ import annotations

from typing import Any

from san9mcp import registry
from san9mcp.target_unit import TARGET_TACTICS, plan_target_unit


@registry.tool(
    "san9_target_unit",
    group="E",
    tier="dev",
    status="stub",
    summary="离线部队目标选择器：伪报/扰乱/激励/救援共用；校验所在屏、锁定身份、滚动计划与结构化证据，不执行输入。",
    schema={
        "type": "object",
        "properties": {
            "observation": {"type": "object"},
            "target_name": {"type": "string"},
            "target_row": {"type": "integer"},
            "expect": {"description": "期望所在屏或字段"},
            "precondition": {"type": "object"},
            "actions": {"type": "array"},
            "scroll_plan": {"type": "array"},
            "tactic": {"type": "string", "enum": list(TARGET_TACTICS)},
        },
        "required": ["observation"],
    },
)
def san9_target_unit(**kwargs: Any) -> dict[str, Any]:
    return plan_target_unit(**kwargs)


__all__ = ["san9_target_unit"]
