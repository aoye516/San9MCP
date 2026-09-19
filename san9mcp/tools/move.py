# -*- coding: utf-8 -*-
"""F 组 · 人材命令：`san9_move`（把武将移动到自势力的另一座设施）。

为什么单独一个模块
==================
`act.py` 里当时还挂着别的会话**未提交**的改动（`git status` = `M`）。
把新工具塞进去会让两份改动混在一个文件里、以后很难拆。
⇒ 能力仍在 `san9/`（`san9/movecmd.py`），本模块只做 MCP 翻译与校验。

与 `san9_transport` 的三处关键差别（⛔ 别混，2026-09-19 真机确认）
----------------------------------------------------------------
| | 輸送 | 移動 |
|---|---|---|
| 顺序 | 選武將 → 士兵數量 → 執行 → 目標列表 | **目標設施 → 執行武將 → 執行** |
| 选武将 | **单选**（点一下自动回，无「決定」）| **多选**（勾一下弹窗不关，要按绿「決定」）|
| 数值屏 | 有（士兵數量）| 无 |

⇒ 两条链路**只共用目标列表那一段**（`targetlist.parse_target_list` / `select_target`）。
"""
from __future__ import annotations

from san9mcp import registry, runtime
from san9mcp.tools.act import _fail_and_clean, _parse_officers, _target_reading

MOVE_IRREVERSIBLE = (
    "⚠️⚠️ **`dry_run=false` 这一步不可逆** —— 点下最后的绿「執行」命令就进游戏了，"
    "Esc 撤不回来（武将本旬会被占用、开始行军）。"
    "⇒ 默认 `dry_run=true`：一路开到「執行」**变绿**、把 起点/目标/武将 读给你看，"
    "**然后按中止退回**，不碰游戏状态。要真提交必须显式传 `dry_run=false`。")


@registry.tool(
    "san9_move", group="F", tier="play",
    summary="**把武将移动到自势力的另一座设施**（人材 → 移動）。"
            "链路：点城→菜单→人材→移動→点「目標設施」→选目标设施表→Enter→"
            "点「執行武將」→勾选（**多选**）→绿「決定」→绿「執行」。"
            "⭐ **默认 `dry_run=true`**：一路开到「執行」变绿，把「命令设施 / 目标设施 / "
            "执行武将」读给你看，**然后按中止退回**，不碰游戏状态。"
            "⚠️ 传 `dry_run=false` 才真提交 —— **提交后不可逆**（该武将本旬被占用）。"
            "⭐ 与 `san9_transport` 的三处关键差别：**顺序相反**（目标→武将）、"
            "选将**多选**（要按「決定」）、**没有**士兵数量屏。"
            "⭐ 目标设施走 `namelock`，只认 `locked` 行；`expect_city` 对不上**零动作拒绝**。"
            "⭐ 收尾判据是底栏回到「請選擇命令起點」。"
            "⚠️ **两条已知限制**（工具会明确报失败，不硬做）："
            "① 目标表**不含命令来源城**（你要调人的那座城自己不会出现在表里）；"
            "② 若该城本旬**没有可行动武将**，「移動」是灰项、菜单里按 Enter 进不去，"
            "工具返回 `command_unavailable` 并附游戏底栏原话。",
    schema={"type": "object", "properties": {
        "row": {"type": "integer",
                "description": "⭐ **命令设施**（要**从**哪座城调人）在右侧「移動列表」里的行号"
                               "（0 起）。先调 `san9_look` 拿 `facilities[].row`。"
                               "⛔ 不要复用上次看到的行号 —— 列表顺序会变。"},
        "expect_city": {"type": ["string", "null"], "default": None,
                        "description": "⭐ **目标设施**名（人**要调去**哪座城）。"
                                       "工具会先读当前目标表、用 namelock 定位本次行号。"
                                       "⛔ 传**来源城**自己会找不到（目标表不含它）。"
                                       "传 null = 只读模式：开屏→读 15 行→退回，不提交任何东西。"},
        "officers": {"type": ["array", "string", "null"], "default": None,
                     "description": "要调动的执行武将：行号数组（如 [0] 或 [0,2]）或 \"all\"。"
                                    "⚠️ 行号指**选将弹窗当前列出**的顺位（0 起），"
                                    "本旬只剩 1 人时游戏可能已自动选中。"
                                    "`dry_run=false` 时这些人会被真派出去、本旬不能再下别的命令。"},
        "dry_run": {"type": "boolean", "default": True,
                    "description": "⭐ 默认 true = 走到「執行」变绿就中止（不提交）。"
                                   "false = 真提交，不可逆。"},
        "detailed": {"type": "boolean", "default": False,
                     "description": "true=附每步的画面变化量、底栏原文、结构判据"},
    }})
