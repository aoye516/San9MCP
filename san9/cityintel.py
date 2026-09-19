# -*- coding: utf-8 -*-
"""**右键点城市** → 「都市/勢力情報」一览表 → 读「都市武将」名册。

为什么另起一个模块（不是改 `intel.py`）
=====================================
`intel.py` 走的是**右上角「情報」菜单 → 全武將一覽**，2026-09-19 真机验明它有两个硬伤：
  ① 那屏列的是**全天下武将**（真机读到 `山越大王`/`孔融`/`王允`/`何進`），**不是我方名册**
  ② 那屏保留战略面外框 ⇒ `go` 锚点在它上面假阳性（0.9067），一堆判据被骗

用户（2026-09-19，产品经理走查）指出正确入口：**右键点城市**。
流程与"左键执行城市操作"完全一致（先在右侧列表选中城市，再点城市），只是换成右键。
弹出的一览表右下角带 `(城名)`、只列**该城武将** —— 这才是名册。

入口链（真机四步一验，每步一个改屏动作，全部有程序化判据）
-----------------------------------------------------------
| 步 | 动作 | 判据 |
|---|---|---|
| 1 | `cmdscreen.select_city_row(row)` | 已验证路径（只读准备动作） |
| 2 | `CITY_CENTER(640,480)` **右键** | 底栏出现 `查看都市情報`、右下出现 `(城名)` |
| 3 | `press("down")` × k → `press("enter")` | 底栏跟随高亮变 `在看都市的武將情報` |
| 4 | `press("escape")` × **1** | 底栏说明字消失，**一步直接回干净战略面** |

三条读数纪律（沿用项目既有定案，不重新发明）
--------------------------------------------
1. **行位由墨量定，不由 OCR 定，也不预设 N 行。**
   这个表**不画行分隔线**（`scripts/probe_city_officer_grid.py` 证明：只找到 3 条线，
   其中 2 条是高亮条的亮边）⇒ 拿不到表格下边界 ⇒ **推不出满屏行槽位个数**。
   而名册长度本来就该由屏上内容决定。
2. **「读不出来」≠「那行没人」**（与 `atoms.read_panel_from` 同一条纪律）：
   有墨但 OCR 读不出 ⇒ 记 `unread`，**不从名册里删掉、不猜值**。
   实测南皮 5 行里 y=505 那行就是这种（墨 15，OCR 全尺度空）。
3. **名字是"选择器文字" ⇒ 必须过 `namelock` 三级判定**（`locked`/`candidate`/`unknown`）。
   `candidate` **只给参考，禁止据此点人**。

⛔ 不给能力值：`統率/武力/智力/政治` 那几列是数字，而**本屏没有字形模板库**。
   项目定案是"数字 = 字形查表，读不出作废"（OCR 会在空区域凭空生成数字）⇒ 不建库就不给。
⛔ 不裁小图单独 OCR：实测裁 68×27 后 5 行只读出 1 个，全屏 OCR 读出 4 个
   —— OCR 引擎要靠周边上下文定位文字框。**墨量定行 + 全屏 OCR 取字**才是对的组合。
"""
from __future__ import annotations

import collections
import time

import cv2
import numpy as np

from . import namelock, ocr

# ── 版面常量：全部由 scripts/probe_intel_officer_layout.py +
#              scripts/probe_city_officer_grid.py 在实拍帧上**现量**（⛔ 非目测）
MENU_X = 682
MENU_ROW0_Y = 493
MENU_PITCH = 30
MENU_ITEMS = ("都市情報", "都市武将", "勢力情報", "勢力武将",
              "勢力爵位", "勢力兵法", "勢力陣形", "勢力船")
"""右键弹出的 8 项，y=493 起行距 30，x≈682（现量）。

⚠️ 这里**只用来做"菜单在不在"的核对和"要按几次 ↓"的算术**，
   ⛔ **不拿它当点击坐标** —— 移动/确认全走键盘（↑↓ + Enter），零坐标。
"""

MENU_CAPTION_REG = (400, 928, 1000, 960)
MENU_CAPTION_MARKS = ("都市情報", "都市情报", "查看都市")
"""刚右键出来时底栏的说明字（高亮默认在第 1 项「都市情報」上）。"""

