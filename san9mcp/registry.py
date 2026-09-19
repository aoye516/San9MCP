# -*- coding: utf-8 -*-
"""工具注册表：名字 → schema + handler + 分档 + 状态。

**tier**（分档）
- `play` 玩用（列表里的 P 档）
- `both` 玩和探索都用（B 档）
- `dev`  **仅开发期**，默认**不注册**，需 `SAN9_MCP_DEV=1` 才出现（X 档）

**status**（状态）
- `ready` 能跑
- `stub`  占位：**不进 `tools/list`**（免得 agent 调到坏东西），但 `san9_docs` 里会列出来

`tools/list` 只暴露 `ready` 且 tier 允许的工具 —— 让 agent 看到的每个工具都能用。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable

DEV_ENV = "SAN9_MCP_DEV"


@dataclass
class Tool:
    name: str
    group: str
    tier: str
    status: str
    summary: str
    schema: dict
    fn: Callable | None = None
    needs: list = field(default_factory=list)

    def mcp(self) -> dict:
        desc = self.summary
        if self.status != "ready":
            desc = "⚠️ 尚未实现 —— %s" % desc
        return {
            "name": self.name,
            "description": desc,
            "inputSchema": self.schema or {"type": "object", "properties": {}},
        }

    def row(self) -> dict:
        return {"name": self.name, "group": self.group, "tier": self.tier,
                "status": self.status, "summary": self.summary}


REGISTRY: list[Tool] = []


def tool(name: str, group: str, summary: str,
         schema: dict | None = None,
         tier: str = "play", status: str = "ready",
         needs: list | None = None):
    """把一个函数登记成 MCP 工具。

    用法：
        @registry.tool("san9_status", group="A", summary="体检……")
        def san9_status():
            ...
    """
    def deco(fn):
        if any(t.name == name for t in REGISTRY):
            raise ValueError("工具名重复：%s" % name)
        REGISTRY.append(Tool(
            name=name, group=group, tier=tier, status=status, summary=summary,
            schema=schema or {"type": "object", "properties": {}},
            fn=fn, needs=needs or []))
        return fn
    return deco


def dev_enabled() -> bool:
    return os.environ.get(DEV_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def visible() -> list[Tool]:
    """能进 `tools/list` 的：状态 ready，且 dev 档需显式开启。"""
    return [t for t in REGISTRY
            if t.status == "ready" and (t.tier != "dev" or dev_enabled())]


def by_name(name: str | None) -> Tool | None:
    if not name:
        return None
    for t in REGISTRY:
        if t.name == name:
            return t
    return None


def catalogue() -> dict:
    """给 `san9_docs` 用的总目录（含**未实现**的，方便 agent 知道边界在哪）。"""
    groups: dict = {}
    for t in REGISTRY:
        groups.setdefault(t.group, []).append(t.row())
    return {
        "tools": len(REGISTRY),
        "ready": sum(1 for t in REGISTRY if t.status == "ready"),
        "stub": sum(1 for t in REGISTRY if t.status != "ready"),
        "dev_enabled": dev_enabled(),
        "groups": {k: groups[k] for k in sorted(groups)},
    }
