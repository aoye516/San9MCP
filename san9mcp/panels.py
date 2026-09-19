# -*- coding: utf-8 -*-
"""「面板编辑会话」状态机 —— 让**一个工具可以多轮输入**。

## 为什么要这个

有些操作 agent 没法一次把参数填齐：它得**先看到可选项**才能决定。
最典型的是「选派哪些武将」—— 名单每旬都在变，agent 不看一眼没法填。

做法不是"再多一个工具"，而是：**第一轮返回可选项 + 一个 session，第二轮带着
session 补参数**。服务端持有中间状态，agent 只记住一个字符串。

（这就是 README 第三节的「降级模型」；配套的硬规则是「绝不半执行」——
参数不齐时不硬做，只返回缺什么。）

## 两个必须守住的点

1. **session 会过期。** 换旬（`end_turn`）必须清空 —— 否则 agent 拿着上一旬
   的 session 回来，面对的已经是另一个世界了。超时也清。
2. **session 只记"该记的"**：工具名 + hwnd + 参数，**不记屏幕状态**。
   屏幕状态每次都要**重新验证**（"选将弹窗开着吗"），不能假设它还开着。
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

TTL_SECONDS = 300.0
"""超过这个时间没续上就作废。"""


@dataclass
class Session:
    sid: str
    tool: str
    hwnd: int
    created: float
    data: dict = field(default_factory=dict)

    def age(self) -> float:
        return time.time() - self.created


_SESSIONS: dict[str, Session] = {}


def open_(tool: str, hwnd: int, **data) -> str:
    sweep()
    sid = "%s-%s" % (tool.replace("san9_", "")[:8], uuid.uuid4().hex[:6])
    _SESSIONS[sid] = Session(sid=sid, tool=tool, hwnd=hwnd,
                            created=time.time(), data=dict(data))
    return sid


def get(sid: str | None) -> Session | None:
    """取 session。**找不到/过期/窗口换了就返回 None** —— 调用方必须重新开一轮。"""
    if not sid:
        return None
    sweep()
    s = _SESSIONS.get(sid)
    if s is None:
        return None
    return s


def close(sid: str | None) -> None:
    if sid:
        _SESSIONS.pop(sid, None)


def clear_all(reason: str = "") -> int:
    """清空所有 session。**换旬时必须调** —— 见模块注释第 1 点。"""
    n = len(_SESSIONS)
    _SESSIONS.clear()
    return n


def sweep() -> int:
    dead = [k for k, s in _SESSIONS.items() if s.age() > TTL_SECONDS]
    for k in dead:
        _SESSIONS.pop(k, None)
    return len(dead)


def live() -> list[dict]:
    sweep()
    return [{"sid": s.sid, "tool": s.tool, "age": round(s.age(), 1),
             "data": s.data} for s in _SESSIONS.values()]