OFFICER_CAPTION_MARKS = ("武將", "武将", "選擇", "选择", "情報", "情报")
"""进入「都市武将」后底栏说明字。实测读到「請選擇欲觀看其情報的武將。」"""

EMPTY_CITY_MARKS = ("沒有武將在這都市", "没有武将在这都市", "沒有武將", "没有武将")
"""⭐ 游戏自己说的「这城没人」。

2026-09-19 真机自测（北海，面板读数 `在任 0/0`）抓到的：
选「都市武将」后游戏**不开表格屏**，而是在底栏回一句
**「沒有武將在這都市。」**

⇒ 此时「表头读不到」**不是故障，是正确答案的正确表现**。
初版把它当解析失败报（`ok:false`，理由"表头 y≈384 只读到 []"），
还顺带把已经成功的退屏误报成"退屏没成功" —— 典型的**报症状不报病因**。
项目铁律：**失败报病因，且优先引用游戏自己的话**（底栏 hint / game_says）。
"""

HEAD_Y = 384
HEAD_WORDS = ("武將", "武将", "統率", "统率", "武力", "智力", "政治",
              "學得", "学得", "健康", "寶物", "宝物", "身分")
ROW0_Y = 415
ROW_PITCH = 30
ROW_TOL = 12
NAME_X = 301
NAME_HALF = 34
"""武将名列。半宽 34：到「統率」列(381) 的中点是 341，取 34 更保守，不会读进能力值。"""

INK_X_HALF = 30
INK_THR = 150
INK_MIN = 3
"""墨量检测参数（字亮底暗）。`INK_MIN=3` 由实测定：数据行峰值 15~46，噪声块 3~10。"""

ROW_INK_MIN = 10
ROW_OFF_TOL = 0.25
"""判"这是数据行"的两个条件：峰值墨 >= 10 **且** 落在 30 格上（偏差 < 0.25）。

实测南皮：5 个数据行偏差 0.03~0.07、墨 15~46 全过；
5 个噪声块（y=750~785，小地图区域的字）墨 3~10 或偏差 0.3~0.5，全被挡。
"""

# 底栏右下角的城名标注。实测原文是 `地域：南皮（南皮）`，x 范围 1110~1242、y≈943
# （`scripts/probe_city_tag_reg.py` 现量，⛔ 非目测）。
CITY_TAG_REG = (1095, 930, 1280, 958)

CITY_TAG_IS_NOT_A_GATE = (
    "⛔ **这个标注不能当「这屏属于哪座城」的判据。** 实测三帧（都市武将名单屏 / "
    "右键 8 项菜单 / **退出后的干净战略面**）右下角**全都**读到 `地域：南皮（南皮）` "
    "⇒ 它是**右侧列表选中项的常驻标注**，跟当前是哪个屏毫无关系。"
    "证据：scripts/probe_city_tag_reg.py。"
    "（与「右侧竖列不是所属设施列」、「商人='駐在' 是合法值」同一类教训："
    "**看起来像就是证据 —— 这个念头本身就是 bug。**）"
    "所以 `city` 字段只是**原文照给**，调用方要知道自己选的是第几行。")


def _grab(hwnd):
    return ocr._grab_bgr(hwnd)


def _ocr_boxes(bgr, upscales=(2, 3, 4)) -> list[dict]:
    out: list[dict] = []
    for up in upscales:
        try:
            out += ocr.read(bgr, upscale=up)
        except Exception:
            pass
    return out


def _region_text(bgr, reg, upscales=(3, 4)) -> str:
    txt = ""
    try:
        sub = bgr[reg[1]:reg[3], reg[0]:reg[2]]
        for up in upscales:
            txt += "".join(i["text"] or "" for i in ocr.read(sub, upscale=up))
    except Exception:
        pass
    return txt.strip()


