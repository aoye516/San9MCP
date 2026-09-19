"""探索结果暂存（**待校验区**）。

为什么有这个模块
----------------
上一轮我边探索边往 `docs/05-操作知识库.md` / `.workbuddy/memory/MEMORY.md` 写结论，
写错过两次（"点击必须发真实鼠标移动"被对照实验推翻；"武将表格要点一下"是误判）。

用户要求：**本轮探索结果先不进知识库**，集中存到一处，交给另一个模型核验，
通过之后再入库。所以这里只负责**记下"结论 + 怎么测的 + 证据"**，状态一律 unverified。

落盘位置
--------
`C:\\Users\\luj05\\san9ai\\explore\\`（纯 ASCII 数据目录）
  findings.jsonl   一行一条发现
  shots/           带标尺截图
  uimap.json       六大类命令的界面采集
  ocr/             OCR 原文
"""

from __future__ import annotations

import json
import os
import time

from . import paths

ROOT = os.path.join(paths.DATA, "explore")
SHOTS = os.path.join(ROOT, "shots")
OCRD = os.path.join(ROOT, "ocr")
LOGS = os.path.join(ROOT, "logs")
FINDINGS = os.path.join(ROOT, "findings.jsonl")
UIMAP = os.path.join(ROOT, "uimap.json")

for _d in (ROOT, SHOTS, OCRD, LOGS):
    os.makedirs(_d, exist_ok=True)

def _next_id() -> str:
    """从已有文件续号 —— 模块级的计数器每次进程重启都会归零，会撞号（踩过）。"""
    n = 0
    if os.path.exists(FINDINGS):
        with open(FINDINGS, encoding="utf-8") as f:
            n = sum(1 for ln in f if ln.strip())
    return f"F-{n + 1:03d}"


def add(claim: str, method: str, area: str = "general",
        evidence: dict | None = None, status: str = "unverified",
        review: str = "", review_by: str = "") -> dict:
    """记一条发现。返回写进去的那条记录。"""
    rec = {
        "id": _next_id(),
        "t": time.strftime("%H:%M:%S"),
        "area": area,
        "claim": claim,
        "method": method,
        "evidence": evidence or {},
        "status": status,               # unverified / inferred / verified / rejected
        "review": review,
        "review_by": review_by,
    }
    with open(FINDINGS, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def load() -> list[dict]:
    if not os.path.exists(FINDINGS):
        return []
    with open(FINDINGS, encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def dump_ocr(tag: str, lines: list[str]) -> str:
    """把一次 OCR 的原文存下来（核验时能对照）。"""
    p = os.path.join(OCRD, f"{tag}.txt")
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return p
