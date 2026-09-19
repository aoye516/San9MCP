# -*- coding: utf-8 -*-
"""B 组 · 眼睛（读状态）。**只读，不做任何动作。**

组装方式：几乎全部转发 `san9/` 里已经验证过的能力（`atoms.state` / `atoms.menu` /
`atoms.read_journal` / `ocr.read_topbar`），本包不重新实现读屏。
"""
from __future__ import annotations

from san9 import atoms, cityintel, intel, intel_city, ocr, ui_read

from san9mcp import registry, runtime


@registry.tool(
    "san9_ui_read", group="D", tier="play", status="ready",
    summary="只读当前已打开的 UI：真机已支持 command_menu 与 officer_picker；"
            "其它屏幕明确拒绝。读不出返回 unread/uncertain，不猜、不点击、不关闭。",
    schema={"type": "object", "properties": {
        "evidence": {"type": "object"},
        "screen": {"type": "string", "enum": ["command_menu", "officer_picker"]},
    }})

def san9_ui_read(evidence: dict | None = None, *, screen: str = "command_menu"):
    """读取当前已打开的面板；不打开、不点击、不关闭。

    若调用方没有传结构化 evidence，则只支持从真实的命令菜单现场采集一包
    evidence，再交给纯解析器校验。其它面板仍要求上游适配器先提供 evidence，
    避免在这里猜测屏幕类型、按钮文字或坐标。
    """
    if evidence is None:
        if screen not in ("command_menu", "officer_picker"):
            return runtime.fail(
                "当前真实读屏适配器只支持已打开的 command_menu/officer_picker；其它屏幕必须显式提供 evidence",
                screen=screen,
            )
        w = runtime.window()
        focused = runtime.ensure_foreground(w)
        if not focused:
            return runtime.fail("游戏未能确认在前台，拒绝读取当前屏幕", focused=False)
        try:
            if screen == "officer_picker":
                from san9 import target_officer
                bgr = target_officer._grab(w.hwnd)
                gate = target_officer.check_picker(bgr)
                if not gate.get("ok"):
                    return runtime.fail(
                        gate.get("why") or "当前屏幕不是选将窗口；不打开、不点击、不关闭",
                        focused=True,
                        gate=gate,
                    )
                parsed_picker = target_officer.parse_picker(bgr)
                rows = parsed_picker.get("rows") or []
                evidence = {
                    "screen": {"id": "officer_picker", "ok": True},
                    "buttons": [],
                    "lists": [{
                        "id": "officers",
                        "items": [{
                            "label": row.get("raw"),
                            "raw": row.get("raw"),
                            "index": row.get("row"),
                            "selected": False,
                            "status": row.get("status"),
                        } for row in rows],
                        "complete": True,
                    }],
                    "inputs": [],
                    "highlight": None,
                    "hint": gate.get("hint", ""),
                }
                parsed = ui_read.read_ui(evidence)
                parsed["source"] = "san9.target_officer.parse_picker"
                parsed["focused"] = True
                parsed["gate"] = gate
                parsed["picker"] = parsed_picker
                if parsed.get("ok"):
                    return runtime.ok(**parsed)
                return runtime.fail(parsed.get("why") or parsed.get("status") or "ui_read_failed", **parsed)

            from san9 import cmdmenu
            raw = cmdmenu.read_menu(hwnd=w.hwnd)
            if not raw.get("found"):
                return runtime.fail(
                    "当前屏幕未识别为已打开的命令菜单；不打开、不点击、不关闭",
                    focused=True,
                    raw=raw,
                )
            main = raw.get("main") or []
            sub = raw.get("sub") or []
            main_peaks = [item.get("colorfulness") for item in main]
            numeric_peaks = [float(value) for value in main_peaks
                             if isinstance(value, (int, float))]
            peak_highlight_index = None
            if numeric_peaks:
                peak = max(numeric_peaks)
                tied = [item.get("index") for item in main
                        if isinstance(item.get("colorfulness"), (int, float))
                        and float(item["colorfulness"]) == peak]
                below = [value for value in numeric_peaks if value < peak]
                # 展开的主菜单高亮色明显高于普通绿项；只有唯一峰值且
                # 与次高值拉开差距时才锁定，避免把颜色噪声当成选中态。
                if len(tied) == 1 and peak >= 120 and (not below or peak - max(below) >= 20):
                    peak_highlight_index = tied[0]
            buttons = [{
                "label": item.get("name") or None,
                "raw": item.get("raw") or None,
                "enabled": item.get("enabled"),
                "disabled": item.get("disabled"),
                "highlight": bool(item.get("highlight", False))
                    or item.get("index") == peak_highlight_index,
                "index": item.get("index"),
                "y": item.get("y"),
            } for item in main]
            highlight = None
            if peak_highlight_index is not None:
                highlighted = next((item for item in buttons
                                    if item.get("index") == peak_highlight_index), None)
                if highlighted and highlighted.get("label"):
                    highlight = {"button": highlighted["label"], "source": "colorfulness_peak"}
            facility_names = getattr(atoms, "FACILITY_CMDS", ())
            items = []
            for item in sub[:len(facility_names) or None]:
                index = item.get("index")
                table_name = (
                    facility_names[index]
                    if isinstance(index, int) and 0 <= index < len(facility_names)
                    else None
                )
                raw_name = item.get("name") or None
                # 设施子菜单的行序是游戏固定表；已有 atoms.read_open_menu
                # 用同一张表，并以能读到的行名做对位校验。这里保留 raw，
                # 同时标明表名来源，避免把 OCR 空值伪装成 OCR 成功。
                items.append({
                    "label": raw_name or table_name,
                    "raw": item.get("raw") or None,
                    "label_source": "ocr" if raw_name else "fixed_command_table",
                    "enabled": item.get("enabled"),
                    "disabled": item.get("disabled"),
                    "selected": bool(item.get("highlight", False)),
                    "index": index,
                    "y": item.get("y"),
                })
            evidence = {
                "screen": {"id": "command_menu", "ok": True},
                "buttons": buttons,
                "lists": [{
                    "id": "submenu",
                    "items": items,
                    "complete": True,
                }],
                "inputs": [],
                "highlight": highlight,
                "hint": atoms.hint(w.hwnd),
            }
            parsed = ui_read.read_ui(evidence)
            parsed["source"] = "san9.cmdmenu.read_menu"
            parsed["focused"] = True
            parsed["raw"] = raw
            if parsed.get("ok"):
                return runtime.ok(**parsed)
            return runtime.fail(parsed.get("why") or parsed.get("status") or "ui_read_failed", **parsed)
        except Exception as exc:
            return runtime.fail(
                "读取当前命令菜单失败：%s: %s" % (type(exc).__name__, exc),
                focused=True,
            )
    parsed = ui_read.read_ui(evidence)
    if parsed.get("ok"):
        return runtime.ok(**parsed)
    return runtime.fail(parsed.get("why") or parsed.get("status") or "ui_read_failed", **parsed)