def bottom_hint(bgr) -> str:
    """底栏说明字。**这是判"高亮在第几项 / 现在是哪个屏"的唯一程序化判据。**

    ⛔ 不许目测高亮条位置（禁令）。
    ⚠️ 不用 `atoms.hint` —— 实测它在这类屏上**读不稳**（同一屏有时回空串，
       而对同一帧做全屏 OCR 在 y≈941 明明有字）。这里自己读 `MENU_CAPTION_REG`。
    """
    return _region_text(bgr, MENU_CAPTION_REG)


def city_tag(bgr, boxes=None) -> str:
    """右下角城名标注的原文（实测 `地域：南皮（南皮）`）。

    ⚠️ **只是原文，不是判据** —— 详见 `CITY_TAG_IS_NOT_A_GATE`。

    ⛔ **不能裁区域单独 OCR**：实测裁 `CITY_TAG_REG` 后读出 `电域` 或空串，
       而同一帧**全屏** OCR 在 x=1110~1242 / y≈943 清清楚楚读出 `地域：南皮（南皮）`。
       原因同"裁名字格"那条：OCR 引擎要靠周边上下文定位文字框。
       ⇒ 这里也走 **全屏 OCR + 按位置命中**。
    """
    bs = boxes if boxes is not None else _ocr_boxes(bgr)
    cand = sorted({(b["text"] or "").strip() for b in bs
                   if CITY_TAG_REG[1] <= b["y"] <= CITY_TAG_REG[3]
                   and CITY_TAG_REG[0] <= b["x"] <= CITY_TAG_REG[2]
                   and (b["text"] or "").strip()}, key=len, reverse=True)
    return cand[0] if cand else ""


def check_rclick_menu(bgr) -> dict:
    """门禁①：右键菜单开着吗？

    判据是**菜单自己的内容**：底栏说明字命中 `查看都市情報` 之类，
    **或者** 菜单 8 项里能在正确的 y 上读到 >= 3 项。
    ⛔ 不用 `go` 锚点 —— 一览类屏保留战略面外框，`go` 会假阳性（2026-09-19 血案）。
    """
    hint = bottom_hint(bgr)
    hit_hint = [m for m in MENU_CAPTION_MARKS if m in hint]

    boxes = _ocr_boxes(bgr)
    hit_items = []
    for i, name in enumerate(MENU_ITEMS):
        y = MENU_ROW0_Y + MENU_PITCH * i
        for b in boxes:
            if abs(b["y"] - y) > ROW_TOL or abs(b["x"] - MENU_X) > 60:
                continue
            t = (b["text"] or "")
            # 繁简都可能，取后两字做宽松包含（「都市情報」/「都市情报」）
            if name[-2:] in t or name[:2] in t:
                hit_items.append(name)
                break
    ok = bool(hit_hint) or len(set(hit_items)) >= 3
    return {"ok": ok, "hint": hint, "hint_hit": hit_hint,
            "items_found": sorted(set(hit_items)),
            "why": "" if ok else
                   ("右键菜单没认出来：底栏读到 %r（没命中 %s），"
                    "菜单项只认出 %s（<3 项）⇒ 停手不往下按键"
                    % (hint, list(MENU_CAPTION_MARKS), sorted(set(hit_items))))}


