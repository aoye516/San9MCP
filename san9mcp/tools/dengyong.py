# -*- coding: utf-8 -*-
"""F 组 · 人材命令：`san9_dengyong`（登庸：把在野 / 他势力武将招募过来）。

⭐ **两步用法**（用户 2026-09-20 定的形态）：

    第 1 步  `san9_dengyong(row=N)`                 ← 只读：开表、把整张「選擇武將」表读给你
    第 2 步  `san9_dengyong(row=N, target_row=K)`   ← 选第 K 行 → Enter → 执行

第 1 步会把**在野武将**（`身分=在野`）单独列出来，连同 所在城池 + 統率/武力/智力/政治
+ 到達預定（行军天数）一起返回 —— 这些数**读得出**（这一屏字号够大），
与地域表那个读不出的「在野」不同。

为什么单独一个模块
==================
同 `san9_move` / `san9_search`：能力在 `san9/dengyong.py`，本模块只做 MCP 翻译与校验，
⛔ 不往 `act.py` 里塞（那里挂着别处的未提交改动）。
"""
from __future__ import annotations

from san9mcp import registry, runtime
from san9mcp.tools.act import _fail_and_clean

DENGYONG_IRREVERSIBLE = (
    "⚠️⚠️ **`dry_run=false` 这一步不可逆** —— 点下绿「執行」，这名武将本旬就被派去登庸了，"
    "Esc 撤不回来（要等过旬才知道成没成）。"
    "⇒ 默认 `dry_run=true`：一路开到「執行」**变绿**，把 起点 / 对象武将 / 五维 读给你看，"
    "**然后按中止退回**，不碰游戏状态。要真提交必须显式传 `dry_run=false`。")

DENGYONG_FLOW_NOTE = (
    "⭐ 前段与 `san9_search`（探索）同族：同一个「人材」子菜单、同一个命令界面外观、"
    "同一个「军师气泡」坑（气泡会盖住「執行」，所以点之前要**等它自己消失**）。"
    "差别只在选目标那一步：探索是**地域表、鼠标点行**；登庸是**全屏「選擇武將」表、↑↓ + Enter**。")

DENGYONG_COLUMNS_NOTE = (
    "表的列依次是：`武將 · 身分 · 忠誠 · 勢力 · 所在 · 統率 · 武力 · 智力 · 政治 · 到達預定`。"
    "**在野武将**：`身分=在野`、`忠誠=---`、`勢力` 空；"
    "`所在` 是**他现在待的城池/港**（不是你派他去的地方），"
    "`到達預定` 是**派人过去要走几天**。")


@registry.tool(
    "san9_dengyong", group="F", tier="play",
    summary="**登庸武将**（人材 → 登庸）：把在野 / 他势力的武将招募过来。"
            "链路：点城→菜单→人材→登庸→点「對象武將」→**全屏「選擇武將」表**→"
            "↑↓ 选行 → Enter 确认 → **等军师气泡自己消失** → 绿「執行」。"
            "⭐ **两步用法**：`target_row=null`（默认）= **只读侦察**，把整张表读给你"
            "（`officers[]` 每人的 身分/所在/統率/武力/智力/政治/到達預定，**数字读得出**），"
            "并把 `身分=在野` 的单独汇成 `wild[]`；拿到行号后再调一次并传 `target_row`。"
            "⭐ **默认 `dry_run=true`**：开到「執行」变绿就中止退回，不碰游戏状态。"
            "⚠️ 传 `dry_run=false` 才真提交 —— 提交后不可逆（该武将本旬被占用）。"
            "💡 登庸的**成功率不是 100%**（取决于对方忠诚、我方魅力、使者政治），失败也属正常。",
    schema={"type": "object", "properties": {
        "row": {"type": "integer",
                "description": "⭐ **命令设施**（从哪座城派人去登庸）在右侧「移動列表」里的行号"
                               "（0 起）。先调 `san9_look` 拿 `facilities[].row`。"
                               "⛔ 不要复用上次看到的行号 —— 列表顺序会变。"},
        "expect_city": {"type": ["string", "null"], "default": None,
                        "description": "把命令设施的城名读出来核对（防止行号串了）。"},
        "target_row": {"type": ["integer", "null"], "default": None,
                       "description": "⭐ 要登庸的武将在这张表里的**行号（0 起）**——"
                                      "从第一步返回的 `officers[].row` 里挑。"
                                      "传 null = 只读侦察（开表→读全表→退回，不选也不提交）。"},
        "only_wild": {"type": "boolean", "default": False,
                      "description": "true = 返回的 `officers[]` 只保留 `身分=在野` 的人。"},
        "dry_run": {"type": "boolean", "default": True,
                    "description": "⭐ 默认 true = 走到「執行」变绿就中止（不提交）。"
                                   "false = 真提交，不可逆。"},
        "detailed": {"type": "boolean", "default": False,
                     "description": "true=附每步的原始证据（底栏原文、亮度剖面、按键记录）"},
    }})