@registry.tool(
    "san9_intel_city", group="C", tier="dev", status="stub",
    summary="只读城市敌情证据：出征/计略/外交前使用；要求城市情报屏门禁，数字不完整即作废，支持合法空城结论。",
    schema={"type": "object", "properties": {
        "observation": {"type": "object"},
        "context": {"type": "string"},
    }, "required": ["observation"]})

def san9_intel_city(observation: dict, context: str | None = None):
    parsed = intel_city.read_city_intel(observation, context=context)
    if parsed.get("ok"):
        return runtime.ok(**parsed)
    return runtime.fail(parsed.get("reason") or parsed.get("status") or "intel_city_failed", **parsed)



def _reading(out: dict) -> str:
    """给 agent 一句人话总结 —— 不用它去拼字段。"""
    tb = out.get("topbar") or {}
    date = tb.get("date") or "".join(
        str(x) for x in (tb.get("year"), tb.get("month")) if x) or "（日期没读出来）"
    fac = out.get("facilities") or []
    bits = [date, "设施 %d 个" % len(fac)]
    if tb.get("gold") is not None:
        bits.append("資金 %s" % tb["gold"])
    if tb.get("food") is not None:
        bits.append("兵糧 %s" % tb["food"])
    if out.get("commanded"):
        bits.append("正在指挥「%s」" % out["commanded"])
    hot = [f for f in fac if f.get("highlight")]
    if hot:
        # 选中行的名字常常读不出来（实测高亮那行 OCR 最不稳）→ 退回报行号，
        # 而不是输出「当前选中「None」」这种废话
        bits.append("当前选中「%s」" % (hot[0].get("name")
                                    or "第 %d 行（名字读不出）" % hot[0]["row"]))
    if out.get("hint"):
        bits.append("提示「%s」" % str(out["hint"])[:28])
    face = out.get("face") or {}
    if face and not face.get("clear"):
        bits.append("⚠️ 有东西挡住战略面（go=%.2f）" % (face.get("go_score") or 0.0))
    return "；".join(bits)


@registry.tool(
    "san9_look", group="B", tier="both",
    summary="⭐ 一站式总观测（agent 的眼睛）：是否干净战略面 · 年月/信望/資金/兵糧 · "
            "设施列表（含哪个被选中、有没有可行动武将）· 当前指挥对象 · 底部提示 · 截图路径。"
            "**每旬开局先调这个。**",
    schema={"type": "object", "properties": {
        "scope": {"type": "string", "enum": ["brief", "full"], "default": "brief",
                  "description": "brief=常用字段；full=另附每个锚点的原始分数（排查用）"},
        "with_shot": {"type": "boolean", "default": True,
                      "description": "是否存一张截图（多模态 agent 可以看）"},
    }})