def check_officer_screen(bgr) -> dict:
    """门禁②：现在在「都市武将」名单屏吗？**双条件，任一不过就不解析。**

    ① 底栏说明字（`請選擇欲觀看其情報的武將`）
    ② 表头行 y≈384 能读到 >= 3 个表头词

    为什么要两道：菜单里「都市武将」和「都市情報」「勢力武将」相邻，
    按错一次 ↓ 就会进版面完全不同的屏。单靠底栏不够
    （`勢力武将` 的底栏说明字很可能也含「武將」）。
    """
    hint = bottom_hint(bgr)
    hit = [m for m in OFFICER_CAPTION_MARKS if m in hint]

    boxes = _ocr_boxes(bgr)
    got = []
    for b in boxes:
        if abs(b["y"] - HEAD_Y) > ROW_TOL:
            continue
        got += [w for w in HEAD_WORDS if w in (b["text"] or "")]
    got = sorted(set(got))

    # ⭐ 先看游戏有没有直接告诉我「这城没人」 —— 这条**优先于**表头判定。
    # 否则会把正确答案报成故障（2026-09-19 北海实测的坑，见 EMPTY_CITY_MARKS）。
    empty_hit = [m for m in EMPTY_CITY_MARKS if m in hint]

    cap_ok, hdr_ok = bool(hit), len(got) >= 3
    out = {"caption_ok": cap_ok, "header_ok": hdr_ok,
           "hint": hint, "hint_hit": hit, "header_found": got,
           "city_tag": city_tag(bgr, boxes),
           "empty_city": bool(empty_hit), "empty_hit": empty_hit}
    if empty_hit:
        # 没有表格屏可解析，但这是**合法结论**，不是失败。
        out["ok"] = False
        out["game_says"] = hint
        out["why"] = ("游戏自己回了「%s」⇒ **这座城没有武将**（合法结论，不是读数失败）。"
                      "所以没有表格屏、表头当然读不到。" % (empty_hit[0]))
        return out
    out["ok"] = cap_ok and hdr_ok
    out["why"] = ("" if out["ok"] else
                  ("不是「都市武将」名单屏："
                   + ("" if cap_ok else "底栏读到 %r 没命中 %s；"
                      % (hint, list(OFFICER_CAPTION_MARKS)))
                   + ("" if hdr_ok else "表头 y≈%d 只读到 %s（<3 个）；" % (HEAD_Y, got))
                   + "⇒ 停手不解析"))
    return out


def detect_data_rows(bgr) -> list[dict]:
    """**墨量定行**：返回武将名列上"有内容"的行。

    ⛔ 不预设行数（这个表不画行分隔线，推不出满屏槽位数，见模块头）。
    ⛔ 不靠 OCR 定行（实测 OCR 会漏掉有墨的行 —— 南皮 y=505）。
    """
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    h = g.shape[0]
    band = g[:, max(0, NAME_X - INK_X_HALF):NAME_X + INK_X_HALF]
    ink = (band > INK_THR).sum(axis=1)

    out: list[dict] = []
    y = HEAD_Y + 12                     # 从表头下方开始，跳过表头自己
    while y < h:
        if ink[y] >= INK_MIN:
            y0 = y
            while y < h and ink[y] >= INK_MIN:
                y += 1
            center = (y0 + y - 1) // 2
            off = (center - ROW0_Y) / ROW_PITCH
            row = int(round(off))
            err = abs(off - row)
            out.append({"row": row, "y": ROW0_Y + ROW_PITCH * row,
                        "ink_y0": y0, "ink_y1": y - 1, "ink_center": center,
                        "ink_peak": int(ink[y0:y].max()), "off_err": round(err, 3),
                        "is_data": bool(row >= 0
                                        and int(ink[y0:y].max()) >= ROW_INK_MIN
                                        and err < ROW_OFF_TOL)})
        else:
            y += 1
    return out


def _col_text(boxes, y: int, cx: int, half: int) -> tuple[str | None, list[str]]:
    """(行 y, 列 cx) 这一格的文字：多尺度读数**取最长**（小字 OCR 的典型错是少读字）。"""
    cand = sorted({(b["text"] or "").strip() for b in boxes
                   if abs(b["y"] - y) <= ROW_TOL and abs(b["x"] - cx) <= half
                   and (b["text"] or "").strip()}, key=len, reverse=True)
    return (cand[0] if cand else None), cand


