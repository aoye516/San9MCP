# -*- coding: utf-8 -*-
"""san9mcp 共享运行时：窗口 · 前台化 · 截图 · 证据信封。

## 边界铁律

本包（`san9mcp/`）只做**翻译与校验**。截图 / OCR / 点击 / 菜单逻辑**全部在 `san9/` 里**。
不许在这里重写一遍 —— 否则会变成两份实现，早晚不一致。

这里只负责四件事：
1. 拿到游戏窗口（拿不到就抛 `GameNotRunning`，这是**正常失败路径**，不是崩溃）
2. 切前台（**屏幕区域截图的前提**：拍的是屏幕，游戏不在前台就拍到别的东西）
3. 存图（走 `paths.store()` 存**相对路径**）
4. 统一的 `ok()` / `fail()` 信封 —— **失败必须带明确原因，绝不允许静默**
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from san9 import paths, winio  # noqa: E402

EXE = "San9WPK.exe"
SCREEN = "strategy"
SHOT_DIR = os.path.join(paths.DATA, "mcp", "shots")

_seq = [0]


# ---------------------------------------------------------------- 窗口

class GameNotRunning(RuntimeError):
    """游戏没在跑。调用方应把它翻译成 `ok:false` + 一条明确的 hint。"""


def window():
    w = winio.find_game(EXE)
    if w is None:
        raise GameNotRunning(
            "找不到游戏窗口（%s）。先调 san9_launch，或手动启动游戏后重试" % EXE)
    return w


def ensure_foreground(w, settle: float = 0.45) -> bool:
    """把游戏切到前台。

    ⛔ **不是可选的优化**：截图一律走"屏幕区域拷贝"（见 `grab` 的说明），
    拿的是**屏幕像素**。游戏不在最前面，拍到的就是别的窗口。
    """
    try:
        if winio.is_foreground(w.hwnd):
            return True
        winio.focus(w.hwnd)
        time.sleep(settle)
        return bool(winio.is_foreground(w.hwnd))
    except Exception:
        return False


def grab(w):
    """屏幕区域截图。

    ⛔ **绝不用 `use_window_dc=True`** —— 那是另一套坐标空间：
    实测同一矩形下平均色差 54，同一个「進行」按钮在两套空间里分别是
    (1218,865) 和 (975,696)。用它截图会让所有点击错位 ——
    这就是"点了没反应"的根因。
    """
    return winio.grab(w.hwnd, use_window_dc=False)


def save_shot(w, tag: str = "shot") -> str:
    """存一张图，返回**相对 DATA 的路径**（JSON 里一律存相对路径，整个数据目录能搬走）。"""
    os.makedirs(SHOT_DIR, exist_ok=True)
    gw, gh, buf = grab(w)
    _seq[0] += 1
    p = os.path.join(SHOT_DIR, "%04d_%s.png" % (_seq[0], ascii_tag(tag)))
    winio.save_png(p, gw, gh, buf)
    return paths.store(p)


def ascii_tag(s: str) -> str:
    """文件名只用 ASCII。

    起因：`cv2.imwrite` 在非 ASCII 路径上**静默失败**（建了文件但名字是 GBK 乱码），
    排查了很久。PNG 走 Pillow 其实没问题，但没必要在两套行为之间冒险。
    """
    out = "".join(ch if (ch.isalnum() and ord(ch) < 128) else "_" for ch in (s or ""))
    while "__" in out:
        out = out.replace("__", "_")
    return out.strip("_")[:40] or "x"


# ---------------------------------------------------------------- 证据信封

def ok(**kw) -> dict:
    """成功信封。`ok` **最后写**，不许被 payload 里的同名字段覆盖。"""
    d = dict(kw)
    d["ok"] = True
    return d


def fail(reason: str = "", /, **kw) -> dict:
    """失败信封。失败必须带**明确原因**。

    ⚠️ 形参故意叫 `reason` 而**不是** `why`。
    因为调用方几乎一律是 `fail(x, **payload)`，而 payload 里往往已经有 `why` 键 ——
    形参要是也叫 `why`，就会炸 `TypeError: got multiple values for argument 'why'`。
    实测踩过：`san9_facility_command` 失败时**真正的失败原因被这个 TypeError 盖掉**，
    而 `server.py` 把它报成"参数不对"，看起来像 schema 问题，极难查。
    （同类地雷还埋在 `san9_recover` 里，改完形参名一并拆除。）

    ✅ **2026-09-19 结构性修复**：第一个参数改成**位置限定**（`/`）——
    它只能按位置传，名字不再占用关键字。
    所以 `fail("...", reason="x")`、`fail("...", stage="y")` 一律合法，
    payload 里叫什么都不可能再撞。

    ⛔ **这个坑已经踩过三次**（`why` → `stage` → `reason`），
    每次都是"payload 里恰好也有同名字段"。所以从"改形参名"升级成"改签名" ——
    **凡是接收 `**payload` 的函数，第一个参数一律加 `/`。**
    """
    d = dict(kw)
    d["ok"] = False
    d["why"] = reason or d.get("why") or "（工具没说明原因 —— 这本身是个 bug）"
    return d


# ---------------------------------------------------------------- 界面判据

def probe(w, bgra=None) -> dict:
    """一次截图，把界面判据全算出来（避免重复抓屏）。

    返回：
      clear            战略面是否**通畅**（`go` 锚点在 = 没东西挡路）
      on_strategy_face 是否在战略面上（`info` + `func` 两个身份锚点都在）
      go_score         「進行」的匹配分（通畅 0.8~1.0 / 被挡 0.09~0.35）
      blocked          go_score 明显低 → 有东西挡着
      anchors          每个锚点的原始结果（留证用）
    """
    from san9.anchors import AnchorBook

    book = AnchorBook()
    if bgra is None:
        gw, gh, buf = grab(w)
    else:
        gw, gh, buf = bgra
    clear, hits = book.is_clear(buf, gw, gh, SCREEN)
    ident = book.identify(buf, gw, gh, SCREEN)
    go = book.find(buf, gw, gh, SCREEN, "go")
    score = round(float(go.get("score", 0.0)), 3) if go else None
    return {
        "clear": bool(clear),
        "on_strategy_face": bool(ident),
        "go_score": score,
        "blocked": bool(score is not None and score < 0.5),
        "anchors": [dict(h) for h in hits],
    }