def san9_look(scope: str = "brief", with_shot: bool = True):
    w = runtime.window()
    focused = runtime.ensure_foreground(w)
    out: dict = {"focused": focused}
    if not focused:
        out["focus_warning"] = "游戏没能在前台 —— 截图拍的是别的窗口，下面的读数不可信"

    p = runtime.probe(w)
    out["face"] = {k: p[k] for k in ("on_strategy_face", "clear", "go_score", "blocked")}
    if scope == "full":
        out["anchors"] = p["anchors"]

    try:
        st = atoms.state(w.hwnd)
    except Exception as e:
        out["reading"] = "读设施列表失败"
        return runtime.fail("读设施列表抛异常：%s: %s" % (type(e).__name__, e),
                            **out)
    out["topbar"] = st.get("topbar") or {}
    out["facilities"] = st.get("facilities") or []
    out["commanded"] = st.get("commanded")
    out["hint"] = st.get("hint") or ""
    if with_shot:
        out["shot"] = runtime.save_shot(w, "look")
    out["reading"] = _reading(out)

    if not st.get("ok"):
        return runtime.fail(st.get("error") or "读设施列表失败", **out)
    if not p["on_strategy_face"]:
        return runtime.fail(
            "不在战略面（或在战略面但身份锚点没认出来）—— 先用 san9_recover 或 san9_shot 看现场",
            **out)
    return runtime.ok(**out)


@registry.tool(
    "san9_shot", group="B", tier="both",
    summary="截图存文件，返回路径 + 识别到的界面名。给多模态 agent 看现场用。"
            "**注意：只给「看」，不要靠它目测坐标去点东西**（显示端会缩放，目测误差 >200px）。")
def san9_shot(tag: str = "shot"):
    w = runtime.window()
    focused = runtime.ensure_foreground(w)
    p = runtime.probe(w)
    path = runtime.save_shot(w, tag)
    return runtime.ok(path=path, focused=focused,
                      face={k: p[k] for k in ("on_strategy_face", "clear", "go_score")},
                      note="path 是相对数据目录的路径；需要绝对路径请拼 SAN9_DATA")


@registry.tool(
    "san9_topbar", group="B", tier="play",
    summary="只读顶栏四个数字：年月 / 信望 / 資金 / 兵糧（带位数校验，读错会被标出来）。"
            "比 san9_look 便宜，适合每旬快速看一眼。")
def san9_topbar():
    w = runtime.window()
    focused = runtime.ensure_foreground(w)
    tb = ocr.read_topbar(w.hwnd)
    d = {k: v for k, v in tb.items() if k != "raw"}
    d["raw"] = tb.get("raw")
    return runtime.ok(focused=focused, **d)


@registry.tool(
    "san9_menu", group="B", tier="play",
    summary="打开某个设施的**命令菜单**并读出：设施情报面板（民心/在任/士兵/金/糧…）+ "
            "六类主菜单 + 「設施」8 个子项**哪些可用哪些置灰**。读完自动关掉。"
            "用来回答「我现在这个城能干什么」。",
    schema={"type": "object", "properties": {
        "row": {"type": "integer",
                "description": "设施在右侧列表里的**行号**（从 san9_look 的 facilities[].row 拿）。推荐用法。"},
        "facility": {"type": "integer",
                     "description": "设施序号（旧接口，走名字解析，不可靠 —— 优先用 row）"},
    }})
def san9_menu(row: int | None = None, facility: int = 0):
    w = runtime.window()
    focused = runtime.ensure_foreground(w)
    r = atoms.menu(w.hwnd, row if row is not None else facility)
    r["focused"] = focused
    if not r.get("ok"):
        why = r.get("why") or "菜单没打开/没读出来"
        rec = r.get("recover") or {}
        if rec and not rec.get("recovered"):
            why += (" ｜ ⛔ **界面没清理干净** —— 请人工介入（按 Esc / 点「中止」），"
                    "工具不会再自动重试。")
        return runtime.fail(why, **r)
    return runtime.ok(**r)


@registry.tool(
    "san9_panel", group="B", tier="play",
    summary="读某个设施的**左上情报面板**（17 项，固定行表）：民心/收益/收穫/耐久/士氣/"
            "人口/兵役人口/士兵/傷兵/增加兵役/資金收入/兵糧收入/商人/市價/在任/俘虜/在野。"
            "比 san9_menu 便宜（不读主菜单、不展开子菜单）。"
            "⚠️ 面板只在命令菜单开着时才存在，所以本工具会开一次菜单再关掉"
            "（路径与 san9_menu 相同，已验证）。**只读，不下达任何命令。**",
    schema={"type": "object", "properties": {
        "row": {"type": "integer",
                "description": "设施在右侧列表里的**行号**（从 san9_look 的 facilities[].row 拿）"},
        "expect": {"type": "string",
                   "description": "期望的设施名（强烈建议传）：工具会用面板标题核对，不一致会 warn"},
        "detailed": {"type": "boolean", "default": False,
                     "description": "true=附每行原始 OCR 文本 / 锚点对位 / 墨迹计数（排查用）"},
    }, "required": ["row"]})