def san9_dengyong(row: int | None = None, expect_city: str | None = None,
                  target_row: int | None = None, only_wild: bool = False,
                  dry_run: bool = True, detailed: bool = False):
    from san9 import dengyong

    w = runtime.window()
    focused = runtime.ensure_foreground(w)
    out: dict = {"focused": focused, "dry_run": bool(dry_run),
                 "irreversible_note": DENGYONG_IRREVERSIBLE,
                 "flow_note": DENGYONG_FLOW_NOTE,
                 "columns_note": DENGYONG_COLUMNS_NOTE,
                 "target_row_arg": target_row}

    def _refocus():
        """⭐ 每个改屏步骤之前重申前台 —— `winio.press` 走 SendInput，只送到前台窗口。
        本工具一次调用连着做五步、跑几十秒，期间焦点可能被抢走
        （2026-09-19 真机症状：方向键"像坏了"，其实是键没送到游戏，见 docs/04 §12.13）。
        """
        try:
            return runtime.ensure_foreground(w)
        except Exception:
            return None

    def _step(p):
        """步骤结果 → 给调用方看的小结（⛔ 不要用 `act._target_reading`，它吃的是别的结构）。"""
        if not isinstance(p, dict) or detailed:
            return p
        keep = ("ok", "why", "step", "note", "label", "clicked", "table",
                "command_screen_before", "current", "current_after", "presses",
                "highlight_before_enter", "left_table", "execute_button",
                "green_execute", "green_execute_after_wait", "recover", "go_clear",
                "waited_for_bubble_s", "checked_after_s", "menu", "nav",
                "hint", "commanded", "hint_blocked", "sub_read")
        return {k: v for k, v in p.items() if k in keep}

    # ── 0. 参数校验：⛔ 一个键都不按 ────────────────────────────────
    if row is None or isinstance(row, bool) or not isinstance(row, int):
        return runtime.fail(
            "必须传 `row` = **命令设施**（从哪座城派人）在右侧列表里的行号（0 起）。"
            "先调 `san9_look`（字段 `facilities[].row`），⛔ 不要复用旧行号。",
            failed_at="arg", reason_code="bad_arg", touched_game=False, **out)
    if row < 0:
        return runtime.fail("`row` 不能是负数（收到 %r）。" % row,
                            failed_at="arg", reason_code="bad_arg",
                            touched_game=False, **out)
    if target_row is not None and (isinstance(target_row, bool)
                                   or not isinstance(target_row, int)
                                   or not (0 <= target_row < dengyong.N_SLOTS)):
        return runtime.fail(
            "`target_row` 必须是 0..%d（表上只有 %d 行），收到 %r。"
            % (dengyong.N_SLOTS - 1, dengyong.N_SLOTS, target_row),
            failed_at="arg", reason_code="bad_arg", touched_game=False, **out)
    if (not dry_run) and target_row is None:
        return runtime.fail(
            "`dry_run=false` 必须给 `target_row`（要登庸谁）—— "
            "⛔ 不允许在不知道对象的情况下提交。先调一次只读模式拿行号。",
            failed_at="missing_target_row", reason_code="bad_arg",
            touched_game=False, **out)

    # ── 1. 打开「登庸」命令界面（人材 → 登庸）───────────────────────
    _refocus()
    op = dengyong.step_open_screen(w.hwnd, row, expect_city)
    out["open_screen"] = _step(op)
    if not op.get("ok"):
        shot = runtime.save_shot(w, "dengyong_open_fail")
        return _fail_and_clean(
            "打不开「登庸」命令界面：%s ｜ ⭐ **最常见原因：这座城本旬没有可行动的武将**"
            "—— 此时「登庸」是灰项，游戏底栏会写「沒有可執行的武將。」"
            % (op.get("why") or "?"),
            failed_at="command_unavailable", reason_code="command_unavailable",
            game_says=op.get("hint"), shot=shot, **out)
    out["commanded"] = op.get("commanded")

    # ── 2. 点「對象武將」→ 全屏「選擇武將」表 ───────────────────────
    _refocus()
    tb = dengyong.step_open_picker(w.hwnd)
    out["open_picker"] = _step(tb)
    if not tb.get("ok"):
        shot = runtime.save_shot(w, "dengyong_table_fail")
        return _fail_and_clean(
            "点「對象武將」之后**没出「選擇武將」表**：%s ｜ "
            "⛔ 也可能这一屏真的**一个可登庸的武将都没有**（表是空的）。"
            % (tb.get("why") or "?"),
            failed_at="picker_table", reason_code="picker_table_missing",
            shot=shot, **out)

    # ── 3. 读整张表（这一步就是"返回这些人的信息"）─────────────────
    rows = dengyong.read_rows(w.hwnd)
    out["highlight_row"] = dengyong.highlight_row(w.hwnd).get("row")
    table = []
    for r in rows:
        if all(r.get(k) is None for k in ("name", "status", "where", "lead")):
            continue                      # 整行空 —— 表底部的空行
        table.append({k: v for k, v in r.items() if not k.endswith("_raw")} | (
            {"raws": {k[:-4]: v for k, v in r.items() if k.endswith("_raw")}} if detailed else {}))
    out["n_rows"] = len(table)
    out["officers"] = [r for r in table if not (only_wild and r.get("status") != "在野")]
    out["wild"] = [r["row"] for r in table if r.get("status") == "在野"]
    out["officers_note"] = (
        "⭐ 列含义：`status`=身分（**在野** = 无主、可登庸）· `where`=他现在待的城池/港 · "
        "`lead/war/intel/pol`=統率/武力/智力/政治 · `eta`=派人过去要几天。"
        "⚠️ 数字为 `null` = **读不出，作废不猜**（看 `detailed=true` 的 `raws`）。"
        "⚠️ `row` 是**当前这一屏**的行号 —— ⛔ 别跨次复用，每次都要重新只读一次。")

    if not table:
        _refocus()
        out["closed"] = _step(dengyong.step_abort(w.hwnd))
        return runtime.fail(
            "「選擇武將」表**一行都没读出来** ⇒ 要么这一屏没有可登庸的武将，"
            "要么版面变了。⛔ 不猜、不硬点。（已尝试退回干净战略面）",
            failed_at="empty_table", reason_code="empty_table", **out)

    # ── 4. 只读侦察模式：到此为止，退回 ───────────────────────────
    if target_row is None:
        _refocus()
        out["closed"] = _step(dengyong.step_abort(w.hwnd))
        out["ok_but_no_submit"] = True
        out["note"] = ("只读模式：已读出整张「選擇武將」表（含 %d 名**在野**武将），"
                       "**没提交任何命令**。下一步挑一个 `row` 传 `target_row` 再来一次。"
                       % len(out["wild"]))
        if not out["closed"].get("ok"):
            return runtime.fail(
                "数据读到了，但**退屏没成功** ⇒ 先调 `san9_recover`。（%s）"
                % out["closed"].get("why"),
                failed_at="close", reason_code="close_failed", **out)
        return runtime.ok(**out)

    # ── 5. ↑↓ 移到目标行 → Enter 确认 ─────────────────────────────
    _refocus()
    mv = dengyong.step_move_to_row(w.hwnd, target_row)
    out["move_to_row"] = _step(mv)
    if not mv.get("ok"):
        shot = runtime.save_shot(w, "dengyong_move_fail")
        return _fail_and_clean(
            "把高亮移到第 %d 行失败：%s ｜ ⛔ **绝不盲按方向键**（方向键是相对移动，"
            "猜错就会登庸错人）。" % (target_row, mv.get("why") or "?"),
            failed_at="move_highlight", reason_code="move_failed",
            shot=shot, **out)
    picked = next((r for r in table if r["row"] == target_row), {})
    out["target_officer"] = {k: picked.get(k) for k in
                             ("row", "name", "status", "where", "lead", "war",
                              "intel", "pol", "eta")}

    _refocus()
    cf = dengyong.step_confirm(w.hwnd)
    out["confirm"] = _step(cf)
    if not cf.get("ok"):
        shot = runtime.save_shot(w, "dengyong_confirm_fail")
        return _fail_and_clean(
            "确认登庸对象失败：%s" % (cf.get("why") or "?"),
            failed_at="confirm", reason_code="confirm_failed", shot=shot, **out)

    # ── 6. dry_run：中止退回 ──────────────────────────────────────
    if dry_run:
        _refocus()
        out["closed"] = _step(dengyong.step_abort(w.hwnd))
        out["note"] = ("dry_run：已开到「執行」变绿（起点=%s、对象=第 %d 行 %s），"
                       "**没有提交**，已退回。要真提交请传 `dry_run=false`。"
                       % (out.get("commanded"), target_row,
                          out.get("target_officer", {}).get("name") or "?"))
        if not out["closed"].get("ok"):
            return runtime.fail(
                "dry_run 收尾退屏失败 ⇒ 先调 `san9_recover`。（%s）" % out["closed"].get("why"),
                failed_at="close", reason_code="close_failed", **out)
        return runtime.ok(**out)

    # ── 7. 真提交 ─────────────────────────────────────────────────
    _refocus()
    ex = dengyong.step_execute(w.hwnd)
    out["execute"] = _step(ex)
    if not ex.get("ok"):
        shot = runtime.save_shot(w, "dengyong_execute_fail")
        return _fail_and_clean(
            "点「執行」之后没回到干净战略面：%s ｜ ⚠️ 提交时会有**军师气泡**，"
            "它几秒自动消失，本工具**不会**去按键猜。请看一眼屏幕再决定。"
            % (ex.get("why") or "?"),
            failed_at="execute", reason_code="execute_unconfirmed",
            shot=shot, **out)
    out["closed"] = {"ok": True, "how": "点「執行」后底栏回「請選擇命令起點」（游戏自己关的）"}
    out["committed"] = True
    out["note"] = ("已提交：**%s** 派人去登庸 **%s**（他在 %s）。"
                   "⚠️ 登庸**成功率不是 100%%**；过旬后用 `san9_journal` 看游戏写的结果。"
                   "⛔ 这条命令占了那名使者本旬的行动。"
                   % (out.get("commanded"),
                      out.get("target_officer", {}).get("name") or "?",
                      out.get("target_officer", {}).get("where") or "?"))
    return runtime.ok(**out)