def parse_city_officers(bgr, roster=None) -> dict:
    """**纯函数**：一帧「都市武将」→ 该城武将名册。不碰游戏，可离线回归。

    返回
      - `city`        —— 右下角读到的 `(城名)`（原文，可能有 OCR 小错）
      - `rows`        —— 逐行 `{row, y, ink_peak, name_raw, name, name_status, …}`
                         `name` **只在 `name_status=="locked"` 时非空**
      - `unread`      —— **有墨但读不出名字**的行号（⛔ 不许静默丢弃）
      - `counts`      —— locked / candidate / unknown 三级计数
      - `n_rows`      —— 屏上判定为数据行的行数（= 该城可见武将数）
    """
    out: dict = {
        "ok": False, "rows": [], "unread": [], "counts": {}, "why": "",
        "abilities": None,
        "abilities_note": ("能力值（統率/武力/智力/政治/學得/健康/寶物/身分）**故意不给** —— "
                           "项目定案「数字 = 字形模板查表，读不出作废」，"
                           "而**本屏还没有自己的字形库**（⛔ 顶栏库不能复用：已两次查证，"
                           "换屏后命中率 4.8%~38.8% 且命中距离贴着上限 = 擦边可能是错的）。"
                           "OCR 会在空区域凭空生成数字且不触发 low_conf ⇒ 宁可不给。"),
        "page_note": ("本函数只读**当前可见的一屏**，不翻页。"
                      "行数由墨量检测决定（这个表不画行分隔线，推不出满屏槽位数），"
                      "所以读到 N 行只能说明「屏上看得见 N 行」。"),
    }
    gate = check_officer_screen(bgr)
    out["screen_check"] = gate
    if gate.get("empty_city"):
        # ⭐ 游戏说「沒有武將在這都市」⇒ **这是答案，不是失败。**
        # 0 人是合法名册（与 `san9_officers` 的 `0/0` 是合法真读数同一条纪律）。
        out["ok"] = True
        out["n_rows"] = 0
        out["empty_city"] = True
        out["game_says"] = gate.get("game_says")
        out["why"] = ""
        out["empty_note"] = ("这座城**确实没有武将** —— 依据是游戏自己的话「%s」，"
                             "不是「我没读出来」。⛔ 不要把这种情况当读数失败重试。"
                             % (gate.get("game_says") or ""))
        return out
    if not gate["ok"]:
        out["why"] = gate["why"]
        return out
    out["city_tag_raw"] = gate.get("city_tag") or None
    out["city_tag_note"] = CITY_TAG_IS_NOT_A_GATE

    blocks = detect_data_rows(bgr)
    out["ink_blocks"] = blocks
    data = [b for b in blocks if b["is_data"]]
    out["n_rows"] = len(data)
    if not data:
        out["why"] = ("门禁过了（确实在「都市武将」屏）但**墨量检测一行都没找到** ⇒ "
                      "要么这座城真的没有武将，要么版面常量过期。"
                      "如实报 0 行，⛔ 不猜。ink_blocks 里有原始证据。")
        out["ok"] = True          # 屏读对了、解析没出错，只是 0 行
        return out

    boxes = _ocr_boxes(bgr)
    out["n_boxes"] = len(boxes)
    raws = []
    for b in data:
        raw, cand = _col_text(boxes, b["y"], NAME_X, NAME_HALF)
        raws.append((b, raw, cand))

    locks = namelock.lock_many([r[1] or "" for r in raws], roster or [], unique=True)
    cnt: collections.Counter = collections.Counter()
    for (b, raw, cand), lk in zip(raws, locks):
        st = lk["status"]
        cnt[st] += 1
        row = {"row": b["row"], "y": b["y"], "ink_peak": b["ink_peak"],
               "name_raw": raw, "name_candidates": cand,
               "name": lk["locked"], "name_status": st,
               "name_nearest": lk["nearest"], "name_ratio": lk["ratio"]}
        if st != "locked" and lk.get("why"):
            row["name_why"] = lk["why"]
        if not raw:
            row["note"] = ("这一行**有墨**（峰值 %d）但 OCR 全尺度读不出名字 ⇒ "
                           "记 unread，**没有从名册里删掉**。"
                           "⛔「读不出来」≠「这行没人」" % b["ink_peak"])
            out["unread"].append(b["row"])
        out["rows"].append(row)

    out["counts"] = dict(cnt)
    out["ok"] = True
    if out["unread"]:
        out["why"] = ("第 %s 行有墨但名字读不出（那些行屏上是有人的）⇒ 如实报 unread"
                      % out["unread"])
    return out


# ────────────────────────────────────────────────────────── 动手部分（每步一验）

