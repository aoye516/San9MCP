# -*- coding: utf-8 -*-
"""F 组 · 軍事命令：`san9_deploy`（出征）—— **完整链路**。

⭐ 用户 2026-09-20 定的范围：
   ·「你只做执行武将+选士兵数量的基础版工具就行」（第一步）
   ·「出征还没搞定，得到真正能打仗为止，把出征做完，并且要学会输入士兵数量，
     比如会输入8000这种，mcp工具得能指定数量才行」（第二版 ⇒ 补完目标选择）

真机全链（2026-09-20 双向验收过）：

    点城 → 軍事 → 出征 → 点「執行武將」→ 勾选 → 「決定」
      → ⭐ 游戏自动填 大將/陣形/船/士兵/派生数值，绿「執行」自己亮
      → （可选）点「士兵」→「輸入數字」对话框（清去 + 逐位点数字键）→「執行」
      → 点绿「執行」→ **自动弹出「選擇對象」目标表（15 行）**
      → 点目标行 → **直接弹出「決定方針」窗**（实测：不是先走地图）
      → （可选）改 4 组二选一 → 点绿「執行」= **真正发兵**

为什么单独一个模块：同 `san9_move` / `san9_search` / `san9_dengyong`。
"""
from __future__ import annotations

from san9mcp import registry, runtime


def _fail_clean(reason: str, /, **payload):
    """失败 → **先留证（图由调用方已拍）→ 再清理** → 才把结果交出去。

    ⭐ 与 `act._fail_and_clean` 的区别：**先走 `deploy.step_back_out`（分级退屏）**。
    ⛔⛔ 为什么必须这样（2026-09-20 踩实）：出征链路会停在**「決定方針」窗**上，
       `atoms.recover` 只会按 Esc，**方针窗不吃 Esc** ⇒ 清不掉，
       报"界面没清理干净"并把游戏停在出征屏要人接手。
       `step_back_out` 懂三层（方针窗→点中止 / 目标表→Esc / 出征屏→Esc）。
    """
    from san9 import atoms, deploy as _D
    w = runtime.window()
    bo = _D.step_back_out(w.hwnd)
    payload["back_out"] = {k: v for k, v in bo.items()
                           if k in ("ok", "actions", "stage_end", "why")}
    if not bo.get("ok"):
        rec = atoms.recover(w.hwnd)
        payload["recover"] = rec
        if not rec.get("recovered"):
            reason = ((reason or "失败") +
                      " ｜ ⛔ **界面没清理干净**（停在 %s）—— 请人工介入"
                      "（按 Esc / 点「中止」），工具不会再自动重试。" % bo.get("stage_end"))
    try:
        from san9mcp import panels
        panels.clear_all()
    except Exception:
        pass
    return runtime.fail(reason, **payload)

DEPLOY_IRREVERSIBLE = (
    "⚠️⚠️ **真正的提交点是「決定方針」窗里的绿「執行」**（不是出征屏那个「執行」，"
    "那个只是去选目标）。本工具 `dry_run=True`（默认）**全程不提交**："
    "配好部队 → 点开出征屏「執行」看目标表 → 若有 `target_row` 就点进去看方針 → 然后中止退回。"
    "只有 `dry_run=false` 且给了 `target_row` 才真的发兵。")

DEPLOY_FLOW_NOTE = (
    "⭐⭐ 出征和前三条命令（移動/探索/登庸）**结构完全不同**：全屏面板、左侧一列字段、"
    "**没有任何彩色标签**；目标是在点完出征屏「執行」后**自动弹出的「選擇對象」表**里点一行选的"
    "（点一行就进「決定方針」窗）。"
    "⛔ 别拿 `looks_like_move_screen` 那套判据套它。")

DEPLOY_TARGET_NOTE = (
    "⭐ **目标表（「選擇對象」）小字常读不出**（实测 `薊`/`鄴`/`平原`/`下邳` 这类读不出，"
    "被高亮的那一行更是几乎读不出）—— 但**不影响操作**：选目标按 `row`，"
    "点进去后会用方针窗里**大字号**的「目標/勢力」回读核对，`expect_target` 就是干这个的。")

DEPLOY_STEP_NOTE = (
    "⭐ 提交后**还要再点一次战略面的「進行」**部队才真的开始走。")