def san9_move(row: int | None = None, expect_city: str | None = None,
              officers=None, dry_run: bool = True, detailed: bool = False):
    from san9 import movecmd, targetlist

    w = runtime.window()
    focused = runtime.ensure_foreground(w)
    out: dict = {"focused": focused, "dry_run": bool(dry_run),
                 "officers_arg": officers, "irreversible_note": MOVE_IRREVERSIBLE}

    def _refocus():
        """⭐ **每一个改屏步骤之前都要重申前台** —— `winio.press` 走 SendInput，
        只送到**前台窗口**。本工具一次调用里连着做八步、跑几十秒，
        期间焦点可能被抢走（用户在看屏幕、终端抢焦点…），
        而 `ensure_foreground` 原本只在**函数开头**调了一次。
        ⛔ 2026-09-19 真机症状：`select_target` 连按 3 轮 `up×3` 高亮**一动不动**、
        报"没到位"，看起来像"方向键坏了" —— 其实是键没送到游戏。
        （同样的动作拆成单步 `move_step.py` 跑就没事，因为那脚本**每步都重申前台**。）
        """
        try:
            return runtime.ensure_foreground(w)
        except Exception:
            return None

    # ── 0. 参数校验：⛔ 一个键都不按 ────────────────────────────────
    if row is None or isinstance(row, bool) or not isinstance(row, int):
        return runtime.fail(
            "必须传 `row` = **命令设施**（要从哪座城调人）在右侧列表里的行号（0 起）。"
            "先调 `san9_look` 拿 `facilities[].row`。⛔ 传目标城的行号是常见的搞反。",
            failed_at="bad_row", reason_code="bad_arg", touched_game=False, **out)
    if not 0 <= row < targetlist.N_SLOTS:
        return runtime.fail(
            "row=%r 越界：右侧列表 0..%d。" % (row, targetlist.N_SLOTS - 1),
            failed_at="bad_row", reason_code="bad_arg", touched_game=False, **out)

    sel_all, rows_idx = (None, None)
    if officers is not None:
        sel_all, rows_idx = _parse_officers(officers)
        if sel_all is None:
            return runtime.fail(
                '`officers` 只接受 "all" 或行号数组（如 [0] / [0,2]）', got=officers,
                failed_at="bad_officers", reason_code="bad_arg",
                touched_game=False, **out)
    # ⚠️ `officers` 在"本旬只剩 1 人"时**不需要**（游戏已自动填好）——
    #    而这一点在动手前**无法预知** ⇒ 不做硬性拒绝，改成"没给就跳过选将"。
    #    真提交时若确实没给且城里有 ≥2 人，会走到「執行」不绿那一步被拦住（**零部分提交**）。
    if (not dry_run) and expect_city and officers is None:
        out["officers_missing_warning"] = (
            "⚠️ `dry_run=false` 但没给 `officers`：如果这座城本旬**只剩 1 名可下令武将**，"
            "游戏会自动填好他、照常提交；若有多人则会停在「執行」不绿而被拦下"
            "（`execute_not_ready`，**不会半提交**）。")

    # ── 1. 开「人材 → 移動」命令界面 ────────────────────────────────
    op = movecmd.step_open_screen(w.hwnd, row)
    if detailed:
        out["open_steps"] = op
    out["commanded"] = op.get("commanded")
    if not op.get("ok"):
        # ⛔ 取证必须在清理之前（项目血案：拍在 recover 之后 = 拍到已恢复的画面）
        shot = runtime.save_shot(w, "move_open_fail")
        ls = op.get("labels") or {}
        return _fail_and_clean(
            "没开到「移動」命令界面（停在 %s）：%s ｜ "
            "⭐ **最常见的原因：这座城本旬没有可行动的武将** —— 此时「移動」是**灰项**，"
            "菜单里按 Enter 进不去（不是定位失败、也不是 bug）。"
            "另一常见原因：row 指到了他势力的城、或菜单没开。"
            % (op.get("row"), op.get("why")),
            failed_at="command_unavailable", reason_code="command_unavailable",
            symptom="open_move_screen", game_says=op.get("hint"),
            labels=ls, shot=shot, **out)

    # ── 2. 点「目標設施」→ 进选择目标模式（表格 / 地图两形态都算）────
    tg = movecmd.step_target_mode(w.hwnd)
    if detailed:
        out["target_mode_step"] = tg
    if not tg.get("ok"):
        shot = runtime.save_shot(w, "move_target_fail")
        return _fail_and_clean(
            "点「目標設施」之后没进选择目标模式：%s" % tg.get("why"),
            failed_at="target_mode", reason_code="target_mode_failed",
            shot=shot, **out)

    # ── 3. 确保是表格形态，并读表 ───────────────────────────────────
    gl = movecmd.step_open_list(w.hwnd)
    if detailed:
        out["open_list_step"] = gl
    if not gl.get("ok"):
        shot = runtime.save_shot(w, "move_list_fail")
        return _fail_and_clean(
            "没能拿到目标设施表：%s" % gl.get("why"),
            failed_at="target_list", reason_code="target_list_failed",
            shot=shot, **out)

    bgr = movecmd.ocr._grab_bgr(w.hwnd)
    p = movecmd.targetlist.parse_target_list(bgr, cities=_known_cities())
    out["parsed"] = p
    out["reading"] = _target_reading(p)
    out["source_exclusion_note"] = (
        "⚠️ **本表不含命令来源城**（第 %d 行那座城自己不会出现）—— "
        "'表里找不到某城' 可能只是因为这个，⛔ 别当成读漏了。" % row)

    rows = p.get("rows") or []

    # ── 3b. 只读模式：expect_city 没给 → 读给你看，然后退回 ─────────
    if not expect_city:
        out["decision_step"] = "list_only"
        out["closed"] = movecmd.close(w.hwnd)
        out["ok_but_no_submit"] = True
        out["note"] = ("只读模式：已读当前目标表并退回干净战略面，**没提交任何命令**。"
                       "下一步请传 `expect_city`（目标设施名）+ `officers`。")
        if not out["closed"].get("ok"):
            return runtime.fail(
                "数据读到了，但**退屏没成功** ⇒ 先调 `san9_recover`。（%s）"
                % out["closed"].get("why"),
                failed_at="close", reason_code="close_failed", **out)
        return runtime.ok(**out)

    # ── 4. 定位目标设施那一行（按名字锁，不按旧 row）─────────────────
    matches = [r for r in rows if r.get("city") == expect_city
               and r.get("status") == "locked"]
    if len(matches) != 1:
        shot = runtime.save_shot(w, "move_target_locate_fail")
        desc = [(r.get("row"), r.get("status"), r.get("raw")) for r in rows]
        return _fail_and_clean(
            "目标设施「%s」在表里无法**唯一锁定**：matches=%d。（%s）｜ "
            "⛔ 常见原因：① 它其实是**命令来源城**（目标表不含它）；"
            "② 名字是 candidate/unread（形近或单字读不出，如单字城名）；"
            "③ 它不属于本势力。**一个方向键都没按。**"
            % (expect_city, len(matches),
               targetlist.CANDIDATE_IS_NOT_A_BUG),
            failed_at="target_not_uniquely_locked",
            reason_code="target_not_uniquely_locked",
            want=expect_city, matches=len(matches), all_rows=desc,
            touched_game=False, shot=shot,
            recover=movecmd.close(w.hwnd), **out)

    trow = matches[0]["row"]
    out["located"] = {"expect_city": expect_city, "row": trow,
                      "method": "current_table_locked_city"}
    out["focus_before_select"] = _refocus()
    sel = movecmd.select_list_row(w.hwnd, trow)
    out["select"] = sel
    if not sel.get("ok"):
        shot = runtime.save_shot(w, "move_select_fail")
        return _fail_and_clean(
            "把高亮移到第 %d 行（%s）失败：%s" % (trow, expect_city, sel.get("why")),
            failed_at="select_target", reason_code="select_failed",
            shot=shot, **out)

    out["focus_before_confirm"] = _refocus()
    cf = movecmd.step_confirm_target(w.hwnd)
    out["confirm"] = cf
    if not cf.get("ok"):
        shot = runtime.save_shot(w, "move_confirm_fail")
        return _fail_and_clean(
            "按 Enter 确认目标失败：%s" % cf.get("why"),
            failed_at="confirm_target", reason_code="confirm_failed",
            shot=shot, **out)

    # ── 5. 点「執行武將」→ 选将（**多选**）──────────────────────────
    out["focus_before_picker"] = _refocus()
    pk = movecmd.step_open_picker(w.hwnd)
    out["picker_step"] = pk
    if not pk.get("ok"):
        shot = runtime.save_shot(w, "move_picker_fail")
        return _fail_and_clean(
            "打开选将这条路失败：%s ｜ ⚠️ 注意：**「本旬只剩 1 名可下令武将」不是失败** —— "
            "那种情况会被识别成 `auto_selected`（游戏已自动填好人）走另一条路；"
            "报到这里说明是**真的打不开**（标签定位失败，且不是「没有彩色标签」那种）。"
            % pk.get("why"),
            failed_at="picker_failed", reason_code="picker_failed",
            game_says=pk.get("hint"), shot=shot, **out)

    # ⭐⭐ 「執行武將」标签是灰的 = **本旬只剩 1 名可下令武将、游戏已自动填好他**
    #    （用户 2026-09-19 指出这是「人材/軍事」这类命令的**通例**，见 docs/04 §12.10）
    #    ⇒ 跳过"选将 + 決定"，直接去判「執行」是不是绿的（那是游戏自己给的硬信号）。
    auto = bool(pk.get("auto_selected"))
    out["auto_selected_officer"] = auto
    picked, rows_idx = [], []
    dc = {"ok": True, "skipped": "auto_selected"}
    if auto:
        out["picker_note"] = pk.get("note")
        out["picked"] = []
        out["note_auto"] = ("⚠️ 「執行武將」标签是**灰的** ⇒ 游戏已自动选好本旬唯一的"
                            "可下令武将。**本工具没有读到他叫什么名字**"
                            "（卡片上的字不在返回值里）—— 真提交前请自己看清楚。")
    if not auto:
        plist = movecmd.picker_officers_raw(w.hwnd)
        out["officers_in_picker"] = [{"row": o["row"], "y": o["y"], "name": o.get("name")}
                                     for o in plist]
        if sel_all:
            rows_idx = list(range(len(plist)))
        bad = [i for i in rows_idx if not (0 <= i < len(plist))]
        if bad:
            shot = runtime.save_shot(w, "move_officer_range_fail")
            return _fail_and_clean(
                "officers 行号 %s 超出范围 —— 弹窗名单读到 %d 行（0..%d）。"
                "⚠️ 读到的行数**可能多于真实人数**（检测器兜底会造 8 行）"
                "⇒ 越界时报错，⛔ 不猜。" % (bad, len(plist), len(plist) - 1),
                failed_at="officer_out_of_range", reason_code="bad_arg",
                touched_game=False, shot=shot, recover=movecmd.close(w.hwnd), **out)

        picked = []
        for i in rows_idx:
            r = movecmd.step_pick_officer(w.hwnd, i)
            picked.append({"row": i, "ok": r.get("ok"), "y": r.get("clicked_y"),
                           "name_raw": r.get("row_name_raw"), "why": r.get("why")})
            if not r.get("ok"):
                shot = runtime.save_shot(w, "move_pick_fail")
                return _fail_and_clean(
                    "勾选第 %d 个武将失败：%s" % (i, r.get("why")),
                    failed_at="pick_officer", reason_code="pick_failed",
                    shot=shot, picked=picked, **out)
        out["picked"] = picked

        dc = movecmd.step_confirm_officers(w.hwnd)
        out["decide"] = dc
        if not dc.get("ok"):
            shot = runtime.save_shot(w, "move_decide_fail")
            return _fail_and_clean(
                "点「決定」失败：%s" % dc.get("why"),
                failed_at="confirm_officers", reason_code="decide_failed",
                shot=shot, **out)

    # ── 6. 「執行」应变绿 —— 这是**游戏自己**给的"目标+武将都齐了"信号 ──
    bgr = movecmd.ocr._grab_bgr(w.hwnd)
    green = movecmd.cmdscreen.find_button_in(bgr, "green", *movecmd.MOVE_BTN_BAND)
    out["green_execute"] = green
    if green is None:
        shot = runtime.save_shot(w, "move_execute_grey_fail")
        return _fail_and_clean(
            "「執行」按钮**不是绿的** ⇒ 目标或武将没选齐（选齐才变绿，这正是本判据的分辨力）。"
            "⛔ 不点。",
            failed_at="execute_grey", reason_code="execute_not_ready",
            shot=shot, **out)

    # ── 7. dry_run：到此为止 ────────────────────────────────────────
    if dry_run:
        out["closed"] = movecmd.close(w.hwnd)
        out["committed"] = False
        out["note"] = ("dry_run：已开到「執行」变绿（目标=「%s」、武将=%s），"
                       "**没有提交**，已退回。要真提交请传 `dry_run=false`。"
                       % (expect_city, [x.get("name_raw") for x in picked]))
        if not out["closed"].get("ok"):
            return runtime.fail(
                "dry_run 收尾退屏失败 ⇒ 先调 `san9_recover`。（%s）" % out["closed"].get("why"),
                failed_at="close", reason_code="close_failed", **out)
        return runtime.ok(**out)

    # ── 8. 真提交 ──────────────────────────────────────────────────
    out["focus_before_execute"] = _refocus()
    ex = movecmd.step_execute(w.hwnd)
    out["execute"] = ex
    if not ex.get("ok"):
        shot = runtime.save_shot(w, "move_submit_fail")
        return _fail_and_clean(
            "提交失败：%s" % ex.get("why"),
            failed_at=ex.get("stage"), reason_code="submit_failed",
            shot=shot, recover=movecmd.close(w.hwnd), **out)

    out["committed"] = True
    out["closed"] = movecmd.close(w.hwnd)
    out["note"] = ("✅ **移動命令已提交给游戏**（不可逆）：从「第 %d 行那座城」调 %s 去「%s」。"
                   "⭐ **独立证据怎么查**：① 本旬立刻用 `san9_officers(row=<来源城>)` —— "
                   "「在任 X/Y」的 X（可下令人数）应当减掉本轮派走的人数；"
                   "② **过旬后**用 `san9_journal` 会看到游戏自己写"
                   "「XX 等人似乎已進入了 YY」+「YY 的大将换成 XX」。"
                   % (row, [x.get("name_raw") for x in picked], expect_city))
    if not out["closed"].get("ok"):
        return runtime.fail(
            "命令提交了，但**退屏没成功** ⇒ 界面可能还停在命令链上，先调 `san9_recover`。（%s）"
            % out["closed"].get("why"),
            failed_at="close", reason_code="close_failed", **out)
    return runtime.ok(**out)


def _known_cities() -> list:
    """目标设施候选表（与 `san9mcp/tools/act.py` 同一份来源）。"""
    import io
    import json
    import os

    from san9 import paths
    p = os.path.join(paths.CONFIG, "roster_facilities.json")
    if not os.path.exists(p):
        return []
    d = json.load(io.open(p, encoding="utf-8"))
    return [f["name"] for f in (d.get("facilities") or [])]
