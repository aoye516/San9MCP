# -*- coding: utf-8 -*-
"""san9mcp 的 MCP 协议层（stdio + 换行分隔 JSON-RPC 2.0）。

    python -m san9mcp.server
    SAN9_MCP_DEV=1 python -m san9mcp.server     # 连 dev 档工具一起暴露

接法（Claude Code / Codex / WorkBuddy 的 MCP 配置都一样）：

    {"mcpServers": {"san9": {
        "command": "python",
        "args": ["-m", "san9mcp.server"],
        "cwd": "C:/path/to/san9mcp"
    }}}

不依赖 `mcp` SDK —— 协议本身很简单，手写反而少一层版本坑。
本文件**只做协议**，一个业务逻辑都不放。
"""
from __future__ import annotations

import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from san9mcp import registry, runtime, scheduler  # noqa: E402
from san9mcp import tools  # noqa: E402,F401   —— 导入即注册

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "san9", "version": "0.2.0"}
SCHEDULER = scheduler.Scheduler()


# ---------------------------------------------------------------- 调用

def call_tool(name: str | None, args: dict | None) -> dict:
    """调一个工具。**任何异常都翻译成 `ok:false` + 原因**，绝不把栈丢给 agent。"""
    t = registry.by_name(name)
    if t is None:
        known = [x.name for x in registry.visible()]
        return runtime.fail("未知工具 %r" % name, available=known)
    if t.status != "ready":
        return runtime.fail("工具 %s 尚未实现（status=%s）" % (name, t.status))
    if t.tier == "dev" and not registry.dev_enabled():
        return runtime.fail(
            "dev 档工具默认不启用（%s=1 才开）" % registry.DEV_ENV,
            why_dev="这些是标定/调试用的，玩游戏的 agent 不需要，也不该用")
    try:
        # 多个 Codex CLI 可以各自启动 MCP server，但真正进入 handler 前必须
        # 共用 scheduler 的跨进程游戏锁，避免截图/按键/点击互相串屏。
        r = SCHEDULER.call(t.name, args or {}, t.fn)
        return r if isinstance(r, dict) else runtime.ok(value=r)
    except runtime.GameNotRunning as e:
        return runtime.fail(str(e), hint="调 san9_launch，或先手动启动游戏")
    except TypeError as e:
        return runtime.fail("参数不对：%s" % e, schema=t.schema)
    except Exception as e:
        return runtime.fail("%s: %s" % (type(e).__name__, e),
                            trace=traceback.format_exc()[-1500:])


# ---------------------------------------------------------------- 协议

def send(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle(req: dict) -> dict | None:
    mid = req.get("id")
    method = req.get("method")
    params = req.get("params") or {}

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
        }}
    if method in ("notifications/initialized", "initialized",
                  "notifications/cancelled"):
        return None                      # 通知没有回复
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid,
                "result": {"tools": [t.mcp() for t in registry.visible()]}}
    if method == "tools/call":
        try:
            result = call_tool(params.get("name"), params.get("arguments") or {})
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text",
                             "text": json.dumps(result, ensure_ascii=False, indent=2)}],
                "isError": not result.get("ok", False),
            }}
        except Exception as e:            # call_tool 兜不住才到这里
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text",
                             "text": "%s: %s\n%s" % (type(e).__name__, e,
                                                     traceback.format_exc())}],
                "isError": True,
            }}
    if mid is not None:
        return {"jsonrpc": "2.0", "id": mid,
                "error": {"code": -32601, "message": "不支持的方法 %s" % method}}
    return None


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            resp = handle(req)
        except Exception as e:
            resp = {"jsonrpc": "2.0", "id": req.get("id"),
                    "error": {"code": -32603, "message": "%s: %s" % (type(e).__name__, e)}}
        if resp is not None:
            send(resp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