def san9_panel(row: int, expect: str | None = None, detailed: bool = False):
    w = runtime.window()
    focused = runtime.ensure_foreground(w)
    r = atoms.read_panel_only(w.hwnd, row, expect=expect)
    r["focused"] = focused
    # ⚠️ `rows` 要**留到摘要生成之后**再 pop —— `value_looks_like` 在 rows 里，
    # 提前 pop 会让"疑错字 vs 串台"的判据在 detailed=False 时静默丢失。
    _rows_for_summary = r.get("rows") or []
    if not detailed:
        r.pop("rows", None)
    if not r.get("ok"):
        why = r.get("why") or "读情报面板失败"
        rec = r.get("recover") or {}
        if rec and rec.get("recovered") is False:
            why += " ｜ ⛔ **界面没清理干净** —— 请人工介入（按 Esc / 点「中止」）"
        return runtime.fail(why, **r)
    v = r.get("values") or {}
    bits = ["「%s」" % (r.get("title") or r.get("commanded") or "?")]
    for k in ("民心", "士兵", "士氣", "資金收入", "兵糧收入", "在任"):
        if v.get(k) is not None:
            bits.append("%s %s" % (k, v[k]))
    # ⭐ 描述性文字行（如 `商人`）读到了字但没对上已知词 ⇒ **原文照给**，
    # 让 agent 自己用语义判断。`value_looks_like.verdict` 告诉它可信度：
    #   likely_typo = 与合法值只差一两字（OCR 错字，可用）
    #   unrelated   = 跟本行任何合法值都不沾（多半是串台的城名人名，别用）
    vt = r.get("values_text") or {}
    if vt:
        rows_by = {x["label"]: x for x in _rows_for_summary}
        # 判据也要单独带出来 —— detailed=False 时 rows 已被 pop，
        # 只留摘要里那句话不够，agent 可能想按 verdict 编程判断
        r["values_text_looks_like"] = {
            k: (rows_by.get(k) or {}).get("value_looks_like") for k in vt}
        for k, t in vt.items():
            vl = (rows_by.get(k) or {}).get("value_looks_like") or {}
            vd = vl.get("verdict")
            if vd == "likely_typo":
                bits.append("%s ≈%s（OCR 原文，疑错字，最接近「%s」）"
                            % (k, t, vl.get("best")))
            else:
                bits.append("⚠️ %s 读到「%s」但与该行合法值无关 ⇒ **别当成这一行的值**"
                            % (k, t))
    if r.get("unread"):
        bits.append("⚠️ 有墨迹但 OCR 读不出：%s" % "、".join(r["unread"]))
    r["reading"] = "；".join(bits)
    return runtime.ok(**r)


@registry.tool(
    "san9_officers", group="B", tier="play",
    summary="某设施「**现在还有几个人能下令**」—— 面板「在任 X/Y」的语义化读数："
            "X=本旬可下令人數 / Y=該設施在任武將總數，另附 俘虜 / 在野 两格"
            "（读不出就明说 `unread`，**不当成 0**）。"
            "⛔ **本工具只给人数，不给名字** —— 「名字/官职/本旬是否已行动」要从"
            "「情報→全武將」一覽读，本项目尚未实现（返回里会写明 `roster_available:false`）。"
            "⛔ **Y−X 不许当成「已行动人数」**（实测反例见 `availability.cannot_order_note`）。"
            "只读、不下达任何命令；但面板只在命令菜单开着时存在，所以会开一次菜单再关掉"
            "（**与 san9_panel 同一条已验证路径**）。",
    schema={"type": "object", "properties": {
        "row": {"type": "integer",
                "description": "设施在右侧列表里的**行号**（从 san9_look 的 facilities[].row 拿）"},
        "expect": {"type": "string",
                   "description": "期望的设施名（建议传）：会与面板标题核对，不一致会 warn"},
        "detailed": {"type": "boolean", "default": False,
                     "description": "true=附面板 17 行的原始读数 / 锚点对位 / 墨迹计数（排查用）"},
    }, "required": ["row"]})
