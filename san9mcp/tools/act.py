# -*- coding: utf-8 -*-
"""F 组 · 内政（設施命令）。

## 两轮式（README 第三节的「降级模型」）

| 你怎么调 | 发生什么 |
|---|---|
| 只给 `row` + `command` | **第 1 轮**：打开到"选将"这一步 → 把**候选名单**端出来，**不选、不提交**，返回 `session` |
| 带 `session` + `officers` 回来 | **第 2 轮**：选行 → 決定 → 執行 → 读结果框 |
| 直接给 `officers="all"` 或 `[0,2]` | **一把梭**：内部把两轮连起来跑完 |

**为什么拆两轮**：名单**每旬都在变**，agent 不看一眼没法决定派谁。
按行号选 —— 行号是准的；**名字只用来"给人看"**（实测 4 行只读出 2 个名字，
所以绝不按名字匹配，名字读不出就给 `null`，不编）。

## 返回值里三样是硬证据

1. `commanded` —— 游戏自报的设施名（"点对了没有"）
2. `verify.text` —— **游戏自己报的结果播报框**，例如
   「平原的民心變為375(+15)，增加兵役變為11498(+460)」。
   **設施 9 条是「立即」见效 ⇒ 数字闭环当场成立，不用等过旬。**
3. `failed_at` —— 挂在第几步
"""
from __future__ import annotations

from san9 import atoms, cmdscreen

from san9mcp import panels, registry, runtime

UI_CHOOSE_LOCKED = "locked"
UI_CHOOSE_NONCLICKABLE = frozenset(("candidate", "unknown", "unread"))


def _ui_choose_int(value, default=None):
    if isinstance(value, bool):
        return default
    if isinstance(value, float) and not value.is_integer():
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _ui_choose_status(item):
    status = item.get("name_status", item.get("status"))
    if item.get("unread") or status == "unread":
        return "unread"
    if status in UI_CHOOSE_NONCLICKABLE or status == UI_CHOOSE_LOCKED:
        return status
    return "unknown"


def normalize_ui_choose_rows(rows):
    """Return a side-effect-free, zero-based row view for D27."""
    if not isinstance(rows, (list, tuple)):
        return [], ["rows must be a list"]
    normalized, errors, seen = [], [], set()
    for position, item in enumerate(rows):
        if not isinstance(item, dict):
            errors.append("row %d is not an object" % position)
            continue
        number = _ui_choose_int(item.get("row", item.get("index", position)))
        if number is None or number < 0:
            errors.append("row %d has no non-negative zero-based row" % position)
            continue
        if number in seen:
            errors.append("duplicate row %d" % number)
            continue
        seen.add(number)
        status = _ui_choose_status(item)
        normalized.append({
            "row": number,
            "name": item.get("name") if status == UI_CHOOSE_LOCKED else None,
            "status": status,
            "visible": item.get("visible", True) is not False,
            "brightness_peak": item.get("brightness_peak", item.get("brightness")),
            "ink": item.get("ink", item.get("ink_amount", item.get("ink_peak"))),
        })
    normalized.sort(key=lambda item: item["row"])
    return normalized, errors


def confirm_ui_choose_highlight(rows, *, current_row=None,
                                brightness_margin=1.0, min_ink=1):
    """Confirm the selected row from brightness peak plus non-zero ink."""
    normalized, errors = normalize_ui_choose_rows(rows)
    if errors:
        return {"ok": False, "why": "; ".join(errors), "rows": normalized}
    measured = [r for r in normalized if r["visible"] and r["brightness_peak"] is not None]
    if not measured:
        return {"ok": False, "why": "missing brightness peak evidence", "rows": normalized}
    try:
        measured = [dict(r, brightness_peak=float(r["brightness_peak"])) for r in measured]
    except (TypeError, ValueError):
        return {"ok": False, "why": "brightness peak is not numeric", "rows": normalized}
    measured.sort(key=lambda item: item["brightness_peak"], reverse=True)
    best = measured[0]
    second = measured[1]["brightness_peak"] if len(measured) > 1 else None
    try:
        required_margin = float(brightness_margin)
        required_ink = int(min_ink)
    except (TypeError, ValueError):
        return {"ok": False, "why": "highlight thresholds are not numeric",
                "rows": normalized}
    if required_margin < 0 or required_ink < 0:
        return {"ok": False, "why": "highlight thresholds must be non-negative",
                "rows": normalized}
    if second is not None and best["brightness_peak"] - second < required_margin:
        return {"ok": False, "why": "brightness peak is ambiguous", "rows": normalized,
                "peaks": [(r["row"], r["brightness_peak"]) for r in measured]}
    ink = _ui_choose_int(best["ink"])
    if ink is None or ink < required_ink:
        return {"ok": False, "why": "highlight row has insufficient ink evidence",
                "rows": normalized, "row": best["row"], "ink": best["ink"]}
    if current_row is not None and _ui_choose_int(current_row) != best["row"]:
        return {"ok": False, "why": "current_row disagrees with brightness peak",
                "rows": normalized, "row": best["row"]}
    return {"ok": True, "row": best["row"],
            "brightness_peak": best["brightness_peak"], "ink": ink,
            "rows": normalized}


def resolve_ui_choose_target(rows, target):
    """Resolve a target to a locked zero-based row only."""
    normalized, errors = normalize_ui_choose_rows(rows)
    if errors:
        return {"ok": False, "why": "; ".join(errors), "rows": normalized}
    if isinstance(target, bool) or target is None:
        return {"ok": False, "why": "target must be a locked name or zero-based row"}
    if isinstance(target, int):
        matches = [item for item in normalized if item["row"] == target]
        if not matches:
            return {"ok": False, "why": "zero-based row %d is not present" % target}
        selected = matches[0]
        if selected["status"] != UI_CHOOSE_LOCKED:
            return {"ok": False, "why": "row %d is %s and is not clickable" %
                    (target, selected["status"]), "row": target}
        if not selected["name"]:
            return {"ok": False, "why": "locked row %d has no locked name" % target,
                    "row": target}
        return {"ok": True, "row": selected["row"], "name": selected["name"]}
    if isinstance(target, str) and target:
        matches = [item for item in normalized
                   if item["status"] == UI_CHOOSE_LOCKED and item["name"] == target]
        if len(matches) != 1:
            return {"ok": False, "why": "locked name %r is not uniquely present" % target}
        return {"ok": True, "row": matches[0]["row"], "name": target}
    return {"ok": False, "why": "target must be a locked name or zero-based row"}