@registry.tool(
    "san9_deploy", group="F", tier="play",
    summary="**出兵（出征）**（軍事 → 出征）—— 完整链路："
            "**选执行武将 →（可选）指定士兵数量 → 选目标 →（可选）改方针 → 发兵**。"
            "链路：点城→菜单→軍事→出征→点「執行武將」→勾选武将→「決定」"
            "→（可选）点「士兵」输数量→点「執行」→目标表→点目标行→方針窗"
            "→（可选）改方针→「執行」（=真正发兵）。"
            "⭐⭐ **选完武将 + 決定 之后游戏会把 大將/陣形/船/士兵/派生数值全自动填好**，绿「執行」自己就亮。"
            "⭐ **两步用法**：①不传 `target_row` ⇒ 配好部队后点开目标表，**只把 15 行目标读给你**然后退回；"
            "②传 `target_row` ⇒ 点进「決定方針」窗并把方針读给你。"
            "⭐ **默认 `dry_run=true` 全程不发兵**；要真发兵传 `dry_run=false` + `target_row`。"
            "⚠️ 发兵后还要在战略面点一次「進行」部队才开动。",
    schema={"type": "object", "properties": {
        "row": {"type": "integer",
                "description": "⭐ **執行設施**（从哪座城出兵）在右侧「移動列表」里的行号（0 起）。"
                               "先调 `san9_look` 拿 `facilities[].row`。"
                               "⛔ 不要复用上次看到的行号 —— 列表顺序会变。"},
        "expect_city": {"type": ["string", "null"], "default": None,
                        "description": "把执行设施的城名读出来核对（防止行号串了）。"},
        "officers": {"type": ["array", "string", "null"], "default": "all",
                     "description": "要出征的执行武将：行号数组（如 [0] 或 [0,1]）或 \"all\"（默认，全带）。"
                                    "行号 = 「選擇武將」弹窗里的顺位（0 起）。"
                                    "⛔ **1 部队最多 5 人**（游戏限制）。"},
        "soldiers": {"type": ["integer", "string", "null"], "default": None,
                     "description": "士兵数量。传整数（如 8000）= 指定人数（工具会「清去」再逐位点数字键）；"
                                     "传 \"max\" = 每个武将带满；传 null（默认）= 不动它（用游戏自动填的）。"},
        "target_row": {"type": ["integer", "null"], "default": None,
                       "description": "⭐ **目标**在「選擇對象」表里的行号（0 起，共 15 行）。"
                                      "不传 ⇒ 只读回目标表（不发兵、不进方针窗）。"
                                      "传了 ⇒ 点进「決定方針」窗。⛔ 行号不许猜，用上一次调用的 `targets[]`。"},
        "expect_target": {"type": ["string", "null"], "default": None,
                          "description": "目标城名核对（如 \"洛陽\"）。工具会在方针窗用大字号回读「目標」比对；"
                                         "对不上就**停手**（不确认）。强烈建议传 —— 目标表小字常读不出，"
                                         "这一步是唯一可靠的核对点。"},
        "houshin": {"type": ["object", "null"], "default": None,
                    "description": "可选：改方针。键 ∈ 敵接近時/自主撤退/追擊/事後命令，"
                                   "值 ∈ 每组的两个标签（敵接近時: 攻擊|無視；自主撤退/追擊: 許可|不許可；"
                                   "事後命令: 攻擊|撤退）或 \"1\"/\"2\"（左/右格）。"
                                   "不传 ⇒ 用游戏默认（攻擊/攻擊/許可/許可/攻擊）。"},
        "dry_run": {"type": "boolean", "default": True,
                    "description": "⭐ 默认 true = **全程不发兵**（配好部队、把目标表/方针窗读给你看，然后中止退回）。"
                                   "false = 真的点方针窗的「執行」发兵（**不可逆**，必须同时给 `target_row`）。"},
        "detailed": {"type": "boolean", "default": False,
                     "description": "true=附每步的原始证据（字段读数、结构判据、方針全量）"},
    }})