def san9_officers(row: int, expect: str | None = None, detailed: bool = False):
    w = runtime.window()
    focused = runtime.ensure_foreground(w)
    # 能力全在 san9/ 里：读面板走 read_panel_only（先开菜单 → 读 → 关），
    # 语义翻译走 officer_availability —— 本包不重新解释任何像素。
    r = atoms.read_panel_only(w.hwnd, row, expect=expect)
    r["focused"] = focused
    r["availability"] = atoms.officer_availability(r)
    for lab in ("俘虜", "在野"):
        cell = atoms._panel_cell_status(r, lab)
        if cell is not None:
            r.setdefault("others", {})[lab] = cell
    if not detailed:
        r.pop("rows", None)
    if not r.get("ok"):
        why = r.get("why") or "读情报面板失败"
        rec = r.get("recover") or {}
        if rec and rec.get("recovered") is False:
            why += " ｜ ⛔ **界面没清理干净** —— 请人工介入（按 Esc / 点「中止」）"
        return runtime.fail(why, **r)

    # 名册（名字 / 官职 / 本旬是否已行动）没有可信来源，**明确说没有**，
    # 而不是让 agent 以为"工具没返回 = 这城没人"。
    r["roster"] = None
    r["roster_available"] = False
    r["roster_why"] = (
        "**本工具**拿不到名册：面板只有「在任 X/Y」这个计数，没有名字。"
        "名字要去「情報→全武將」一覽读 ⇒ 改调 **`san9_intel_officer`**（已实现）。"
        "⛔ 另一条路「选将弹窗」不要用：要开命令界面，且**本旬只剩 1 人时游戏会自动执行命令**，"
        "做只读侦察会被误伤。")
    r["roster_needs"] = {
        "tool": "san9_intel_officer",
        "status": "已实现（只给**势力名册**，不分城；不翻页；没有「本旬已行动」列）",
        "note": "⚠️ 它给的是**整个势力**当前可见一屏的武将，**不是本设施的在任名单** —— "
                "「这几个人是不是都在这座城」目前无判据，不要把两者当同一回事。"}

    a = r["availability"]
    bits = ["「%s」" % (r.get("title") or r.get("commanded") or "?")]
    if a.get("can_order_now") is None:
        bits.append("本旬可下令人数：**读不出来**（%s）" % (a.get("reason") or ""))
    else:
        bits.append("本旬可下令 %d 人 / 在任总数 %d" % (a["can_order_now"], a["total"]))
        if a["cannot_order_now"]:
            bits.append("另 %d 人当前不可下令（成因不可判定）" % a["cannot_order_now"])
    for lab, cell in (r.get("others") or {}).items():
        bits.append("%s %s" % (lab, cell["value"] if cell["status"] == "read"
                              else "（%s）" % cell["status"]))
    bits.append("名册：不可用（见 roster_why）")
    r["reading"] = "；".join(bits)
    return runtime.ok(**r)


@registry.tool(
    "san9_journal", group="L", tier="play",
    summary="读「情報 → 進行記錄」= **上旬每条命令的结果**（谁做了什么、成功还是失败）。"
            "这是**命令是否生效的权威来源** —— 验证手段就用它，不要用左下角播报面板"
            "（那个只在过旬演出期间有效，演完就没了）。",
    schema={"type": "object", "properties": {
        "want_lines": {"type": "boolean", "default": True,
                       "description": "是否附原始行文本（默认附，便于核对）"},
    }})
def san9_journal(want_lines: bool = True):
    w = runtime.window()
    focused = runtime.ensure_foreground(w)
    r = atoms.read_journal(w.hwnd)
    r["focused"] = focused
    if not want_lines:
        r.pop("lines", None)
    if not r.get("ok"):
        return runtime.fail(r.get("why") or "读進行記錄失败", **r)
    if not r.get("lines"):
        r["note"] = "本次記錄为空 —— 空内容是**合法**的（可能本旬还没过、或没有值得记的事）"
    return runtime.ok(**r)


def _intel_officer_reading(p: dict, rc: dict) -> str:
    """把名册解析结果翻成一句人话。**可信度必须写在脸上。**"""
    cnt = p.get("counts") or {}
    rows = p.get("rows") or []
    named = [r["name"] for r in rows if r.get("name_status") == "locked"]
    maybe = [(r.get("name_raw"), r.get("name_nearest")) for r in rows
             if r.get("name_status") == "candidate"]
    bits = ["屏上 %d 行" % len(rows),
            "锁定 %d 人" % cnt.get("locked", 0)]
    if named:
        bits.append("／".join(named))
    if maybe:
        bits.append("疑似 %d 人（**不可当作可点身份**）：%s"
                    % (len(maybe), "、".join("%s→%s?" % (a, b) for a, b in maybe)))
    if cnt.get("unknown"):
        bits.append("认不出 %d 人（候选表里没有对得上的）" % cnt["unknown"])
    if p.get("unread"):
        bits.append("⚠️ 第 %s 行名字 OCR 读不出（**没当成空位删掉**）"
                    % "、".join(str(i) for i in p["unread"]))
    bits.append("能力值：不给（见 abilities_note）")
    bits.append("所属设施：不给（见 at_note）")
    if rc.get("n"):
        bits.append("候选表 %d 人（%s）" % (rc["n"], rc.get("meta_status") or "?"))
    return "；".join(bits)