def open_rclick_menu(hwnd, row: int) -> dict:
    """打开右键一览菜单。**一次调用只做这一件事。**

    两个动作：① `select_city_row(row)`（已验证的只读准备动作）
              ② 在 `CITY_CENTER` **右键**（唯一的改屏动作）
    然后**必须核实菜单真的开了**（`check_rclick_menu`），核不上 ⇒ 如实报，不重试不换坐标。
    """
    from . import cmdscreen, winio

    out: dict = {"ok": False, "row": row, "why": ""}
    try:
        out["select_city"] = cmdscreen.select_city_row(hwnd, row)
    except Exception as e:
        out["why"] = "选列表第 %d 行就失败了：%s ⇒ 一个字都没点城市" % (row, e)
        return out
    time.sleep(1.2)

    xy = cmdscreen.CITY_CENTER
    out["click_xy"] = list(xy)
    out["click_note"] = ("CITY_CENTER 是**推导值**：选中列表行后该设施就落在屏幕正中，"
                         "与左键执行城市操作用的是同一个点（已验证路径）。⛔ 不是猜的。")
    winio.click_client(hwnd, xy[0], xy[1], button="right", settle=0.5)
    time.sleep(1.5)

    bgr = _grab(hwnd)
    if bgr is None:
        out["why"] = "右键点完抓不到画面 ⇒ 无法核实菜单开没开，不敢往下按键"
        return out
    chk = check_rclick_menu(bgr)
    out["menu_check"] = chk
    if not chk["ok"]:
        out["why"] = chk["why"]
        return out
    out["ok"] = True
    return out


def enter_menu_item(hwnd, item: str = "都市武将") -> dict:
    """在已打开的右键菜单里走到 `item` 并 Enter 进去。**纯键盘，零坐标。**

    为什么可以在一次调用里按多下：↑↓ 移动**不改变游戏状态**，只挪高亮，
    而且每按完都能用底栏说明字验证。Enter 只按**一次**，且按前先核对高亮对不对。
    """
    from . import winio

    out: dict = {"ok": False, "item": item, "why": "", "steps": []}
    if item not in MENU_ITEMS:
        out["why"] = "不认识的菜单项 %r（这屏只有 %s）" % (item, list(MENU_ITEMS))
        return out
    idx = MENU_ITEMS.index(item)

    bgr = _grab(hwnd)
    if bgr is None:
        out["why"] = "抓不到画面 ⇒ 不知道菜单在不在，什么都不按"
        return out
    chk = check_rclick_menu(bgr)
    out["menu_check_before"] = chk
    if not chk["ok"]:
        out["why"] = "按键前核不到右键菜单 ⇒ 停手。%s" % chk["why"]
        return out

    # 刚打开时高亮默认在第 1 项（底栏 = 查看都市情報，实测）⇒ 按 idx 次 ↓
    for i in range(idx):
        winio.press("down")
        time.sleep(0.4)
        h = bottom_hint(_grab(hwnd))
        out["steps"].append({"press": "down", "n": i + 1, "hint": h})

    # ⭐ Enter 之前**核对底栏说明字**：它跟着高亮走，是"高亮在对的项上"的唯一判据
    hint_now = out["steps"][-1]["hint"] if out["steps"] else chk["hint"]
    key = item[-2:]                     # 「都市武将」→「武将」
    ok_highlight = (key in hint_now) or (key.replace("将", "將") in hint_now)
    out["hint_before_enter"] = hint_now
    if not ok_highlight:
        out["why"] = ("按了 %d 次 ↓ 之后，底栏说明字是 %r，读不出它指向「%s」⇒ "
                      "**不敢按 Enter**（按错一项会进版面完全不同的屏）。"
                      "⛔ 停手不猜。注：底栏 OCR 偶尔回空串，可重试一次读。"
                      % (idx, hint_now, item))
        return out

    winio.press("enter")
    time.sleep(1.6)
    out["steps"].append({"press": "enter"})
    out["ok"] = True
    return out


