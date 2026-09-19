# -*- coding: utf-8 -*-
"""L 组 · 时间与弹窗。"""
from __future__ import annotations

from san9 import atoms, dialog

from san9mcp import panels, registry, runtime

MAX_TURNS = 12


def _date(hwnd):
    """顶栏日期字符串（如 `184年4月上旬`）。过旬的**硬判据** —— 比内部计数可靠。"""
    try:
        from san9 import ocr
        return ocr.read_topbar(hwnd).get("date")
    except Exception:
        return None


@registry.tool(
    "san9_end_turn", group="L", tier="play",
    summary="过 n 旬。工具会吞掉演出 / 战斗报告 / 普通通知弹窗；"
            "遇到**需要你决策的**弹窗会停下来，返回 `stopped_at`，"
            "你处理完再调一次。"
            "**过完必须调 `san9_journal` 看本旬到底发生了什么** —— "
            "命令只在这时候结算，不看就等于白下。",
    schema={"type": "object", "properties": {
        "n": {"type": "integer", "default": 1, "minimum": 1,
              "description": "过几旬（上限 %d；每旬单独留证，中途失败立刻停）" % MAX_TURNS},
    }})
def san9_end_turn(n: int = 1):
    w = runtime.window()
    focused = runtime.ensure_foreground(w)
    n = max(1, min(int(n), MAX_TURNS))

    # ⛔ **换旬必须清空所有 session。**
    # 否则 agent 拿着上一旬的 session 回来，面对的已经是另一个世界了 ——
    # 可选武将名单、设施行号、界面状态全变了（见 san9mcp/panels.py 开头）。
    cleared = panels.clear_all("end_turn")

    date_before = _date(w.hwnd)
    rounds = []
    for i in range(n):
        r = atoms.end_turn(w.hwnd)          # 一次只过一旬，每旬单独留证
        rounds.append({
            "i": i + 1,
            "ok": bool(r.get("ok")),
            "advanced": r.get("advanced"),
            "stopped_at": r.get("stopped_at"),
            "why": r.get("why"),
            "shot": r.get("shot"),
            "topbar": r.get("topbar"),
        })
        if not r.get("ok"):
            # ⭐⭐ **"换旬成功但被弹窗挡住"和"真的没推进"必须分开报。**
            # 实测（2026-09-19）：184年3月下旬 → 4月上旬 **明明推进了**，
            # 但 `session.end_turn` 因为先撞上事件框而返回 `stopped_at="blocked"`、
            # `advanced=0` → 旧代码于是报「第 1 旬没推进」，**完全指错方向**。
            if dialog.blocking(w.hwnd):
                d = dialog.read_dialog(w.hwnd)
                d["how_judged"] = dialog.blocking_detail(w.hwnd)
                return runtime.ok(
                    focused=focused, asked=n, advanced=i, rounds=rounds,
                    date_before=date_before, date_after=_date(w.hwnd),
                    complete=False, stage="need_dialog", blocked_by="dialog",
                    dialog=d,
                    note="这一旬**很可能已经推进了**（`stopped_at=blocked` 是因为撞上了事件框，"
                         "不是没换旬）。必须先处理弹窗才能继续。",
                    next="调 san9_dialog 看正文并按索引回答；"
                         "答完可能还有下一层，全部处理完再调 san9_journal。")
            # **中途失败立刻停** —— 继续往下过会把错误连锁放大
            return runtime.fail(
                "第 %d 旬没推进：%s" % (i + 1, r.get("why") or "未知原因"),
                focused=focused, advanced=i, rounds=rounds,
                date_before=date_before, date_after=_date(w.hwnd),
                dialog_check=dialog.blocking_detail(w.hwnd),
                hint="先调 san9_shot 看现场，需要的话 san9_recover 清理")

    date_after = _date(w.hwnd)
    out = {"focused": focused, "asked": n, "advanced": len(rounds),
           "sessions_cleared": cleared,
           "date_before": date_before, "date_after": date_after,
           "advanced_by_date": bool(date_before and date_after
                                    and date_before != date_after),
           "rounds": rounds, "last_topbar": rounds[-1]["topbar"] if rounds else {}}

    # ⭐⭐ **过旬之后经常弹事件框，而且它们不会自己消失** ——
    # 旧版在这里直接返回 ok:true，agent 以为"过完旬、画面干净了"，
    # 下一个工具就撞在一个被挡住的画面上。
    # 实测（2026-09-19）：184年3月下旬 → 4月上旬之后弹出
    # 「稟張角大人，若讓南皮的武將修行，就能學得步兵兵法『奮戰』。要讓其修行至下個季節嗎？」
    # 的是/否框，`go` 卡在 0.13；而且这框**多步**：答完还有「選擇武將」列表。
    # 现在：发现还被挡着就**如实报出来**（`complete:false`），把正文读给 agent，
    # 让它用 `san9_dialog` 拍板。**不许在这儿干等，也不许替它决定。**
    if dialog.blocking(w.hwnd):
        d = dialog.read_dialog(w.hwnd)
        d["how_judged"] = dialog.blocking_detail(w.hwnd)
        return runtime.ok(**out, complete=False, stage="need_dialog",
                          blocked_by="dialog", dialog=d,
                          next="调 san9_dialog 看正文并按索引回答；"
                               "答完可能还有下一层（看返回的 still_blocking）。"
                               "全部处理完再调 san9_journal 读本旬结果。")
    out["complete"] = True
    out["next"] = "调 san9_journal 读本旬结果"
    return runtime.ok(**out)