@registry.tool(
    "san9_intel_officer", group="B", tier="play",
    summary="读「情報 → 全武將」一覽 = **全天下武将**（含他势力与在野）。"
            "🔴 **想拿「某座城有谁」请改用 `san9_city_officers`** —— 那个走右键点城市，"
            "给的是该城的在任名册。本工具这屏默认列的是全天下人"
            "（真机实测读到 `山越大王`/`孔融`/`王允`/`何進`），**不是我方名册**，"
            "要筛我方得先切一覽屏的栏目按钮（说明书 P.11），尚未实现。"
            "会开一次一覽屏、读一屏、按 Esc 退回战略面。"
            "⭐ 名字是**选择器文字**（要拿去点人），所以走 `namelock` 三级判定："
            "`locked`=与候选表精确对上，**只有这类才允许当作可点身份**；"
            "`candidate`=形近但没对上（如「高异」vs「高昇」），**给你参考、禁止据此点人**；"
            "`unknown`=认不出。OCR 读不出的行进 `unread`，**绝不当成空位删掉**。"
            "⛔ **不给能力值**（統率/武力/智力/政治）—— 这个屏字号与顶栏不同一套，"
            "顶栏字形库命中率仅 4.8% 且擦边，要读得先为本屏自建模板库（见 abilities_note）。"
            "⛔ **不给所属设施** —— 右侧那竖列是地图城名标注、不是表格列（见 at_note）。"
            "⚠️ 只读**当前可见的一屏**（" + str(intel.N_ROWS) + " 行），不翻页 —— "
            "满屏时要怀疑还有下一页。",
    schema={"type": "object", "properties": {
        "detailed": {"type": "boolean", "default": False,
                     "description": "true=附逐行原始读数、两道门禁分数、OCR 框数（排查用）"},
        "keep_open": {"type": "boolean", "default": False,
                      "description": "⚠️ true=读完**不退屏**（留在一覽屏上，用于人工比对）。"
                                     "默认 false：读完必退，不把界面留脏。"},
    }})
def san9_intel_officer(detailed: bool = False, keep_open: bool = False):
    w = runtime.window()
    focused = runtime.ensure_foreground(w)
    r = intel.read_officer_list(w.hwnd, keep_open=keep_open)
    r["focused"] = focused
    if not focused:
        r["focus_warning"] = "游戏没在前台 —— 截图拍的是别的窗口，下面的读数不可信"

    p = r.get("parsed") or {}
    rc = r.get("roster_config") or {}
    if not r.get("ok"):
        why = r.get("why") or "读全武將一覽失败"
        st = r.get("stage")
        if st == "open":
            why += " ｜ 停在**开屏**这一步 ⇒ 名册一个字都没读，**没有半执行**"
        elif st == "close":
            why += " ｜ 名册已读到，但**退屏没成功** ⇒ 界面可能还停在一覽屏，先调 san9_recover"
        return runtime.fail(why, **r)

    # ⚠️ 顺序要紧：reading 依赖 rows，必须在裁字段之前先生成
    r["reading"] = _intel_officer_reading(p, rc)
    r["roster_available"] = True
    if detailed:
        r["reading_rows"] = [
            "%2d) %s → %s" % (x["row"], x.get("name_raw") or "（读不出）",
                              x.get("name") or "%s[%s]" % (x.get("name_nearest") or "-",
                                                           x["name_status"]))
            for x in p.get("rows") or []]
    else:
        # 简版也**必须保留 locked 名字**（这是本工具的产出），只砍排查字段
        r["roster"] = [x["name"] for x in p.get("rows") or []
                       if x.get("name_status") == "locked"]
        for k in ("rows", "caption_check", "header_check", "n_boxes"):
            p.pop(k, None)
    return runtime.ok(**r)


