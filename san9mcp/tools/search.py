# -*- coding: utf-8 -*-
"""F 组 · 人材命令：`san9_search`（派人探索地域，找在野人才 / 宝物）。

为什么单独一个模块
==================
同 `san9_move` 的理由：能力在 `san9/`（`san9/searchcmd.py`），
本模块只做 MCP 翻译与校验，⛔ 不往 `act.py` 里塞（那里挂着别处的未提交改动）。

与其他命令的关键差别
====================
1. 目标是**地域**（另一张表「選擇地域」），不是设施。
2. ⭐ **选目标 = 鼠标点那一行**（点完直接返回命令界面，**不用方向键、不用 Enter**）。
3. ⭐ **点「在野」列标题排序** → 有人才的地域全部排到最上面（这是本工具最有价值的一步）。
4. ⚠️ 提交前后各有一个**信息对话框**（軍師提示 / 执行武将回应），
   **几秒自动消失，⛔ 不要去按 Enter**（用户 2026-09-20 指出）。
"""
from __future__ import annotations

from san9mcp import registry, runtime
from san9mcp.tools.act import _fail_and_clean

SEARCH_IRREVERSIBLE = (
    "⚠️⚠️ **`dry_run=false` 这一步不可逆** —— 点下绿「執行」，那名武将本旬就被派出去探索了，"
    "Esc 撤不回来（要等过旬才会带回结果）。"
    "⇒ 默认 `dry_run=true`：一路开到「執行」**变绿**，把 起点 / 目标地域 / 排序结果 读给你看，"
    "**然后按中止退回**，不碰游戏状态。要真提交必须显式传 `dry_run=false`。")

SEARCH_SORT_NOTE = (
    "⭐ **排序是本工具的核心**：点「在野」列标题（(724,268)，用户 2026-09-20 悬停给出坐标），"
    "在野人数 > 0 的地域会排到最上面 ⇒ 一次点击就能看出\"上哪找人才\"。"
    "⚠️ 排序那一下**会把行高亮清掉**（方向键也唤不回来）"
    "⇒ 选行只能**鼠标点**，⛔ 不能复用「移動」那套靠高亮定位的选行法。")


@registry.tool(
    "san9_search", group="F", tier="play",
    summary="**派人探索地域**（人材 → 探索）：找在野人才 / 宝物。"
            "链路：点城→菜单→人材→探索→点「目標地域」→**点「在野」列标题排序**→"
            "**鼠标点某一行**（点完自动返回）→绿「執行」。"
            "⭐ 排序后**有人才的地域全排在最上面**，所以 `region_row=0` 就是\"在野最多的地域\"。"
            "⭐ **默认 `dry_run=true`**：开到「執行」变绿、把目标地域读给你看，然后中止退回。"
            "⚠️ 传 `dry_run=false` 才真提交 —— 提交后不可逆（该武将本旬被占用）。"
            "⭐ 不传 `region_row` = **只读侦察**：开表→排序→把最上面几行的地域名读给你，再退回。"
            "⚠️ **「在野」是数字列，本工具不读**（字形库没有这种小字号的数字模板）"
            "—— 所以本工具**不给\"有几个在野\"**，只给\"哪些地域有人才 + 排序后的顺序\"。"
            "⚠️ 用户说明：**探索只能派一名将领**（本工具不传 officers，用游戏自动填的那个人）。",
    schema={"type": "object", "properties": {
        "row": {"type": "integer",
                "description": "⭐ **命令设施**（从哪座城派人）在右侧「移動列表」里的行号（0 起）。"
                               "先调 `san9_look` 拿 `facilities[].row`。"
                               "⛔ 不要复用上次看到的行号 —— 列表顺序会变。"},
        "expect_city": {"type": ["string", "null"], "default": None,
                        "description": "把命令设施的城名读出来核对（防止行号串了）。"
                                       "⛔ 与 `san9_move` 的 `expect_city` **含义不同** —— "
                                       "那里指目标城，这里 `row` 就是起点城，这只是个**自检**。"},
        "region_row": {"type": ["integer", "null"], "default": None,
                       "description": "⭐ **排序后**要选第几行（0 起）。"
                                      "排序把在野多的排前面 ⇒ **0 = 在野最多的地域**。"
                                      "传 null = 只读侦察：开表→排序→读最上面几行→退回，不选也不提交。"},
        "top_n": {"type": "integer", "default": 8,
                  "description": "只读侦察时回报最上面几行的地域名（每行一次 OCR，别开太大）。"},
        "sort": {"type": "boolean", "default": True,
                 "description": "⭐ 是否点「在野」列标题排序。默认 true（**强烈建议保持**）。"},
        "dry_run": {"type": "boolean", "default": True,
                    "description": "⭐ 默认 true = 走到「執行」变绿就中止（不提交）。"
                                   "false = 真提交，不可逆。"},
        "detailed": {"type": "boolean", "default": False,
                     "description": "true=附每步的底栏原文、结构判据、OCR 原文"},
    }})