@registry.tool(
    "san9_dialog", group="L", tier="play",
    summary="读 / 回答**事件弹窗**。过旬之后经常弹，类型五花八门，"
            "而且它们**不会自己消失** —— 所以必须有人拍板。"
            "**不传参** = 只读报告它写了什么（`body` / `hint` / `blocking`）；"
            "**`choice=N`** = 回答第 N 个选项（0 起）；"
            "**`select_row=N`** = 列表型弹窗（如「選擇武將」）选第 N 行。"
            "回答一律走**键盘**（`→`/`↓` + `Enter`，**零坐标**）—— 因为实测"
            "**按钮文字 OCR 读不出来**（4 区域 × 3 尺度全空），而按钮位置每个弹窗都不同；"
            "键盘方案新弹窗开箱即用。答完请看 `still_blocking` / `next_body` 判断还有没有下一层。",
    schema={"type": "object", "properties": {
        "choice": {"type": "integer", "minimum": 0,
                   "description": "回答第几个选项（0 起）。"
                                  "0 = 第一个（通常是「是 / 允諾 / 確認」），"
                                  "1 = 第二个（通常是「否 / 拒絕」）"},
        "select_row": {"type": "integer", "minimum": 0,
                       "description": "列表型弹窗（如「選擇武將」）选第几行（0 起）。"
                                      "⚠️ 多行的行为尚未实测过"},
    }})
def san9_dialog(choice: int | None = None, select_row: int | None = None):
    w = runtime.window()
    focused = runtime.ensure_foreground(w)

    if choice is None and select_row is None:
        d = dialog.read_dialog(w.hwnd)          # 只读
        d["focused"] = focused
        if not d.get("blocking"):
            d["note"] = "战略面**没有被挡** —— 现在没有待处理的弹窗"
            return runtime.ok(**d)
        d["next"] = ("依据 body 判断该选哪个，然后 san9_dialog(choice=0)  # 或 1 / 2 …。"
                     "不放心可以先调 san9_shot 看现场。")
        return runtime.ok(**d)

    r = (dialog.select_row(w.hwnd, int(select_row)) if select_row is not None
         else dialog.answer(w.hwnd, int(choice)))
    r["focused"] = focused
    if not r.get("ok"):
        return runtime.fail(r.get("reason") or "应答没生效", **r)
    return runtime.ok(**r)


@registry.tool(
    "san9_recover", group="L", tier="play",
    summary="界面清理：不管现在卡在什么界面（命令界面 / 弹窗 / 菜单），把它弄回**干净战略面**。"
            "判据是 `go` 锚点回来（不是「看起来像」）。"
            "**任何工具返回 ok:false 之后，都应该考虑调它。**")
def san9_recover():
    w = runtime.window()
    runtime.ensure_foreground(w)
    r = atoms.recover(w.hwnd)
    # 界面被清掉了 → 所有"停在半路"的 session 都指向不存在的东西了，一并作废
    r["sessions_cleared"] = panels.clear_all("recover")
    if not r.get("recovered"):
        return runtime.fail(
            r.get("why") or "清理不干净 —— 请停手，留图看明白再决定下一步", **r)
    return runtime.ok(**r)
