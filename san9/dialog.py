# -*- coding: utf-8 -*-
"""事件弹窗：**读它要你干什么** + **用键盘答**。

## 实测事实（2026-09-19，过旬之后遇到的）

- **过旬之后经常弹事件框**，而且类型五花八门（是/否确认框、選武將列表…），
  以后还会遇到更多种。
- 这类框**不会自己消失** —— 谁在那儿"等它消失"，谁就卡住。工具必须**如实报告**再交出去。
- ⛔ **按钮文字 OCR 读不出来**（实测 4 组区域 × 3 个尺度**全空**）。
  所以**不许按坐标点按钮**：每个弹窗的按钮位置都不一样
  （实测「允諾/拒絕」y≈611、「是/否」y≈549、「確認」y≈513，一套坐标广不了）。
- ✅ **键盘答是零坐标的**：`→` 切换高亮、`Enter` 确认（默认高亮第一个）。
  不需要认识按钮长什么样，**新弹窗类型开箱即用**。
- **弹窗是多步的**：实测 是/否 →「選擇武將」列表 → **才**回到战略面。
  所以 `answer()` 答完会**再读一次**，把下一层带回去。
- **正文能读**（多尺度 OCR，个别错字，够 agent 判断）。

## 分工
- `blocking(hwnd)`        判"有没有东西挡着战略面"（`go` 锚点）
- `read_dialog(hwnd)`     只读：读了什么 + 底栏提示
- `answer(hwnd, choice)`  答第 N 个选项（`→`×N + `Enter`）
- `select_row(hwnd, row)` 列表型弹窗：`↓`×N + `Enter`
"""
from __future__ import annotations

import time

import numpy as np

from . import ocr, winio

DIALOG_REG = (430, 340, 1075, 665)
"""弹窗正文区（实测：事件框都落在这里。1280×960 客户区坐标）"""


def _go_score(hwnd) -> float | None:
    from . import anchors
    gw, gh, buf = winio.grab(hwnd, use_window_dc=False)
    st = anchors.AnchorBook().state(buf, gw, gh, "strategy")
    go = next((a for a in st if a["name"] == "go"), None)
    return float(go["score"]) if go and go.get("found") else None


def blocking(hwnd) -> bool:
    """战略面是否被挡（`go` 锚点 < 0.8）。**只读。**"""
    s = _go_score(hwnd)
    return s is not None and s < 0.8


def read_dialog(hwnd) -> dict:
    """**只读**：把当前挡路的对话读出来。

    ⚠️ 按钮文字读不出来（实测），所以只报**正文**和**底栏**；
    选项靠**索引**回答（`answer(hwnd, 0)` = 第一个）。

    ⚠️ 多尺度取"最长"的那条 —— 短的那条往往是掉字版（同 `cmdscreen.read_hint`）。
    """
    out: dict = {"blocking": blocking(hwnd), "go_score": _go_score(hwnd),
                 "region": list(DIALOG_REG)}
    if not out["blocking"]:
        return out
    best = ""
    for up in (4, 5, 3, 6):
        try:
            items = ocr.read_screen(hwnd, DIALOG_REG, upscale=up)
        except Exception:
            continue
        items.sort(key=lambda d: (d["y"], d["x"]))
        t = "".join((i["text"] or "").strip() for i in items)
        if len(t) > len(best):
            best = t
    out["body"] = best or None
    try:
        from . import cmdscreen
        out["hint"] = cmdscreen.read_hint(hwnd) or None
    except Exception:
        out["hint"] = None
    return out


def _after_action(hwnd, before, out: dict) -> dict:
    """动作之后的统一复核：**画面必须变**，变了就再读一次下一层。"""
    from . import cmdscreen
    after = cmdscreen._frame(hwnd)
    moved = float(np.abs(after.astype(int) - before.astype(int)).mean())
    out["screen_changed"] = round(moved, 2)
    if moved <= 0.6:
        out.update({"ok": False, "failed_at": "no_effect",
                    "reason": "按键之后**画面没有任何变化** —— 这里不是键盘可答的弹窗，"
                              "或者键序不对。**停手**，绝不改成点坐标。"})
        return out
    out["ok"] = True
    nxt = read_dialog(hwnd)
    out["still_blocking"] = nxt.get("blocking")
    out["next_body"] = nxt.get("body")
    out["next_hint"] = nxt.get("hint")
    out["go_after"] = nxt.get("go_score")
    return out


def answer(hwnd, choice: int = 0) -> dict:
    """答第 `choice` 个选项（**0 起**）。

    ⛔ **用键盘，不点坐标**：`→`×choice + `Enter`。
    判据是**画面必须变** —— 没变就如实报错，绝不谎报成功。
    """
    from . import cmdscreen
    before = cmdscreen._frame(hwnd)
    g0 = _go_score(hwnd)
    trace: list[dict] = []
    if choice > 0:
        winio.press("right", int(choice), 0.25)
        trace.append({"key": "right", "times": int(choice)})
    winio.press("enter", 1, 1.5)
    trace.append({"key": "enter"})
    time.sleep(1.6)
    out = {"choice": int(choice), "go_before": g0, "trace": trace}
    return _after_action(hwnd, before, out)


def select_row(hwnd, row: int = 0) -> dict:
    """列表型弹窗（如「選擇武將」）：`↓`×row + `Enter`。

    ⚠️ **只在"只有 1 行"的场景实测过**（那一行默认高亮，直接 `Enter` 就确认了）。
    多行时的 `↓` 行为**尚未实测** —— 用之前先单独验一次。
    """
    from . import cmdscreen
    before = cmdscreen._frame(hwnd)
    g0 = _go_score(hwnd)
    trace: list[dict] = []
    if row > 0:
        winio.press("down", int(row), 0.25)
        trace.append({"key": "down", "times": int(row)})
    winio.press("enter", 1, 1.5)
    trace.append({"key": "enter"})
    time.sleep(1.8)
    out = {"row": int(row), "go_before": g0, "trace": trace}
    return _after_action(hwnd, before, out)