def san9_search(row: int | None = None, expect_city: str | None = None,
                region_row: int | None = None, top_n: int = 8,
                sort: bool = True, dry_run: bool = True, detailed: bool = False):
    from san9 import movecmd, searchcmd

    w = runtime.window()
    focused = runtime.ensure_foreground(w)
    out: dict = {"focused": focused, "dry_run": bool(dry_run),
                 "irreversible_note": SEARCH_IRREVERSIBLE,
                 "sort_note": SEARCH_SORT_NOTE,
                 "region_row_arg": region_row}

    def _refocus():
        """⭐ 每个改屏步骤之前重申前台 —— `winio.press` 走 SendInput，只送到前台窗口。
        本工具一次调用连着做五步、跑几十秒，期间焦点可能被抢走。
        ⛔ 2026-09-19 真机症状：`select_target` 连按 3 轮 `up×3` 高亮一动不动、
        看起来像\"方向键坏了\"，其实是键没送到游戏。（那次的完整教训见 docs/04 §12.13）
        """
        try:
            return runtime.ensure_foreground(w)
        except Exception:
            return None

    def _step(p):
        """步骤结果 → 给调用方看的小结。

        ⛔ **不要用 `act._target_reading`** —— 它吃的是**目标列表解析结果**
        （`rows` / `unread` 那套键），把别的字典塞进去会输出误导性的
        「屏上 0 行（自势力设施）」。2026-09-20 我自己踩过一次。
        """
        if not isinstance(p, dict):
            return p
        if detailed:
            return p
        keep = ("ok", "why", "step", "note", "label", "clicked", "gate", "gate_polls",
                "names_before", "names_after", "order_changed", "table_after",
                "already_sorted", "clicks",
                "direction_before", "direction_after", "direction_final",
                "command_screen_before", "command_screen_after", "picked_region",
                "execute_button", "green_execute", "green_execute_after_wait",
                "red_abort", "recover", "go_clear", "waited_for_bubble_s",
                "checked_after_s", "left_table", "gate_polls",
                "auto_selected", "labels", "n_labels", "hint", "commanded",
                "hint_blocked", "sub_read", "submenu_gate_skipped", "menu", "nav")
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
    if region_row is not None and (isinstance(region_row, bool)
                                   or not isinstance(region_row, int)
                                   or not (0 <= region_row < searchcmd.N_SLOTS)):
        return runtime.fail(
            "`region_row` 必须是 0..%d（表上只有 %d 行），收到 %r。"
            % (searchcmd.N_SLOTS - 1, searchcmd.N_SLOTS, region_row),
            failed_at="arg", reason_code="bad_arg", touched_game=False, **out)
    if (not dry_run) and region_row is None:
        return runtime.fail(
            "`dry_run=false` 必须给 `region_row`（要探索哪个地域）—— "
            "⛔ 不允许在不知道目标的情况下提交。",
            failed_at="missing_region_row", reason_code="bad_arg",
            touched_game=False, **out)

    # ── 1. 打开「探索」命令界面（人材 → 探索）────────────────────────
    _refocus()
    op = searchcmd.step_open_screen(w.hwnd, row, expect_city)
    out["open_screen"] = _step(op)
    if not op.get("ok"):
        shot = runtime.save_shot(w, "search_open_fail")
        return _fail_and_clean(
            "打不开「探索」命令界面：%s ｜ ⭐ **最常见原因：这座城本旬没有可行动的武将**"
            "—— 此时「探索」是灰项，游戏底栏会写「沒有可執行的武將。」"
            % (op.get("why") or "?"),
            failed_at="command_unavailable", reason_code="command_unavailable",
            game_says=op.get("hint"), shot=shot, **out)
    out["commanded"] = op.get("commanded")

    # ── 2. 点「目標地域」→ 出「選擇地域」表 ─────────────────────────
    _refocus()
    tb = searchcmd.step_open_target_table(w.hwnd)
    out["open_table"] = _step(tb)
    if not tb.get("ok"):
        shot = runtime.save_shot(w, "search_table_fail")
        return _fail_and_clean(
            "点「目標地域」之后**没出地域表**：%s" % (tb.get("why") or "?"),
            failed_at="target_table", reason_code="target_table_missing",
            shot=shot, **out)

    # ── 3. ⭐ 点「在野」列标题排序 ─────────────────────────────────
    if sort:
        _refocus()
        so = searchcmd.step_sort_by_talent(w.hwnd)
        out["sort"] = _step(so)
        if not so.get("ok"):
            # ⛔ 排序失败**不当致命错**：不排序也能选行，只是选不到"最好的"。
            #    但必须**明确告诉调用方**，否则会误以为 row0 就是最优。
            out["sort_failed"] = True
            out["sort_failed_why"] = so.get("why")
    else:
        out["sort"] = {"skipped": True,
                       "note": "调用方传 sort=false ⇒ 未排序。**row0 不代表最优**。"}

    # ── 4. 读最上面几行的地域名（给人看 / 给调用方挑）───────────────
    k = max(1, min(int(top_n or 8), searchcmd.N_SLOTS))
    names = searchcmd.read_row_names(w.hwnd, list(range(k)))
    out["top_regions"] = [
        {"row": r["row"], "name": r["name"], "status": r["status"],
         "nearest": r.get("nearest"), "raw": r["raw"]} for r in names]
    out["top_regions_note"] = (
        "⭐ 已按「在野」降序 ⇒ **越靠前 = 该地域在野人才越多**。"
        "⚠️ 具体人数本工具**不读**（数字列，字形库没有这个字号）—— 需要确切数字请看屏幕，"
        "或先补 D8 字形库。"
        + ("⚠️ 本次**排序失败**，上面的顺序不是按在野排的！" if out.get("sort_failed") else ""))
    if not any(r["status"] == "locked" for r in names):
        out["names_warning"] = ("前 %d 行的地域名**一个都没锁上**（都是 %s）"
                                "⇒ 名字仅供参考，⛔ 别拿去当断言。"
                                % (k, sorted({r["status"] for r in names})))

    # ── 5. 只读侦察模式：到此为止，退回 ───────────────────────────
    if region_row is None:
        _refocus()
        out["closed"] = _step(searchcmd.step_abort(w.hwnd))
        out["ok_but_no_submit"] = True
        out["note"] = ("只读模式：已开表 + 排序 + 读出最上面 %d 行的地域名，**没提交任何命令**。"
                       "下一步请传 `region_row`（建议 0 = 在野最多的地域）。" % k)
        if not out["closed"].get("ok"):
            return runtime.fail(
                "数据读到了，但**退屏没成功** ⇒ 先调 `san9_recover`。（%s）"
                % out["closed"].get("why"),
                failed_at="close", reason_code="close_failed", **out)
        return runtime.ok(**out)

    # ── 6. 鼠标点选那一行（点完自动返回命令界面）────────────────────
    _refocus()
    pk = searchcmd.step_pick_row(w.hwnd, region_row)
    out["pick_row"] = _step(pk)
    if not pk.get("ok"):
        shot = runtime.save_shot(w, "search_pick_fail")
        return _fail_and_clean(
            "选地域行失败：%s" % (pk.get("why") or "?"),
            failed_at="pick_row", reason_code="pick_row_failed",
            shot=shot, **out)
    out["target_region"] = pk.get("picked_region")

    # ── 7. 判「執行」是否变绿（游戏自己给的硬信号）────────────────
    ready = pk.get("execute_button")
    out["execute_ready"] = bool(ready)
    if not ready:
        # 目标选上了、但「執行」还灰着 ⇒ 多半是"执行武将没定"（本旬 ≥2 人时要手动选）
        _refocus()
        out["closed"] = _step(searchcmd.step_abort(w.hwnd))
        return runtime.fail(
            "目标地域选上了（%s），但**「執行」还是灰的** ⇒ 目标或执行武将没齐。"
            "⭐ 若该城本旬有 **≥2** 名可行动武将，游戏**不会**自动填执行武将，"
            "需要先点「執行武將」标签选人 —— ⛔ **这条路本工具还没做**"
            "（真机只验过\"本旬只剩 1 人 ⇒ 自动填充\"那种）。"
            "换个只剩 1 人可动的城，或等人少时再试。"
            % (pk.get("picked_region", {}).get("name") or "?"),
            failed_at="execute_not_ready", reason_code="execute_not_ready",
            **out)

    # ── 8. dry_run：中止退回 ──────────────────────────────────────
    if dry_run:
        _refocus()
        out["closed"] = _step(searchcmd.step_abort(w.hwnd))
        out["note"] = ("dry_run：已开到「執行」变绿（起点=%s、目标地域=%s），"
                       "**没有提交**，已退回。要真提交请传 `dry_run=false`。"
                       % (out.get("commanded"), out.get("target_region") or {}))
        if not out["closed"].get("ok"):
            return runtime.fail(
                "dry_run 收尾退屏失败 ⇒ 先调 `san9_recover`。（%s）" % out["closed"].get("why"),
                failed_at="close", reason_code="close_failed", **out)
        return runtime.ok(**out)

    # ── 9. 真提交 ─────────────────────────────────────────────────
    _refocus()
    ex = searchcmd.step_execute(w.hwnd)
    out["execute"] = _step(ex)
    if not ex.get("ok"):
        shot = runtime.save_shot(w, "search_execute_fail")
        return _fail_and_clean(
            "点「執行」之后没回到干净战略面：%s ｜ ⚠️ 提交前后各有一个**信息对话框**"
            "（軍師提示 / 武将回应），它们**几秒自动消失**，本工具**不会**去按 Enter 猜默认按钮。"
            "请看一眼屏幕再决定。" % (ex.get("why") or "?"),
            failed_at="execute", reason_code="execute_unconfirmed",
            shot=shot, **out)
    out["closed"] = {"ok": True, "how": "点「執行」后底栏回「請選擇命令起點」（游戏自己关的）"}
    out["committed"] = True
    out["note"] = ("已提交：**%s** 派出一名武将去探索 **%s**。"
                   "⚠️ 两名候选/在野名单本工具不读；过旬后用 `san9_journal` 看游戏写的结果。"
                   "⛔ 别忘了这条命令占了那名武将本旬的行动。"
                   % (out.get("commanded"),
                      (out.get("target_region") or {}).get("name") or "?"))
    return runtime.ok(**out)
