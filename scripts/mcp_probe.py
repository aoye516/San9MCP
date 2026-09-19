# -*- coding: utf-8 -*-
"""MCP 探针 —— 把 JSON-RPC 打进 `san9mcp.server`，打印回复。

用途：**不开 agent 也能测 MCP**。每次改完工具，先用它确认协议层和服务端都活着，
再谈业务对不对。这是我们自己的测试工具，不属于工具包（不进 tools/）。

用法：
    python scripts/mcp_probe.py list
    python scripts/mcp_probe.py list --dev
    python scripts/mcp_probe.py call san9_status
    python scripts/mcp_probe.py call san9_look --args '{"scope":"full"}'
    python scripts/mcp_probe.py call san9_journal --timeout 300
    python scripts/mcp_probe.py raw                 # 只回 initialize + tools/list
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "mcp_probe", "version": "1"}}}
INITED = {"jsonrpc": "2.0", "method": "notifications/initialized"}
LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}


def call_req(name: str, args: dict, mid: int = 3) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "method": "tools/call",
            "params": {"name": name, "arguments": args}}


def run(reqs: list, timeout: float = 240, dev: bool = False):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    if dev:
        env["SAN9_MCP_DEV"] = "1"
    payload = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in reqs)
    p = subprocess.run([PY, "-X", "utf8", "-m", "san9mcp.server"],
                       cwd=ROOT, input=payload, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout, env=env)
    resps = []
    for line in (p.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            resps.append(json.loads(line))
        except json.JSONDecodeError:
            resps.append({"_raw": line})
    return resps, (p.stderr or "")


def _dump(obj, limit: int = 6000) -> str:
    s = json.dumps(obj, ensure_ascii=False, indent=1)
    return s if len(s) <= limit else s[:limit] + "\n  …(截断，共 %d 字符)" % len(s)


def main(argv: list) -> int:
    if not argv:
        print(__doc__)
        return 1
    mode = argv[0]
    dev = "--dev" in argv

    if mode == "raw":
        resps, err = run([INIT, INITED, LIST], dev=dev)
        for r in resps:
            print(_dump(r))
        if err.strip():
            print("--- stderr ---\n" + err[-800:], file=sys.stderr)
        return 0

    if mode == "list":
        resps, err = run([INIT, INITED, LIST], dev=dev)
        tools = []
        for r in resps:
            if isinstance(r.get("result"), dict) and "tools" in r["result"]:
                tools = r["result"]["tools"]
        if not tools:
            print("!! 没拿到 tools/list，原始回复：")
            for r in resps:
                print(_dump(r))
            return 1
        print("暴露 %d 个工具%s：" % (len(tools), "（dev 开）" if dev else ""))
        for t in tools:
            print("  %-24s %s" % (t["name"], t["description"].split("。")[0][:52]))
        return 0

    if mode == "call":
        if len(argv) < 2:
            print("需要工具名：call <name> [--args JSON]")
            return 1
        name = argv[1]
        args = {}
        if "--args" in argv:
            args = json.loads(argv[argv.index("--args") + 1])
        timeout = 240
        if "--timeout" in argv:
            timeout = float(argv[argv.index("--timeout") + 1])
        resps, err = run([INIT, INITED, call_req(name, args)], timeout=timeout, dev=dev)
        hit = None
        for r in resps:
            if r.get("id") == 3:
                hit = r
        if hit is None:
            print("!! 没有拿到 tools/call 的回复。原始：")
            for r in resps:
                print(_dump(r))
            if err.strip():
                print("--- stderr ---\n" + err[-1200:], file=sys.stderr)
            return 1
        res = hit.get("result") or {}
        err_flag = res.get("isError")
        print("isError = %s" % err_flag)
        for c in res.get("content") or []:
            if c.get("type") == "text":
                print(c["text"])
        if hit.get("error"):
            print("JSON-RPC error:", _dump(hit["error"]))
        if err.strip():
            print("--- stderr ---\n" + err[-800:], file=sys.stderr)
        return 0

    print("未知模式 %r" % mode)
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