def _city_officers_reading(p: dict, rc: dict) -> str:
    """把该城名册翻成一句人话。**可信度必须写在脸上。**"""
    if p.get("empty_city"):
        # ⭐ 引用游戏自己的话 —— 这是"确实没人"而不是"我没读出来"的唯一硬依据
        return ("**这座城没有武将（0 人）** —— 依据是游戏自己回的「%s」，"
                "不是读数失败。⛔ 别重试。"
                % (p.get("game_says") or "沒有武將在這都市"))
    cnt = p.get("counts") or {}
    rows = p.get("rows") or []
    named = [r["name"] for r in rows if r.get("name_status") == "locked"]
    maybe = [(r.get("name_raw"), r.get("name_nearest")) for r in rows
             if r.get("name_status") == "candidate"]
    bits = ["屏上 %d 行（= 该城在任武将数，含本旬不可下令的人）" % p.get("n_rows", len(rows))]
    bits.append("锁定 %d 人" % cnt.get("locked", 0))
    if named:
        bits.append("／".join(named))
    if maybe:
        bits.append("疑似 %d 人（**不可当作可点身份**）：%s"
                    % (len(maybe), "、".join("%s→%s?" % (a, b) for a, b in maybe)))
    if cnt.get("unknown"):
        bits.append("认不出 %d 人（候选表里没有对得上的）" % cnt["unknown"])
    if p.get("unread"):
        bits.append("⚠️ 第 %s 行**有墨但名字读不出**（**没当成空位删掉**）"
                    % "、".join(str(i) for i in p["unread"]))
    bits.append("能力值：不给（见 abilities_note）")
    if rc.get("n"):
        bits.append("候选表 %d 人（%s）" % (rc["n"], rc.get("meta_status") or "?"))
    return "；".join(bits)


@registry.tool(
    "san9_city_officers", group="B", tier="play",
    summary="读**某座城的在任武将名册**（名字清单）。入口是**右键点城市** → 弹「都市/勢力情報」"
            "八项一览表 → ↑↓+Enter 进「都市武将」→ 读一屏 → Esc 退回战略面。"
            "⭐ 这是 `san9_officers` 缺的那一半：那个只给人数（`在任 X/Y`），这个给名字。"
            "⭐ 与「巡察选将弹窗」的差别很重要：选将弹窗只列**本旬还能下命令**的人，"
            "本工具列**全部在任**武将（含不可下令者）⇒ 想知道「这城到底有谁」必须用这个。"
            "真机三通道对齐：南皮 面板 `在任 4/5` · 选将弹窗 4 人 · 本工具 5 行（见 docs/04 §7.3b）。"
            "⭐ 名字是**选择器文字**（要拿去点人），走 `namelock` 三级判定："
            "`locked`=与候选表精确对上，**只有这类才允许当作可点身份**；"
            "`candidate`=形近但没对上，**给你参考、禁止据此点人**；`unknown`=认不出。"
            "行位由**墨量检测**定（不预设行数：这个表不画行分隔线，推不出满屏槽位数）；"
            "有墨但 OCR 读不出的行进 `unread`，**绝不当成空位删掉**。"
            "⛔ **不给能力值**（統率/武力/智力/政治/學得/健康/寶物/身分）—— 本屏还没有自己的"
            "字形模板库，而项目定案是「数字读不出就作废」（OCR 会在空区域凭空生成数字）。"
            "⚠️ 只读**当前可见的一屏**，不翻页。",
    schema={"type": "object", "properties": {
        "row": {"type": "integer", "minimum": 0,
                "description": "设施在右侧列表里的**行号，从 `san9_look` 的 "
                               "`facilities[].row` 拿**（⚠️ **0 起**，与 san9_panel / "
                               "san9_menu 同一套编号）。这决定读哪座城。"
                               "⚠️ 屏上右下角那个 `地域：XX` 标注**不能**当城名判据"
                               "（干净战略面上也有它）⇒ 「读的是哪座城」由你传的 row 决定，"
                               "工具会顺手用 `san9_look` 的列表名回带 `row_name` 供你核对。"},
        "expect_city": {"type": "string",
                        "description": "期望的城名（建议传）：与列表里该行的名字核对，"
                                       "不一致就**拒绝执行**（一个键都不按）。"},
        "item": {"type": "string", "default": "都市武将",
                 "description": "一览表里的哪一项。目前只实现「都市武将」的解析；"
                                "其余 7 项（都市情報/勢力情報/勢力武将/勢力爵位/勢力兵法/"
                                "勢力陣形/勢力船）版面未量，传了会被拒。"},
        "detailed": {"type": "boolean", "default": False,
                     "description": "true=附逐行原始读数、门禁明细、墨量块证据（排查用）"},
        "keep_open": {"type": "boolean", "default": False,
                      "description": "⚠️ true=读完**不退屏**（留在名单屏上，用于人工比对）。"
                                     "默认 false：读完必退，不把界面留脏。"},
    }, "required": ["row"]})