def _ui_choose_scroll_spec(scroll, viewport_size, total_rows):
    if scroll is None:
        return {"mode": "none", "page_size": viewport_size or total_rows, "max_pages": 0}, None
    if not isinstance(scroll, dict):
        return None, "scroll must be a declaration object"
    mode = scroll.get("mode", "page")
    if mode not in ("page", "scroll"):
        return None, "unsupported scroll mode %r" % mode
    page_size = _ui_choose_int(scroll.get("page_size", viewport_size), 0)
    if page_size <= 0:
        return None, "scroll.page_size must be positive"
    max_pages = _ui_choose_int(scroll.get("max_pages", total_rows), 0)
    if max_pages < 0:
        return None, "scroll.max_pages must be non-negative"
    return {"mode": mode, "page_size": page_size, "max_pages": max_pages}, None


def build_ui_choose_plan(rows, target=None, *, row=None, name=None,
                         current_row=None, viewport_start=0, viewport_size=None,
                         scroll=None, preflight=None, brightness_margin=1.0,
                         min_ink=1):
    """Build a declarative choose plan without game or input side effects."""
    base = {"ok": False, "actions": [], "target": target}
    if preflight is not None and (not isinstance(preflight, dict) or
                                  preflight.get("ok") is not True):
        base["why"] = "preflight failed"
        base["preflight"] = preflight
        return base
    if target is None:
        if row is not None and name is not None:
            base["why"] = "provide either row or name, not both"
            return base
        target = row if row is not None else name
    targets = list(target) if isinstance(target, (list, tuple)) else [target]
    if not targets:
        base["why"] = "target is empty"
        return base
    normalized, errors = normalize_ui_choose_rows(rows)
    if errors or not normalized:
        base["why"] = "; ".join(errors) if errors else "rows is empty"
        return base
    highlight = confirm_ui_choose_highlight(
        rows, current_row=current_row, brightness_margin=brightness_margin, min_ink=min_ink)
    if not highlight["ok"]:
        base["why"] = "highlight preflight failed: %s" % highlight["why"]
        base["highlight"] = highlight
        return base
    resolved = [resolve_ui_choose_target(rows, item) for item in targets]
    if any(not item.get("ok") for item in resolved):
        base["why"] = next(item["why"] for item in resolved if not item.get("ok"))
        base["resolved"] = resolved
        return base
    if len({item["row"] for item in resolved}) != len(resolved):
        base["why"] = "target rows must be unique"
        base["resolved"] = resolved
        return base
    start = _ui_choose_int(viewport_start)
    size = _ui_choose_int(viewport_size)
    if start is None or start < 0 or (size is not None and size <= 0):
        base["why"] = "viewport_start/viewport_size are invalid"
        return base
    scroll_spec, scroll_error = _ui_choose_scroll_spec(scroll, size, len(normalized))
    if scroll_error:
        base["why"] = scroll_error
        return base
    if size is None:
        size = scroll_spec["page_size"]
    current = highlight["row"]
    actions = []
    page_start = start
    for selected in resolved:
        target_row = selected["row"]
        if not page_start <= target_row < page_start + size:
            if scroll_spec["mode"] == "none":
                base["why"] = "target row is off-screen and scrolling was not declared"
                return base
            target_page = (target_row // size) * size
            delta = target_page - page_start
            pages = abs(delta) // size
            if pages > scroll_spec["max_pages"]:
                base["why"] = "scroll declaration does not permit enough pages"
                return base
            actions.append({"op": "scroll_page", "direction": "down" if delta > 0 else "up",
                            "pages": pages, "page_size": size,
                            "reanchor": "first_visible"})
            page_start = target_page
            current = page_start
        steps = target_row - current
        if steps:
            actions.append({"op": "move_highlight", "direction": "down" if steps > 0 else "up",
                            "steps": abs(steps)})
        actions.append({"op": "choose", "row": target_row, "name": selected["name"]})
        current = target_row
    return {"ok": True, "actions": actions, "target": targets,
            "resolved": resolved, "highlight": highlight,
            "preflight": {"ok": True, "rows": len(normalized)},
            "evidence_boundary": "offline declarative plan only; no live-game execution evidence"}


UI_CHOOSE_SCHEMA = {"type": "object", "properties": {
    "rows": {"type": "array", "description": "当前可见行的离线读数；status 必须为 locked 才可点"},
    "target": {"description": "locked 名字、零基行号，或它们的数组"},
    "row": {"type": "integer", "description": "target 的零基行号别名"},
    "name": {"type": "string", "description": "target 的 locked 名字别名"},
    "current_row": {"type": "integer"},
    "viewport_start": {"type": "integer", "default": 0},
    "viewport_size": {"type": "integer"},
    "scroll": {"type": "object", "description": "声明式翻页：mode/page_size/max_pages"},
    "preflight": {"type": "object", "description": "调用方预检证据；非 ok=true 时不产生动作"},
    "brightness_margin": {"type": "number", "default": 1.0},
    "min_ink": {"type": "integer", "default": 1},
}}


@registry.tool(
    "san9_ui_choose", group="D", tier="dev", status="stub",
    summary="D27 离线规划列表选择：按 locked 名字或零基行号定位；用亮度峰值+墨量确认高亮；"
            "支持声明式滚动翻页。candidate/unknown/unread 永不可点击，预检失败 actions=[]。",
    schema=UI_CHOOSE_SCHEMA)
def san9_ui_choose(rows=None, target=None, row=None, name=None,
                   current_row=None, viewport_start=0, viewport_size=None,
                   scroll=None, preflight=None, brightness_margin=1.0, min_ink=1):
    """D27 planner; deliberately does not access runtime, screenshots, or input."""
    return build_ui_choose_plan(
        rows, target, row=row, name=name, current_row=current_row,
        viewport_start=viewport_start, viewport_size=viewport_size,
        scroll=scroll, preflight=preflight,
        brightness_margin=brightness_margin, min_ink=min_ink)

FACILITY_CMDS = ["巡察", "商業", "開墾", "修築", "徵兵", "訓練"]


def _row_of(w, name: str):
    """按名字查行号。⚠️ 设施名 OCR 经常读不全 —— 查不到就让调用方改用 row。"""
    try:
        return cmdscreen.find_city_row(w.hwnd, name)
    except Exception:
        return None


def _parse_officers(v):
    """`officers` → `(select_all, rows_idx)`；不合法返回 `(None, None)`。

    只接受两种形态：
      · `"all"`（或 全部/全选）→ 全选
      · `[0, 2]` 行号数组 → 选这几行
    **不做"first:N"**：它和 `[0,1,2]` 完全等价，多一种语法只会多一种写错的机会。
    """
    if isinstance(v, str):
        return (True, None) if v.strip().lower() in ("all", "全部", "全选") else (None, None)
    if isinstance(v, (list, tuple)):
        if not v:
            return None, None
        try:
            return False, [int(x) for x in v]
        except (TypeError, ValueError):
            return None, None
    return None, None


def _verify_from(r: dict) -> dict:
    """把下层读到的**结果播报框**翻成工具返回里的 `verify`。"""
    rb = r.get("result_box") or {}
    if rb.get("found"):
        return {"source": "游戏自报的结果播报框（最强证据）",
                "text": rb.get("text"),
                "numbers": rb.get("numbers"),
                "界面已恢复干净": rb.get("cleared")}
    return {"source": None,
            "note": rb.get("why") or "没读到结果框（可能还没到生效阶段）"}


def _merge_steps(a: dict, b: dict) -> list:
    return (a.get("steps") or []) + (b.get("steps") or [])


def _fail_and_clean(reason: str, /, **payload):
    """**失败 → 先留证（图已由下层拍好）→ 再清理 → 然后才把结果交出去。**

    ⛔ 顺序不能反：截图必须在 `recover` **之前**拍。
    踩过：拍在之后 = 拍到的是"恢复后的画面" = 等于没留证。

    ⛔ 2026-09-19 补：**这个函数是必须的。** 我把 `san9_facility_command` 改成两轮式时
    直接调 `cmdscreen.run_facility_command`，**把原来的 `atoms.issue` 连带它的 recover 一起丢了**
    → 失败后命令菜单留在屏幕上，而调用方完全不知道 → 违反"失败必清理"。

    `recover` 自己受 CONTRIBUTING 第 0 条约束：只做**已定位的动作**，上限 2 次，
    定位不到就不动手。清不干净时**明确要求人工介入**，不再自动重试。

    ⛔ `reason` 是**位置限定**参数 —— 因为 `payload`（= 下层返回的 dict）里
    本来就有一个 `reason` 键，占着关键字位就会撞车（实测已复现）。
    """
    w = runtime.window()
    rec = atoms.recover(w.hwnd)
    payload["recover"] = rec
    # ⛔ **清理之后，任何挂着的 session 都必须作废。**
    # 界面已经收干净了，上一轮那份"选将名单"对应的现场已经不存在。
    # 不作废的话 agent 会拿着旧 session 回来，面对的却是另一个世界
    # （和"换旬自动作废"同一个道理）。
    panels.clear_all()
    if not rec.get("recovered"):
        reason = ((reason or "失败") +
                  " ｜ ⛔ **界面没清理干净** —— 请人工介入（按 Esc / 点「中止」），"
                  "工具不会再自动重试。")
    return runtime.fail(reason, **payload)


_SCHEMA = {"type": "object", "properties": {
    "session": {"type": "string",
                "description": "第 2 轮用：第 1 轮返回的那个 session id"},
    "row": {"type": "integer",
            "description": "设施在右侧列表的行号（从 san9_look 的 facilities[].row 拿）—— **推荐**"},
    "facility": {"type": "string",
                 "description": "设施名（备选；名字 OCR 可能读不全，读不到就改用 row）"},
    "command": {"type": "string", "enum": FACILITY_CMDS, "default": "巡察"},
    "officers": {"description": "派谁去。**不传** → 返回候选名单让你选；"
                                "传 \"all\" → 全选；传行号数组如 [0,2] → 选这几行"},
}}


@registry.tool(
    "san9_facility_command", group="F", tier="play",
    summary="对某个设施下达**一条内政命令**：巡察 / 商業 / 開墾 / 修築 / 徵兵 / 訓練。"
            "**两轮式**：不传 `officers` 就先返回候选武将名单（不提交任何东西），"
            "带 `session` + `officers`（\"all\" 或 [0,2]）回来才真正下达。"
            "硬证据：`commanded`（设施点对了没）/ `verify.text`"
            "（**游戏自己报的结果**，如「平原的民心變為375(+15)，增加兵役變為11498(+460)」——"
            "設施 9 条立即见效，**数字闭环当场成立，不用等过旬**）/ `failed_at`。",
    schema=_SCHEMA)
def san9_facility_command(session: str | None = None, row: int | None = None,
                          facility: str | None = None, command: str = "巡察",
                          officers=None):
    w = runtime.window()
    focused = runtime.ensure_foreground(w)

    # ================= 第 2 轮：带 session 回来 =================
    if session:
        s = panels.get(session)
        if s is None:
            return runtime.fail(
                "session 过期或不存在（**换旬会自动作废**）",
                hint="重新调一次 san9_facility_command(row=..., command=...) 拿新名单")
        if s.hwnd != w.hwnd:
            panels.close(session)
            return runtime.fail("游戏窗口变了（重开过？）—— session 作废")
        if officers is None:
            return runtime.fail("第 2 轮必须带 officers（\"all\" 或行号数组如 [0,2]）",
                                officers=s.data.get("officers"))
        sel_all, rows_idx = _parse_officers(officers)
        if sel_all is None:
            return runtime.fail('officers 只接受 "all" 或行号数组（如 [0,2]）', got=officers)

        panels.close(session)
        r = cmdscreen.finish_officer_command(w.hwnd, rows_idx=rows_idx,
                                            select_all=sel_all)
        r["session"] = session
        r["row"] = s.data.get("row")
        r["command"] = s.data.get("command")
        r["focused"] = focused
        r["verify"] = _verify_from(r)
        if not r.get("ok"):
            return _fail_and_clean(r.get("reason") or "选将/执行失败", **r)
        r["complete"] = True
        r["next"] = "闭环已成立（见 verify）。想看全局記錄再调 san9_journal。"
        return runtime.ok(**r)

    # ================= 第 1 轮 =================
    if command not in FACILITY_CMDS:
        return runtime.fail("不支持的命令 %r" % command, 支持=FACILITY_CMDS)
    if row is None:
        if not facility:
            return runtime.fail("必须给 row（推荐）或 facility",
                                hint="先调 san9_look 拿 facilities[].row")
        row = _row_of(w, facility)
        if row is None:
            return runtime.fail(
                "按名字查不到设施「%s」—— 设施名 OCR 经常读不全" % facility,
                hint="改用 row：先 san9_look，再从 facilities[].row 选一个")

    given = officers is not None
    sel_all, rows_idx = (None, None)
    if given:
        sel_all, rows_idx = _parse_officers(officers)
        if sel_all is None:
            return runtime.fail('officers 只接受 "all" 或行号数组（如 [0,2]）', got=officers)

    # ---- 一把梭：全选 ----
    if given and sel_all:
        r = cmdscreen.run_facility_command(w.hwnd, row, command, select_all=True)
        r.update({"row": row, "command": command, "focused": focused,
                  "officers_mode": "all"})
        r["verify"] = _verify_from(r)
        if not r.get("ok"):
            return _fail_and_clean(r.get("reason") or "命令没下达", **r)
        r["complete"] = True
        return runtime.ok(**r)

    # ---- 打开到"选将"这一步，先读名单 ----
    # （行号模式和"第 1 轮"都要先读名单：前者为了把行号翻成 y，后者为了给 agent 看）
    # `officer_rows=rows_idx` 只在"本旬只剩 1 名武将"那条路上被用到：
    # 那时选将弹窗根本不会开，`_finish_without_picker` 要靠它判断
    # "只选第 N 行"这个请求能不能兑现（只有 [0] 能）。
    r1 = cmdscreen.run_facility_command(w.hwnd, row, command, read_officers=True,
                                        officer_rows=rows_idx)
    r1.update({"row": row, "command": command, "focused": focused})
    if not r1.get("ok"):
        r1["verify"] = _verify_from(r1)
        return _fail_and_clean(r1.get("reason") or "打开命令界面/读名单失败", **r1)

    # ⭐⭐ **本旬只剩 1 名可行动武将**：游戏已自动选中、工具**直接执行了**，
    # 没有第二轮可走（也没有可选项）—— 别再造一个假 session 让 agent 白跑一轮。
    if r1.get("executed"):
        r1.update({"complete": True, "officers_mode": "auto_single"})
        r1["verify"] = _verify_from(r1)
        r1.setdefault("next", "闭环已成立（见 verify）。想看全局記錄再调 san9_journal。")
        return runtime.ok(**r1)

    offs = r1.get("officers") or []

    # ---- 一把梭：按行号 ----
    if given:
        r2 = cmdscreen.finish_officer_command(w.hwnd, rows_idx=rows_idx,
                                              select_all=False)
        r2["steps"] = _merge_steps(r1, r2)
        r2.update({"row": row, "command": command, "officers_mode": rows_idx})
        r2["verify"] = _verify_from(r2)
        if not r2.get("ok"):
            r2["officers"] = offs          # 失败也把名单给回去，方便改正
            return _fail_and_clean(r2.get("reason") or "选将/执行失败", **r2)
        r2["complete"] = True
        return runtime.ok(**r2)

    # ---- 第 1 轮：只给名单，**不提交任何东西** ----
    sid = panels.open_("san9_facility_command", w.hwnd,
                       row=row, command=command, officers=offs)

    # ⛔ **别写 `ok(**r1, stage=...)`** —— `r1` 里本来就有 `stage` 键
    # （`run_facility_command(read_officers=True)` 会设它），
    # 显式关键字和 `**` 展开的同名键撞车 →
    # `TypeError: got multiple values for keyword argument 'stage'`。
    # 而这个异常是在**命令链已经跑完之后**才抛的：界面已经停在选将弹窗上，
    # 但 agent 拿不到 session（因为返回的是错误）。
    # → 统一「**先合并成一个 dict，再解包一次**」，显式的值覆盖 r1 的。
    payload = dict(r1)
    payload.update(
        stage="need_officers",
        complete=False,
        session=sid,
        needs={"officers": '传 "all" 全选，或传行号数组如 [0,2] 选指定的几个'},
        note=("**什么都没提交** —— 命令界面停在选将这一步。"
              "选好之后带 session 回来调一次即可。"
              "行号是准的；名字读不出就是 null（不编）。"),
        next=('san9_facility_command(session="%s", officers="all")   # 或 [0,2]' % sid))
    return runtime.ok(**payload)


@registry.tool(
    "san9_ui_number", group="D", tier="play", status="stub",
    summary="数字输入状态机门禁；尚无经过真机证据验证的适配器，拒绝操作游戏。")
def san9_ui_number(*_a, **_k):
    return runtime.fail("D28 纯状态机已实现，但尚无经过证据验证的真机适配器；拒绝操作游戏")


@registry.tool(
    "san9_trade", group="F", tier="play", status="stub",
    summary="買進 / 賣出（要输数量，还要都市里有商人）。")
def san9_trade(*_a, **_k):
    return runtime.fail("尚未实现：需要先做出 `san9_ui_number`（输入数字）")




@registry.tool("san9_target_force", group="E", tier="dev", status="stub", summary="离线势力目标选择器：校验所在屏、锁定身份、翻页与门禁，不执行输入。")
def san9_target_force(command, observation, target=None, row=None, expect=None, precondition=None, actions=None):
    from san9mcp.tools.target_force import plan_target_force, _MISSING
    kwargs = {"row": row, "expect": expect, "precondition": precondition}
    if actions is not None:
        kwargs["actions"] = actions
    return plan_target_force(command, observation, target, **kwargs)


@registry.tool("san9_target_officer", group="E", tier="dev", status="stub", summary="离线武将目标选择器：仅允许 namelock 锁定名单，支持门禁和声明式滚动，不执行输入。")
def san9_target_officer(command, target, rows, locked_names=None, current_page=0, scroll=None, expect=None, precondition=None):
    from san9mcp.tools.target_officer import select_officer_target
    return select_officer_target(command, target, rows, locked_names=locked_names, current_page=current_page, scroll=scroll, expect=expect, precondition=precondition)

@registry.tool(
    "san9_select", group="E", tier="both", status="stub",
    summary="选中一个设施（按名字）→ 地图滚过去 + 用左上标题核实是谁。")
def san9_select(*_a, **_k):
    return runtime.fail("尚未实现")


# ══════════════════════════════════════════════════════════════════════
# 「一覽」目标设施选择器（分水岭能力：解锁 移動/探索/登庸/輸送 这类要指定目标城的命令）
# ══════════════════════════════════════════════════════════════════════

# 候选城名表。⚠️ **只放实测读到过的名字**（本局移动列表 + 一覽屏 OCR 现量），
# ⛔ 不放"全国城表"那种没见过的名字 —— namelock 的候选表越脏，误锁风险越高。
# `aka` 里的每一条都是**真机 OCR 实读到的错字形**（2026-09-19 一覽屏现量），
# ⛔ 不许靠简繁转换库批量生成，⛔ 不许目测推测 —— 别名表脏了就等于把误锁合法化。
KNOWN_CITIES = [
    "平原", "南皮", "北海", "濮陽", "陳留", "小沛", "鄴",
    # 同一行跨帧两种读法：一帧读对「樂陵港」，另一帧读成「榮陵港」（樂/榮 形近）
    # ⇒ **别名必须覆盖同一行的全部实读变体**，否则工具会时好时坏。
    {"name": "樂陵港", "aka": ["榮陵港", "乐陵港"]},
    "安德港", "高唐港", "白馬港",
    # OCR 把「萊」读成「莱」（简体字形），ratio 只有 0.667 → 卡在 candidate
    {"name": "東萊港", "aka": ["東莱港", "东莱港"]},
    # OCR 丢了首字「臨」，只读出「淄港」，ratio 0.8 → 卡在 candidate
    {"name": "臨淄港", "aka": ["淄港", "临淄港"]},
    "東武港", "東阿港",
]


def _target_reading(p: dict) -> str:
    """把一覽表翻成一句人话。**可信度写在脸上。**"""
    rows = p.get("rows") or []
    locked = [(r["row"], r["city"]) for r in rows if r.get("status") == "locked"]
    cand = [(r["row"], r.get("raw")) for r in rows if r.get("status") == "candidate"]
    unread = p.get("unread") or []
    bits = ["屏上 %d 行（自势力设施）" % len(rows)]
    if p.get("current_row") is not None:
        cur = next((r for r in rows if r.get("is_current")), None)
        bits.append("高亮在第 %d 行（%s）"
                    % (p["current_row"],
                       (cur or {}).get("city") or (cur or {}).get("raw") or "读不出"))
    if locked:
        bits.append("可直接点的 %d 行：%s"
                    % (len(locked), "／".join("%d=%s" % x for x in locked)))
    if cand:
        bits.append("⚠️ 形近没锁上 %d 行（**只作参考，禁止据此当目标**）：%s"
                    % (len(cand), "／".join("%d=%r" % x for x in cand)))
    if unread:
        bits.append("⚠️ 第 %s 行有墨但读不出（多半是单字城名）—— **不是空位，不许当没有**"
                    % unread)
    return "；".join(bits)


@registry.tool(
    "san9_target_own_city", group="E", tier="play",
    summary="**选一座自势力设施当命令目标**（走 軍事→輸送 打开「一覽」目标列表）。"
            "这是「移動/探索/登庸/輸送」这类**需要指定目标城**的命令共用的选择器 —— "
            "在它出现之前，agent 只能下不需要目标的設施命令。"
            "⭐ **默认只读**：开屏 → 读 15 行 → 退回战略面，"
            "**不提交任何命令**（`row=null` 时）。传 `row` 才会把高亮移过去。"
            "⛔ **本工具永远不按 Enter** —— 最后那一下确认留给调用方，"
            "因为按下去就不可逆了。"
            "⭐ 城名走 `namelock` 三级判定：`locked`=精确对上，**只有这类才能当目标**；"
            "`candidate`=形近没对上（实测 `榮陵港`vs樂陵港、`淄港`vs臨淄港），"
            "**给你参考、禁止据此下命令**；有墨读不出的进 `unread`，**不当成空位**。"
            "⛔ **不给数值列**（士兵/傷兵/耐久/在任）—— 本屏没建字形模板库，"
            "项目定案「数字读不出就作废，不猜」。"
            "⚠️ 只读**当前可见的 15 行**，不翻页 —— 找不到某座城**不代表它不存在**。",
    schema={"type": "object", "properties": {
        "row": {"type": ["integer", "null"], "default": None,
                "description": "可选：要选中的行号（**0 起**，0..14）。"
                               "不传时必须传 `expect_city` 才会自动按城市名定位；"
                               "两者都不传=只读当前一览表。⚠️ 每个起点的行号顺序可能不同，"
                               "优先传 `expect_city`，不要复用别的城市的 row。"},
        "expect_city": {"type": ["string", "null"], "default": None,
                        "description": "⭐ **推荐参数**：目标城市名。工具先读当前起点的一览表，"
                                       "自己找出这座城的当前行号，再移动高亮；不需要 agent 自己数行。"
                                       "若同时传 `row`，会做城市名↔行号交叉核对，对不上零动作拒绝。"
                                       "⛔ 只认 `locked` 的行 —— `candidate` 一律拒绝。"},
        "officer_row": {"type": "integer", "default": 0,
                        "description": "輸送要先选一名执行武将（**单选**）。这是选将列表里的"
                                       "行号（0 起）。⚠️ 这个人只是用来把一覽屏**开出来**的，"
                                       "本工具不提交命令 ⇒ 选谁都不影响游戏状态。"},
        "keep_open": {"type": "boolean", "default": False,
                      "description": "⚠️ true=读完/选完**不退屏**，留在一覽屏上（给下一个工具"
                                     "接手按 Enter）。默认 false：退回战略面，不把界面留脏。"},
        "detailed": {"type": "boolean", "default": False,
                     "description": "true=附每一步的画面变化量、底栏原文、门禁分数（排查用）"},
    }})
def san9_target_own_city(row: int | None = None, expect_city: str | None = None,
                         officer_row: int = 0, keep_open: bool = False,
                         detailed: bool = False):
    from san9 import ocr, targetlist

    w = runtime.window()
    focused = runtime.ensure_foreground(w)

    # ── 越界先拦，⛔ 拦下来就一个键都不按 ──────────────────────────
    if row is not None and (not isinstance(row, bool)
                            and isinstance(row, int)
                            and not 0 <= row < targetlist.N_SLOTS):
        return runtime.fail(
            "row=%r 越界：本屏只有 %d 个可见槽位（0..%d）。%s ⇒ **一个键都没按、"
            "界面没动过**。" % (row, targetlist.N_SLOTS, targetlist.N_SLOTS - 1,
                            targetlist.SCROLL_UNVERIFIED),
            failed_at="row_out_of_range", reason_code="row_out_of_range",
            row=row, n_slots=targetlist.N_SLOTS, touched_game=False)

    # ── 1. 开屏（軍事 → 輸送 → 選武將 → 執行）──────────────────────
    op = targetlist.open_via_transport(w.hwnd, officer_row)
    if not op.get("ok"):
        shot = runtime.save_shot(w, "target_open_fail")
        cl = targetlist.close_transport(w.hwnd)
        return runtime.fail(
            op.get("why") or "打开「一覽」目标列表失败",
            failed_at=op.get("stage"), reason_code=op.get("stage"),
            symptom="open_via_transport",
            game_says=(op.get("steps") or [{}])[-1].get("hint"),
            next="看 shot 确认停在哪一屏；界面没清干净就调 san9_recover",
            shot=shot, recover=cl, steps=op.get("steps"),
            officers=op.get("officers"), focused=focused)

    # ── 2. 读表（只读，随时可做）────────────────────────────────────
    bgr = ocr._grab_bgr(w.hwnd)
    p = targetlist.parse_target_list(bgr, cities=KNOWN_CITIES)
    out: dict = {"parsed": p, "opened": True, "focused": focused,
                 "reading": _target_reading(p),
                 "scroll_note": targetlist.SCROLL_UNVERIFIED,
                 "own_faction_only": targetlist.OWN_FACTION_ONLY}
    if not focused:
        out["focus_warning"] = "游戏没在前台 —— 截图拍的是别的窗口，下面的读数不可信"

    # ── 3. 城市名→当前行号：**每次从当前屏现量，不复用旧 row** ───────
    rows = p.get("rows") or []
    if expect_city:
        matches = [r for r in rows if r.get("city") == expect_city
                   and r.get("status") == "locked"]
        if len(matches) == 1:
            located_row = matches[0]["row"]
            out["located"] = {"expect_city": expect_city,
                               "row": located_row,
                               "method": "current_list_locked_city"}
            if row is not None and row != located_row:
                cl = targetlist.close_transport(w.hwnd) if not keep_open else {
                    "skipped": "keep_open=true"}
                return runtime.fail(
                    "当前一览表里「%s」实际是第 %d 行，但你传的是 row=%d ⇒ "
                    "**一个方向键都没按**。每个起点行序不同，请以本次现量为准。"
                    % (expect_city, located_row, row),
                    failed_at="row_city_mismatch", reason_code="row_city_mismatch",
                    want=expect_city, got=located_row, row=row,
                    located_row=located_row, touched_game=False,
                    recover=cl, **out)
            row = located_row
        else:
            # 0 个：不存在/读不出/candidate；>1：候选表异常，均不得点
            desc = [(r.get("row"), r.get("status"), r.get("raw")) for r in rows
                    if r.get("city") == expect_city or r.get("raw") == expect_city]
            cl = targetlist.close_transport(w.hwnd) if not keep_open else {
                "skipped": "keep_open=true"}
            return runtime.fail(
                "当前一览表无法唯一锁定目标「%s」：matches=%d，相关行=%s ⇒ "
                "**一个方向键都没按**。先看 returned `parsed/reading` 再决策。"
                % (expect_city, len(matches), desc),
                failed_at="city_not_uniquely_locked",
                reason_code="city_not_uniquely_locked", want=expect_city,
                matches=desc, touched_game=False, recover=cl, **out)
    elif row is None:
        # 两个都不传 = 合法的两步决策第一步：只读表，退干净战略面
        out["decision_step"] = "list_only"
        out["note"] = ("第一步完成：已读取当前起点的一览表并返回每行城市。"
                        "下一步请传 `expect_city`；工具会按本次列表自己定位 row，"
                        "⛔ 不要从别的城市复用 row。")

    # ── 4. 移高亮（⛔ 永不按 Enter）─────────────────────────────────
    if row is not None:
        sel = targetlist.select_target(w.hwnd, row)
        out["select"] = sel
        if not sel.get("ok"):
            shot = runtime.save_shot(w, "target_select_fail")
            cl = targetlist.close_transport(w.hwnd)
            return runtime.fail(
                sel.get("why") or "移动高亮失败",
                failed_at=sel.get("stage"), reason_code=sel.get("stage"),
                symptom="select_target", shot=shot, recover=cl, **out)
        # 复核：高亮落在哪座城（**再读一次**，不信按键记录）
        p2 = targetlist.parse_target_list(ocr._grab_bgr(w.hwnd), cities=KNOWN_CITIES)
        cur = next((r for r in p2.get("rows") or [] if r.get("is_current")), None)
        out["selected"] = {"row": p2.get("current_row"),
                           "city": (cur or {}).get("city"),
                           "raw": (cur or {}).get("raw"),
                           "status": (cur or {}).get("status")}
        out["parsed"] = p2
        out["reading"] = _target_reading(p2)

    # ── 5. 收尾 ────────────────────────────────────────────────────
    if keep_open:
        out["kept_open"] = True
        out["note"] = ("⚠️ **界面留在「選擇設施」一覽屏上**（keep_open=true）。"
                       "什么都没提交；下一步要么由别的工具按 Enter 确认，"
                       "要么调 san9_recover 收拾。")
    else:
        out["closed"] = targetlist.close_transport(w.hwnd)
        if not out["closed"].get("ok"):
            return runtime.fail(
                "数据读到了，但**退屏没成功** ⇒ 界面还停在輸送链上，先调 san9_recover。"
                "（%s）" % out["closed"].get("why"),
                failed_at="close", reason_code="close_failed",
                symptom="close_transport", **out)
        out["note"] = "已退回干净战略面，**没提交任何命令**。"

    if not detailed:
        out["open_steps_omitted"] = "传 detailed=true 看每步的画面变化量与底栏原文"
    else:
        out["open_steps"] = op.get("steps")
        out["officers_in_picker"] = op.get("officers")
    return runtime.ok(**out)


# ══════════════════════════════════════════════════════════════════════
# `san9_transport` —— 真正把輸送**提交给游戏**（2026-09-19 真机跑通并已生效）
# ══════════════════════════════════════════════════════════════════════
#
# ⚠️⚠️ **本仓第一个不可逆的目标类命令工具。** 提交后游戏状态就变了，
#      不像 `san9_target_own_city`（退屏即零代价）。
#
# ⛔ **本工具还不能选士兵数量。** 数量在**更前面一屏**（选完执行武将之后、
#    选目标城之前）—— 用户 2026-09-19 当场纠正：确认屏上那两个 `1000` 是
#    **地图上两个港口的兵力标注**，跟弹窗无关。⇒ 数量通道待做，
#    现在提交的是游戏给的默认兵力。**这条限制必须写在工具说明里，不许瞒。**

TRANSPORT_IRREVERSIBLE = (
    "⚠️⚠️ **这一步不可逆** —— 点下「執行」命令就进游戏了，Esc 撤不回来。"
    "⇒ 本工具的默认行为是 `dry_run=true`：**开到确认屏、读给你看、然后中止**。"
    "要真提交必须显式传 `dry_run=false`。"
)


@registry.tool(
    "san9_transport", group="F", tier="play",
    summary="**輸送士兵到自势力的另一座设施**（真提交，这是分水岭命令的第一个完整闭环）。"
            "链路 軍事→輸送→选执行武将→一覽选目标城→Enter→「決定方針」确认屏→執行。"
            "⭐ **默认 `dry_run=true`**：一路开到确认屏，把「起点/目标/到達預定/自主撤退」"
            "读给你看，**然后按中止退回**，不碰游戏状态。"
            "⚠️ 传 `dry_run=false` 才真提交 —— **提交后不可逆**。"
            "⭐ 必须传 `soldier_count`：选执行武将后先打开「士兵」数量弹窗、设置并确认数量，"
            "然后才选目标设施；不再跳过数量屏。"
            "⭐ 目标城走 `namelock`，只认 `locked` 行；`expect_city` 对不上**零动作拒绝**。"
            "⭐ 提交成功的判据是**确认屏消失**（底栏不再是「決定部隊行動方針」），"
            "⛔ 不是「点了一下就算成」。收尾判据是底栏回到「請選擇命令起點」。",
    schema={"type": "object", "properties": {
        "row": {"type": ["integer", "null"], "default": None,
                "description": "可选兼容参数：目标城行号（0 起）。⚠️ 每个起点的列表顺序不同，"
                               "不要复用旧 row；优先只传 `expect_city`，工具会先读当前一览表"
                               "并自动定位本次行号。"},
        "expect_city": {"type": ["string", "null"], "default": None,
                        "description": "⭐ **推荐且可单独使用**：目标城市名。工具先读当前起点的一覽表，"
                                       "自己定位本次 row，再选设施；不需要 agent 自己传行号。"
                                       "若同时传 row，会做城市名↔行号交叉核对。确认屏目标也会再核对。"},
        "officer_row": {"type": "integer", "default": 0,
                        "description": "执行武将在选将列表里的行号（0 起）。"
                                       "⚠️ `dry_run=false` 时**这个人会被真派出去**、"
                                       "本旬不能再下别的命令 ⇒ 选之前先想清楚。"},
        "soldier_count": {"type": "integer",
                           "description": "⭐ 本次要输送的士兵数量（正整数）。"
                                          "选将后工具会进入「士兵」弹窗，设置并复核该数量，"
                                          "按弹窗执行，再进入目标设施选择。"},
        "dry_run": {"type": "boolean", "default": True,
                    "description": "⭐ 默认 true = 走到最终确认屏后中止（不提交）。"
                                   "false = 数量已确认、目标已确认后真提交，不可逆。"},
        "detailed": {"type": "boolean", "default": False,
                     "description": "true=附每步画面变化量、底栏原文、门禁判据"},
    }})
def san9_transport(row: int | None = None, expect_city: str | None = None,
                   soldier_count: int | None = None, officer_row: int = 0,
                   dry_run: bool = True, detailed: bool = False):
    import time
    from san9 import cmdscreen, ocr, targetlist, winio

    w = runtime.window()
    focused = runtime.ensure_foreground(w)
    out: dict = {"focused": focused, "dry_run": bool(dry_run),
                 "soldier_count": soldier_count,
                 "irreversible_note": TRANSPORT_IRREVERSIBLE}
    if not isinstance(soldier_count, int) or isinstance(soldier_count, bool) or soldier_count <= 0:
        return runtime.fail(
            "必须传 soldier_count=正整数；这次不再允许跳过士兵数量屏。",
            failed_at="bad_soldier_count", reason_code="bad_arg",
            touched_game=False, **out)

    # ── 0. row 可选；若传则先做类型/范围拦截（零动作）───────────────
    if row is not None and (not isinstance(row, int) or isinstance(row, bool)
                            or not 0 <= row < targetlist.N_SLOTS):
        return runtime.fail(
            "row=%r 越界：一覽屏只有 %d 个可见槽位（0..%d）⇒ **一个键都没按**。"
            % (row, targetlist.N_SLOTS, targetlist.N_SLOTS - 1),
            failed_at="row_out_of_range", reason_code="bad_arg",
            touched_game=False, **out)
    if row is None and not expect_city:
        # 第一阶段允许只读列表，但真提交工具不能没有目标
        pass

    # ── 1. 开到一覽屏 ──────────────────────────────────────────────
    op = targetlist.open_via_transport(w.hwnd, officer_row, stop_after_officer=True)
    out["opened"] = op.get("ok")
    if detailed:
        out["open_steps"] = op.get("steps")
        out["officers_in_picker"] = op.get("officers")
    if not op.get("ok"):
        # ⛔⛔ **取证必须在清理之前** —— 2026-09-19 血案：原来写成
        #    `recover=..., shot=save_shot(...)`，Python 按顺序求值 ⇒ 先 recover 再截图
        #    ⇒ 拍到的是"已经收拾干净的战略面"，**失败现场丢了**，等于没留证。
        shot = runtime.save_shot(w, "transport_open_fail")
        return runtime.fail(
            "没开到「選擇設施」一覽屏（停在 %s）：%s" % (op.get("stage"), op.get("why")),
            failed_at=op.get("stage"), reason_code="open_failed",
            shot=shot, recover=targetlist.close_transport(w.hwnd), **out)

    # ── 2. 选将后设置并确认士兵数量，之后才允许打开目标设施列表 ────
    qty = targetlist.set_soldier_quantity(w.hwnd, soldier_count)
    out["quantity"] = qty
    if not qty.get("ok"):
        shot = runtime.save_shot(w, "transport_quantity_fail")
        return runtime.fail(
            "士兵数量步骤失败（停在 %s）：%s" % (qty.get("stage"), qty.get("why")),
            failed_at=qty.get("stage"), reason_code="quantity_failed",
            touched_game=False, shot=shot,
            recover=targetlist.close_transport(w.hwnd), **out)

    # 选完数量后才点输送执行，进入目标设施列表
    green = cmdscreen.find_button(w.hwnd, "green", 600, 900)
    if green is None:
        return runtime.fail(
            "数量已确认，但输送执行屏找不到绿色执行按钮 ⇒ 不进入目标设施。",
            failed_at="transport_execute", reason_code="button_missing",
            shot=runtime.save_shot(w, "transport_execute_missing"),
            recover=targetlist.close_transport(w.hwnd), **out)
    winio.click_client(w.hwnd, green[0], green[1], settle=0.35)
    time.sleep(1.8)
    b_after_qty = ocr._grab_bgr(w.hwnd)
    gate_after_qty = targetlist.check_target_list(b_after_qty)
    out["target_gate"] = gate_after_qty
    if not gate_after_qty.get("ok"):
        return runtime.fail(
            "数量确认后点执行，未进入目标设施列表：%s" % gate_after_qty.get("why"),
            failed_at="target_list", reason_code="target_gate",
            shot=runtime.save_shot(w, "transport_target_fail"),
            recover=targetlist.close_transport(w.hwnd), **out)

    # ── 3. 先读当前一览表，再按城市名定位本次 row ───────────────────
    p = targetlist.parse_target_list(ocr._grab_bgr(w.hwnd), cities=KNOWN_CITIES)
    out["reading"] = _target_reading(p)
    rows = p.get("rows") or []
    hit = None
    if expect_city:
        matches = [r for r in rows if r.get("city") == expect_city
                   and r.get("status") == "locked"]
        if len(matches) == 1:
            located_row = matches[0]["row"]
            out["located"] = {"expect_city": expect_city, "row": located_row,
                               "method": "current_list_locked_city"}
            if row is not None and row != located_row:
                return runtime.fail(
                    "当前一览表里「%s」实际是第 %d 行，但你传的是 row=%d ⇒ **一个方向键都没按**。"
                    % (expect_city, located_row, row),
                    failed_at="row_city_mismatch", reason_code="row_city_mismatch",
                    want=expect_city, got=located_row, row=row,
                    located_row=located_row, touched_game=False,
                    recover=targetlist.close_transport(w.hwnd), **out)
            row = located_row
            hit = matches[0]
        else:
            desc = [(r.get("row"), r.get("status"), r.get("raw")) for r in rows
                    if r.get("city") == expect_city or r.get("raw") == expect_city]
            return runtime.fail(
                "当前一览表无法唯一锁定目标「%s」：matches=%d，相关行=%s ⇒ **一个方向键都没按**。"
                % (expect_city, len(matches), desc),
                failed_at="city_not_uniquely_locked",
                reason_code="city_not_uniquely_locked", want=expect_city,
                matches=desc, touched_game=False,
                recover=targetlist.close_transport(w.hwnd), **out)
    elif row is not None:
        hit = next((r for r in rows if r.get("row") == row), None)
    if row is None:
        # 传给輸送工具时必须最终有目标；先读表的能力由 san9_target_own_city 提供
        return runtime.fail(
            "san9_transport 必须传 expect_city（推荐）或 row；先用 san9_target_own_city 读取列表。",
            failed_at="missing_target", reason_code="missing_target",
            touched_game=False, recover=targetlist.close_transport(w.hwnd), **out)
    if hit is None:
        return runtime.fail(
            "第 %d 行不在解析结果里（只解析到 %d 行）⇒ **不动高亮**。"
            % (row, len(rows)),
            failed_at="row_missing", reason_code="parse",
            touched_game=False, recover=targetlist.close_transport(w.hwnd), **out)
    out["target_row"] = hit
    if hit.get("status") != "locked":
        return runtime.fail(
            "第 %d 行的城名是 %s（读作 %r）⇒ **不是 locked，拒绝当目标**。"
            % (row, hit.get("status"), hit.get("raw")),
            failed_at="target_not_locked", reason_code="namelock",
            touched_game=False, recover=targetlist.close_transport(w.hwnd), **out)

    # ── 3. 把高亮移到目标行 ────────────────────────────────────────
    sel = targetlist.select_target(w.hwnd, row)
    out["select"] = sel if detailed else {
        "ok": sel.get("ok"), "final_row": sel.get("final_row")}
    if not sel.get("ok"):
        return runtime.fail(
            "高亮没移到第 %d 行：%s ⇒ **不按 Enter**。" % (row, sel.get("why")),
            failed_at="select_target", reason_code="select_failed",
            shot=runtime.save_shot(w, "transport_select_fail"),  # ⛔ 先取证
            recover=targetlist.close_transport(w.hwnd), **out)
    out["selected"] = {"row": row, "city": hit.get("city")}

    # ── 4. Enter 确认目标 → 进「決定方針」确认屏 ────────────────────
    winio.press("enter")
    time.sleep(1.8)
    bgr = ocr._grab_bgr(w.hwnd)
    gate = targetlist.check_plan_screen(bgr)
    out["plan_gate"] = gate
    if not gate.get("ok"):
        return runtime.fail(
            "按 Enter 后没进「決定方針」确认屏：%s" % gate.get("why"),
            failed_at="plan_gate", reason_code="gate",
            shot=runtime.save_shot(w, "transport_plan_fail"),  # ⛔ 先取证
            recover=targetlist.close_transport(w.hwnd), **out)

    plan = targetlist.read_plan(bgr)
    out["plan"] = plan["fields"]
    out["plan_raw_rows"] = plan["raw_rows"] if detailed else "传 detailed=true 看原始行"
    out["no_soldier_count"] = targetlist.PLAN_NOT_SOLDIER_COUNT

    # ── 5. dry_run：读完就中止退回，⛔ 不提交 ───────────────────────
    if dry_run:
        out["closed"] = targetlist.close_transport(w.hwnd, max_esc=4)
        if not out["closed"].get("ok"):
            return runtime.fail(
                "确认屏读到了，但**退屏没成功** ⇒ 界面还停在輸送链上，"
                "⚠️ 命令**没提交**，先调 san9_recover。（%s）"
                % out["closed"].get("why"),
                failed_at="close", reason_code="close_failed", **out)
        out["note"] = ("✅ **只看没提交**（dry_run）。确认屏的内容在 `plan` 里，"
                       "已退回干净战略面。要真送，重跑一次并传 `dry_run=false`。")
        return runtime.ok(**out)

    # ── 6. 真提交 ──────────────────────────────────────────────────
    ex = targetlist.execute_plan(w.hwnd, expect_target=expect_city)
    out["execute"] = ex
    if not ex.get("ok"):
        return runtime.fail(
            "确认屏上提交失败（停在 %s）：%s" % (ex.get("stage"), ex.get("why")),
            failed_at=ex.get("stage"), reason_code="submit_failed",
            touched_game=ex.get("touched_game"),
            shot=runtime.save_shot(w, "transport_submit_fail"),  # ⛔ 先取证
            recover=targetlist.close_transport(w.hwnd, max_esc=4), **out)

    # ── 7. 收尾：判据是底栏回到「請選擇命令起點」───────────────────
    out["closed"] = targetlist.close_transport(w.hwnd, max_esc=4)
    out["committed"] = True
    out["note"] = ("✅ **輸送命令已提交给游戏**（不可逆）：%s → %s，"
                   "到達預定 %s，自主撤退 %s。"
                   "⚠️ 提交后常弹事件框；收尾以底栏「請選擇命令起點」为判据。"
                   "⭐ **独立证据怎么查**：到達那一旬用 `san9_journal` 会看到游戏自己写"
                   "「因輸送行動，XX 的士兵增為 N」+「YY 完成輸送到 XX 的任務了」。"
                   % (plan["fields"].get("from_raw"),
                      plan["fields"].get("target_raw"),
                      plan["fields"].get("eta_raw"),
                      plan["fields"].get("retreat_raw")))
    return runtime.ok(**out)