def close_screen(hwnd, tries: int = 2) -> dict:
    """退回战略面。**判据 = 一览屏的说明字消失**（⛔ 不用 `go` 锚点，它会假阳性）。

    实测（2026-09-19）：从「都市武将」名单屏按 **Esc 一次**，画面变化 48.6，
    **8 项菜单一并关闭、直接回到干净战略面**（不是退回菜单）。
    `tries=2` 只是留一次容错，不是"多试几下"。
    """
    from . import winio

    out: dict = {"ok": False, "tries": 0, "why": ""}
    for _ in range(max(1, tries)):
        out["tries"] += 1
        winio.press("escape")
        time.sleep(1.2)
        bgr = _grab(hwnd)
        if bgr is None:
            out["why"] = "按了 Esc 但抓不到画面 ⇒ 无法核实退没退出去"
            return out
        hint = bottom_hint(bgr)
        out["hint"] = hint
        still = [m for m in (OFFICER_CAPTION_MARKS + MENU_CAPTION_MARKS) if m in hint]
        out["still_hit"] = still
        if not still:
            out["ok"] = True
            return out
    out["why"] = ("按了 %d 次 Esc，底栏说明字仍是 %r（命中 %s）⇒ 没退出去。"
                  "如实报，⛔ **不猜坐标去点**。可请用户按一下 Esc。"
                  % (out["tries"], out.get("hint"), out.get("still_hit")))
    return out


def read_city_officers(hwnd, row: int, item: str = "都市武将",
                       keep_open: bool = False) -> dict:
    """完整一趟：右键开菜单 → 进「都市武将」→ 读名册 → Esc 退回战略面。

    ⚠️ 这是**多个改屏动作串在一起**，按纪律本该拆开。允许合并的唯一理由：
    开屏/退屏是一对（开了不退会把界面留脏，比分开调用更危险），中间那步是**纯读**。
    ⛔ 任何一步失败就**立即停下留证**，不继续往下走、不重试、不换坐标。
    """
    from . import intel

    out: dict = {"ok": False, "stage": "open_menu", "row": row, "item": item, "why": ""}

    op = open_rclick_menu(hwnd, row)
    out["open_menu"] = op
    if not op["ok"]:
        out["why"] = op["why"]
        out["cleanup"] = close_screen(hwnd, tries=1)
        return out

    out["stage"] = "enter_item"
    en = enter_menu_item(hwnd, item)
    out["enter_item"] = en
    if not en["ok"]:
        out["why"] = en["why"]
        out["cleanup"] = close_screen(hwnd, tries=1)
        return out

    out["stage"] = "read"
    bgr = _grab(hwnd)
    if bgr is None:
        out["why"] = "进屏成功但抓不到画面"
        out["close"] = close_screen(hwnd)
        return out
    rc = intel.load_roster_config()
    parsed = parse_city_officers(bgr, rc.get("candidates"))
    out["roster_config"] = {"n": rc.get("n"), "path": rc.get("path"),
                            "meta_status": rc.get("meta_status")}
    out["parsed"] = parsed

    if keep_open:
        out["stage"] = "kept_open"
        out["close"] = {"ok": True, "skipped": True,
                        "why": "keep_open=True ⇒ **没有退屏**，界面仍停在名单屏。"
                               "后续任何战略面操作前请先退屏（Esc / san9_recover）。"}
        out["ok"] = bool(parsed.get("ok"))
        if not out["ok"]:
            out["why"] = parsed.get("why") or ""
        return out

    out["close"] = close_screen(hwnd)
    # ⚠️ stage 必须指向**真正出问题的那一步**，不能一律写 "close"。
    # 2026-09-19 北海实测踩到：解析没过（其实是"这城没人"）而退屏明明成功了，
    # 旧代码却把 stage 设成 close、why 串成"名册已读到但退屏没成功" ——
    # **两个结论都是错的**。报症状不报病因就是这么来的。
    if not parsed.get("ok"):
        out["stage"] = "read"
        out["why"] = parsed.get("why") or ""
        out["ok"] = False
        if not out["close"]["ok"]:
            out["why"] += " ｜ 另外退屏也没成功：%s" % out["close"].get("why")
        return out
    out["stage"] = "close" if not out["close"]["ok"] else "done"
    out["ok"] = bool(out["close"]["ok"])
    if not out["ok"]:
        out["why"] = out["close"].get("why") or ""
    return out