def san9_deploy(row: int | None = None, expect_city: str | None = None,
                officers="all", soldiers=None, target_row=None,
                expect_target: str | None = None, houshin=None,
                dry_run: bool = True, detailed: bool = False):
    from san9 import deploy

    w = runtime.window()
    focused = runtime.ensure_foreground(w)
    out: dict = {"focused": focused, "dry_run": bool(dry_run),
                 "officers_arg": officers, "soldiers_arg": soldiers,
                 "target_row": target_row, "expect_target": expect_target,
                 "irreversible_note": DEPLOY_IRREVERSIBLE,
                 "flow_note": DEPLOY_FLOW_NOTE,
                 "target_note": DEPLOY_TARGET_NOTE,
                 "step_note": DEPLOY_STEP_NOTE}

    def _refocus():
        """⭐ 每个改屏步骤之前重申前台（`winio.press` 走 SendInput，只送前台窗口）。"""
        try:
            return runtime.ensure_foreground(w)
        except Exception:
            return None

    def _shot(name):
        return runtime.save_shot(w, "deploy_" + name)

    def _step(p):
        if not isinstance(p, dict) or detailed:
            return p
        keep = ("ok", "why", "step", "note", "clicked", "screen", "picker",
                "left_picker", "fields", "fields_after", "execute_button",
                "dialog", "dialog_after", "left_dialog", "typed", "pressed",
                "recover", "hint", "commanded", "rows", "menu", "nav",
                "targets", "houshin", "read", "target_name_read", "already",
                "after", "committed", "warn")
        return {k: v for k, v in p.items() if k in keep}

    # ── 0. 参数校验：⛔ 一个键都不按 ────────────────────────────────
    if row is None or isinstance(row, bool) or not isinstance(row, int):
        return runtime.fail(
            "必须传 `row` = **執行設施**（从哪座城出兵）在右侧列表里的行号（0 起）。"
            "先调 `san9_look`（字段 `facilities[].row`），⛔ 不要复用旧行号。",
            failed_at="arg", reason_code="bad_arg", touched_game=False, **out)
    if row < 0:
        return runtime.fail("`row` 不能是负数（收到 %r）。" % row,
                            failed_at="arg", reason_code="bad_arg",
                            touched_game=False, **out)
    if not dry_run and not isinstance(target_row, int):
        return runtime.fail(
            "`dry_run=false`（真发兵）**必须同时给 `target_row`** —— "
            "否则会点开目标表却不知道该打谁。⛔ 不猜、不发。",
            failed_at="arg", reason_code="bad_arg", touched_game=False, **out)
    if target_row is not None and (isinstance(target_row, bool) or not isinstance(target_row, int)
                                   or not (0 <= target_row < 15)):
        return runtime.fail("`target_row` 要是 0..14 的整数（目标表 15 行），收到 %r。" % target_row,
                            failed_at="arg", reason_code="bad_arg",
                            touched_game=False, **out)
    if houshin is not None and not isinstance(houshin, dict):
        return runtime.fail("`houshin` 要传对象，如 {\"追擊\": \"不許可\"}，收到 %r。" % houshin,
                            failed_at="arg", reason_code="bad_arg",
                            touched_game=False, **out)

    # ── 1. 打开「出征」界面（軍事 → 出征）───────────────────────────
    _refocus()
    op = deploy.step_open_screen(w.hwnd, row, expect_city)
    out["open_screen"] = _step(op)
    if not op.get("ok"):
        shot = _shot("open_fail")
        return _fail_clean(
            "打不开「出征」界面：%s ｜ ⭐ **最常见原因：这座城本旬没有可行动的武将"
            "（或没有士兵）** —— 此时「出征」是灰项，游戏底栏会写出原因。"
            % (op.get("why") or "?"),
            failed_at="command_unavailable", reason_code="command_unavailable",
            game_says=op.get("hint"), shot=shot, **out)
    out["commanded"] = op.get("commanded")

    # ── 2. 点「執行武將」→ 「選擇武將」弹窗 ─────────────────────────
    _refocus()
    pk = deploy.step_open_picker(w.hwnd)
    out["open_picker"] = _step(pk)
    if not pk.get("ok"):
        shot = _shot("picker_fail")
        return _fail_clean(
            "点「執行武將」之后弹窗没开：%s" % (pk.get("why") or "?"),
            failed_at="picker", reason_code="picker_missing", shot=shot, **out)

    # ── 3. 读名单（给调用方看 / 校验行号）─────────────────────────
    from san9 import cmdscreen
    offs = cmdscreen.picker_officers(w.hwnd)
    out["officers"] = [{"row": o.get("row"), "y": o.get("y"), "name": o.get("name")}
                       for o in offs]
    out["n_officers"] = len(offs)
    if not offs:
        _refocus()
        out["closed"] = _step(deploy.step_abort(w.hwnd))
        return runtime.fail(
            "「選擇武將」弹窗里**一个人都没读出来** ⇒ 要么这城本旬没人可出征，"
            "要么弹窗版面变了。⛔ 不猜、不硬点。",
            failed_at="no_officer", reason_code="no_officer", **out)

    if officers == "all" or officers is None:
        rows = list(range(len(offs)))
    elif isinstance(officers, str):
        return runtime.fail("`officers` 只接受行号数组或 \"all\"，收到 %r。" % officers,
                            failed_at="arg", reason_code="bad_arg",
                            touched_game=False, **out)
    else:
        rows = [int(x) for x in officers]
    bad = [r for r in rows if not (0 <= r < len(offs))]
    if bad:
        _refocus()
        out["closed"] = _step(deploy.step_abort(w.hwnd))
        return runtime.fail(
            "`officers` 里的行号越界：%s（弹窗里只有 %d 人）⇒ ⛔ 一个都没勾、已退回。"
            % (bad, len(offs)),
            failed_at="officer_row", reason_code="bad_row", **out)
    if len(rows) > 5:
        out["warn_max5"] = ("⚠️ 游戏限制**1 部队最多 5 人**，你给了 %d 个 ⇒ "
                            "游戏可能只收前 5 个。" % len(rows))

    # ── 4. 勾选 → 「決定」─────────────────────────────────────────
    _refocus()
    sl = deploy.step_select_officers(w.hwnd, rows)
    out["select_officers"] = _step(sl)
    if not sl.get("ok"):
        shot = _shot("select_fail")
        return _fail_clean("勾选武将失败：%s" % (sl.get("why") or "?"),
                               failed_at="select", reason_code="select_failed",
                               shot=shot, **out)
    _refocus()
    dc = deploy.step_decide(w.hwnd)
    out["decide"] = _step(dc)
    if not dc.get("ok"):
        shot = _shot("decide_fail")
        return _fail_clean(
            "点「決定」之后没回到出征屏：%s" % (dc.get("why") or "?"),
            failed_at="decide", reason_code="decide_failed", shot=shot, **out)
    out["fields"] = dc.get("fields")
    out["fields_note"] = ("⭐ 这些字段**是游戏自动填的**（大將=武官爵位最高者、陣形/船/士兵=默认）"
                          "—— 只有想改的时候才需要动它们。")

    # ── 5. 可选：改士兵数量 ──────────────────────────────────────
    if soldiers is not None:
        _refocus()
        sd = deploy.step_set_soldiers(
            w.hwnd, count=None if soldiers == "max" else int(soldiers),
            use="max" if soldiers == "max" else "value", save_shot=_shot)
        out["set_soldiers"] = _step(sd)
        if not sd.get("ok"):
            shot = _shot("soldier_fail")
            return _fail_clean(
                "设置士兵数量失败：%s" % (sd.get("why") or "?"),
                failed_at="soldiers", reason_code="soldiers_failed", shot=shot, **out)
        out["soldiers_now"] = sd.get("typed")
        out["fields_after_soldiers"] = sd.get("fields_after")

    # ── 6. 点出征屏「執行」→ 「選擇對象」目标表 ─────────────────────
    _refocus()
    ex = deploy.step_execute(w.hwnd, save_shot=_shot)
    out["execute"] = _step(ex)
    if not ex.get("ok"):
        shot = _shot("execute_fail")
        return _fail_clean(
            "点出征屏「執行」之后没弹出目标表：%s" % (ex.get("why") or "?"),
            failed_at="execute", reason_code="execute_failed", shot=shot, **out)

    # ── 7. 读目标表 ──────────────────────────────────────────────
    tg = deploy.read_targets(w.hwnd)
    out["targets"] = [{"row": t["row"], "name": t["name"], "force": t["force"],
                       "troops": t["troops"], "morale": t["morale"]} for t in tg]
    out["n_targets"] = len(tg)
    out["highlight_row"] = deploy.highlight_row(w.hwnd)
    out["targets_note"] = ("⭐ 字段 `name` 可能为 null（城名小字常读不出，高亮那行尤其）"
                           "—— 但**不影响选目标**（按 `row`），核对靠下一步 `expect_target`。")

    # ── 8. 不选目标 ⇒ 只读返回（中止退回）────────────────────────
    if target_row is None:
        _refocus()
        if not dry_run:
            return runtime.fail("内部错误：dry_run=false 但没 target_row。",
                                failed_at="arg", reason_code="bad_arg", **out)
        ab = deploy.step_abort_houshin(w.hwnd)   # 方针窗不在也算 ok
        out["abort_houshin"] = _step(ab)
        _refocus()
        out["closed"] = _step(deploy.step_abort(w.hwnd))
        out["note"] = ("只读：部队已配好（%d 名武将），目标表已读回（%d 行），"
                       "**没有选目标、没有发兵**，已退回干净战略面。"
                       "要进方针窗传 `target_row`；要真发兵再加 `dry_run=false`。" % (len(rows), len(tg)))
        if not out["closed"].get("ok"):
            return runtime.fail("收尾退屏失败 ⇒ 先调 `san9_recover`。（%s）" % out["closed"].get("why"),
                                failed_at="close", reason_code="close_failed", **out)
        return runtime.ok(**out)

    # ── 9. 点目标行 → 「決定方針」窗 ─────────────────────────────
    _refocus()
    pt = deploy.step_pick_target(w.hwnd, target_row, expect_target, save_shot=_shot)
    out["pick_target"] = _step(pt)
    if not pt.get("ok"):
        shot = _shot("pick_fail")
        return _fail_clean(
            "选目标失败：%s" % (pt.get("why") or "?"),
            failed_at="pick_target", reason_code="pick_failed", shot=shot, **out)
    out["houshin_read"] = pt.get("read")
    out["target_name_read"] = pt.get("target_name_read")
    if pt.get("hint_warn"):
        out["warn_expect_unread"] = pt["hint_warn"]

    # ── 10. 可选：改方针 ────────────────────────────────────────
    if houshin:
        for field, val in houshin.items():
            _refocus()
            st = deploy.step_set_houshin(w.hwnd, field, val)
            out.setdefault("set_houshin", {})[field] = _step(st)
            if not st.get("ok"):
                shot = _shot("houshin_set_fail")
                return _fail_clean(
                    "改方针「%s」失败：%s" % (field, st.get("why") or "?"),
                    failed_at="houshin", reason_code="houshin_failed", shot=shot, **out)

    # ── 11. dry_run：中止退回（不发兵）────────────────────────────
    if dry_run:
        _refocus()
        out["abort_houshin"] = _step(deploy.step_abort_houshin(w.hwnd))
        _refocus()
        out["closed"] = _step(deploy.step_abort(w.hwnd))
        out["note"] = ("dry_run：部队配好（%d 名武将）、目标第 %s 行（读到的名字：%s）"
                       "、方针窗已看、**没有点「執行」⇒ 兵没发**，已退回干净战略面。"
                       "要真发兵传 `dry_run=false`。"
                       % (len(rows), target_row, out.get("target_name_read")))
        if not out["closed"].get("ok"):
            return runtime.fail("dry_run 收尾退屏失败 ⇒ 先调 `san9_recover`。（%s）" % out["closed"].get("why"),
                                failed_at="close", reason_code="close_failed", **out)
        return runtime.ok(**out)

    # ── 12. 真发兵：点方针窗「執行」（**不可逆**）───────────────────
    _refocus()
    cf = deploy.step_confirm_houshin(w.hwnd, save_shot=_shot)
    out["confirm"] = _step(cf)
    if not cf.get("ok"):
        shot = _shot("confirm_fail")
        return _fail_clean(
            "点方针窗「執行」失败：%s" % (cf.get("why") or "?"),
            failed_at="confirm", reason_code="confirm_failed", shot=shot, **out)
    out["committed"] = True
    out["note"] = ("✅ **命令已发出**（不可逆）：%s → 目标第 %s 行（%s）。"
                   "⚠️ 游戏会弹一句武将台词（会自己消失）。"
                   "⚠️ **还要在战略面点一次「進行」**部队才真的开始移动。"
                   % (out.get("commanded"), target_row, out.get("target_name_read")))
    return runtime.ok(**out)