def san9_city_officers(row: int, item: str = "都市武将", expect_city: str = "",
                       detailed: bool = False, keep_open: bool = False):
    if item != "都市武将":
        return runtime.fail(
            "只实现了「都市武将」的解析，收到 %r。" % item
            + "其余 7 项的版面（表头 y / 列 x / 行距）**还没现量**，"
            "没量就解析等于瞎猜 ⇒ 按规矩拒绝，**一个键都不按**。",
            item=item, implemented=["都市武将"], all_items=list(cityintel.MENU_ITEMS))

    w = runtime.window()
    focused = runtime.ensure_foreground(w)

    # 先只读地看一眼列表：拿该行的名字，既能核 expect_city，也能回带给 agent。
    # ⚠️ 这一步**纯读**（`city_rows` 只截图+OCR，不点任何东西）。
    row_name = None
    try:
        from san9 import cmdscreen
        rows = cmdscreen.city_rows(w.hwnd)
        if row < len(rows):
            row_name = rows[row].get("name")
        else:
            return runtime.fail(
                "右侧列表只有 %d 行（0~%d），取不到第 %d 行 ⇒ **一个键都没按**。"
                "行号从 san9_look 的 facilities[].row 拿（0 起）。"
                % (len(rows), len(rows) - 1, row),
                row=row, n_rows_in_list=len(rows))
    except Exception as e:
        row_name = None
        row_name_why = "读列表出错：%s ⇒ 没法核对城名，但不阻断（row 仍按你传的用）" % e
    else:
        row_name_why = ""

    if expect_city and row_name and expect_city not in str(row_name):
        return runtime.fail(
            "城名核对不过：你要「%s」，但列表第 %d 行读到的是「%s」⇒ "
            "**拒绝执行，一个键都没按**（点错城会读到别人的名册）。"
            "请用 san9_look 重新确认行号。" % (expect_city, row, row_name),
            row=row, row_name=row_name, expect_city=expect_city)

    r = cityintel.read_city_officers(w.hwnd, row, item=item, keep_open=keep_open)
    r["row_name"] = row_name
    if row_name_why:
        r["row_name_why"] = row_name_why
    if row_name is None:
        r["row_name_note"] = ("列表第 %d 行的名字**没读出来**（那几行 OCR 常读不出，"
                              "见 san9_look 的 name_inferred）⇒ 无法交叉核对"
                              "「读的是不是你要的城」。名册本身仍然是真读数。" % row)
    r["focused"] = focused
    if not focused:
        r["focus_warning"] = "游戏没在前台 —— 截图拍的是别的窗口，下面的读数不可信"

    p = r.get("parsed") or {}
    rc = r.get("roster_config") or {}
    if not r.get("ok"):
        why = r.get("why") or "读都市武将名册失败"
        st = r.get("stage")
        if st == "open_menu":
            why += (" ｜ 停在**右键开菜单**这一步 ⇒ 名册一个字都没读，"
                    "**没有半执行**（没按过任何方向键/Enter）")
        elif st == "enter_item":
            why += (" ｜ 菜单开了但**没敢按 Enter**（底栏说明字读不出它指向「%s」）⇒ "
                    "没进任何子屏，名册没读。⛔ 按错一项会进版面完全不同的屏，"
                    "所以宁可停手" % item)
        elif st == "read":
            why += " ｜ 进屏成功但**解析没过门禁** ⇒ 一行都没端出来"
        elif st == "close":
            why += " ｜ 名册已读到，但**退屏没成功** ⇒ 界面可能还停在名单屏，先调 san9_recover"
        return runtime.fail(why, **r)

    # ⚠️ 顺序要紧：reading 依赖 rows，必须在裁字段之前先生成
    r["reading"] = _city_officers_reading(p, rc)
    r["asked_row"] = row
    if detailed:
        r["reading_rows"] = [
            "%2d) 墨%3d  %s → %s" % (
                x["row"], x.get("ink_peak", 0), x.get("name_raw") or "（读不出）",
                x.get("name") or "%s[%s]" % (x.get("name_nearest") or "-",
                                             x["name_status"]))
            for x in p.get("rows") or []]
    else:
        # 简版也**必须保留 locked 名字 + unread 行号**（这是本工具的产出），只砍排查字段
        r["roster"] = [x["name"] for x in p.get("rows") or []
                       if x.get("name_status") == "locked"]
        # ⚠️ 空城的 `roster=[]` 是**完整答案**，不是"读漏了" ⇒ 不能标 incomplete
        r["roster_incomplete"] = (not p.get("empty_city")) and (
            bool(p.get("unread"))
            or bool((p.get("counts") or {}).get("candidate"))
            or bool((p.get("counts") or {}).get("unknown")))
        if r["roster_incomplete"]:
            r["roster_incomplete_why"] = (
                "`roster` 只含 `locked`（可安全拿去点人）的名字，"
                "**比屏上实际人数少** —— 差额在 `reading` 里说明了"
                "（unread / candidate / unknown）。⛔ 别把 `roster` 的长度当该城人数，"
                "人数看 `parsed.n_rows`。")
        for k in ("rows", "ink_blocks", "screen_check", "n_boxes"):
            p.pop(k, None)
        for k in ("open_menu", "enter_item"):
            r.pop(k, None)
    return runtime.ok(**r)
