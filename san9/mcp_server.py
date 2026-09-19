"""MCP server：把 san9 工具暴露成标准 MCP 工具，任何支持 MCP 的 agent 都能接。

    python -m san9.mcp_server

Claude Code 接法（.mcp.json）：
    {"mcpServers": {"san9": {
        "command": "python",
        "args": ["-m", "san9.mcp_server"],
        "cwd": "C:/path/to/san9mcp"
    }}}

Codex 接法同理，写进它的 MCP 配置即可。

不依赖 mcp SDK —— 用的是 stdio + 换行分隔的 JSON-RPC 2.0，
协议本身很简单，手写反而少一层版本坑。
"""
from __future__ import annotations

import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from san9 import actions as A  # noqa: E402

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "san9", "version": "0.2.1"}

_session = None


def get_session():
    global _session
    if _session is None:
        from san9.session import GameSession

        _session = GameSession(mode=os.environ.get("SAN9_MODE", "fair"))
        _session.attach()
    return _session


TOOLS = [
    {
        "name": "san9_status",
        "description": "环境体检：游戏是否运行、窗口是否标准 1024x768、当前在哪个界面、"
                       "已标定的界面和路径有哪些、存档情况。开局先调这个。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "san9_obs",
        "description": "取当前观测：旬数、当前界面、待处理的弹窗、近期事件、世界状态摘要。"
                       "长流程里默认用 brief；只有开局和每几十旬重新规划时才用 full。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {"type": "string", "enum": ["brief", "full"], "default": "brief"},
                "with_shot": {"type": "boolean", "default": False,
                              "description": "是否附一张截图路径（多模态模型可以用）"},
            },
        },
    },
    {
        "name": "san9_act",
        "description": "执行一批语义动作（不是鼠标坐标）。例如 "
                       "[{\"op\":\"city.patrol\",\"city\":\"洛陽\",\"officers\":[\"張角\"]}]。"
                       "逐条返回审计结果，ok=false 表示没生效。用 san9_docs 查全部动作。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "actions": {"type": "array", "items": {"type": "object"},
                            "description": "动作对象数组"},
            },
            "required": ["actions"],
        },
    },
    {
        "name": "san9_end_turn",
        "description": "推进 n 旬。工具会自动处理时间流动、战斗报告、演出等所有插入事件，"
                       "直到回到可以下命令的战略面。如果中途遇到需要你决策的弹窗，"
                       "会停下来返回 stopped_at 和 question，你处理完再调一次。",
        "inputSchema": {
            "type": "object",
            "properties": {"n": {"type": "integer", "default": 1, "minimum": 1}},
        },
    },
    {
        "name": "san9_shot",
        "description": "截一张游戏画面存到文件，返回路径与识别到的界面名。给多模态模型看。",
        "inputSchema": {
            "type": "object",
            "properties": {"tag": {"type": "string", "default": ""}},
        },
    },
    {
        "name": "san9_docs",
        "description": "动作目录：所有可用 op、参数、以及阵形数值表。规划前调一次。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "san9_launch",
        "description": "启动游戏（走启动器，直接跑 San9WPK.exe 会立刻退出）。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "san9_snapshot",
        "description": "把当前自动存档复制成检查点。长流程里建议每几十旬存一次。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "san9_rollback",
        "description": "回滚到某个检查点（用于试验失败后重来）。",
        "inputSchema": {
            "type": "object",
            "properties": {"to_turn": {"type": "integer", "description": "目标旬号"}},
        },
    },
]


def call_tool(name: str, args: dict) -> dict:
    if name == "san9_docs":
        return A.catalogue()
    if name == "san9_status":
        from san9 import winio

        running = winio.find_all_game_processes("San9")
        win = winio.find_game("San9WPK.exe")
        info = {
            "game_processes": [{"pid": p, "name": n} for p, n in running],
            "window": None,
        }
        if win:
            l, t, w, h = winio.client_rect_screen(win.hwnd)
            info["window"] = {"hwnd": f"0x{win.hwnd:08X}", "client_origin": [l, t],
                              "client_size": [w, h],
                              "standard_1024x768": (w, h) == (1024, 768)}
        try:
            from san9 import driver, vision

            info["paths_calibrated"] = sorted(driver.PathTable().data)
            info["screens_learned"] = sorted(vision.ScreenBook().entries)
        except Exception as e:
            info["calibration_error"] = str(e)
        return info

    s = get_session()
    if name == "san9_launch":
        return {"ok": s.launch(), "hwnd": f"0x{s.hwnd:08X}" if s.hwnd else None}
    if name == "san9_obs":
        return s.observe(args.get("scope", "brief"), bool(args.get("with_shot")))
    if name == "san9_act":
        return {"results": s.act(args.get("actions") or [])}
    if name == "san9_end_turn":
        return s.end_turn(int(args.get("n", 1)))
    if name == "san9_shot":
        return {"ok": True, "path": s.shot(args.get("tag") or "mcp"),
                "screen": s.screen()}
    if name == "san9_snapshot":
        return {"ok": True, "path": s.snapshot("mcp")}
    if name == "san9_rollback":
        return s.rollback(args.get("to_turn"))
    raise ValueError(f"未知工具 {name}")


def send(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        mid = req.get("id")
        method = req.get("method")
        params = req.get("params") or {}

        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            }})
        elif method in ("notifications/initialized", "initialized"):
            continue
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            try:
                result = call_tool(params.get("name"), params.get("arguments") or {})
                send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text",
                                 "text": json.dumps(result, ensure_ascii=False, indent=2)}],
                    "isError": False,
                }})
            except Exception as e:
                send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text": f"{type(e).__name__}: {e}\n"
                                 f"{traceback.format_exc()}"}],
                    "isError": True,
                }})
        elif mid is not None:
            send({"jsonrpc": "2.0", "id": mid,
                  "error": {"code": -32601, "message": f"不支持的方法 {method}"}})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
