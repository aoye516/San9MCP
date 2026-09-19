# -*- coding: utf-8 -*-
"""「選擇武將」弹窗 —— 按名字 / 行号选一个武将当命令目标（target_officer）。

这是什么屏
==========
很多命令要你「指一个武将」：设施类命令选**执行武将**（巡察/商業/徵兵…）、
人材类（登庸/召來）与目标武将、任免类（褒獎/處斬/委任）、計略/外交里的敌方武将…
它们底层打开的**都是同一个「選擇武將」弹窗**。本模块就是这块弹窗的
「解析 + 选人」原语，是 `target_own_city`（选设施）的武将版对照物。

⚠️ 两种形态（同一种弹窗，交互不同，本模块都认）：
  · **单选**（輸送）：点一下武将行 ⇒ 自动回执行界面，**没有「決定」按钮**
  · **多选**（设施类巡察/商業…）：点方框勾选 → 点「決定」(绿) 确认
    ⇒ `select_officer(..., decide=True)` 才会去点「決定」；单选默认不点。

入口链（⭐ 用户口述 + 实测，⛔ 不是我猜的）
------------------------------------------------
| 步 | 动作 | 备注 |
|---|---|---|
| 1 | 点「執行武將」蓝标签（`cmdscreen.find_officer_tab`） | 标签置灰时游戏已自动选好，见 `_finish_without_picker` |
| 2 | 弹窗打开，底栏 `請選擇執行武將。` | **这就是本模块的门禁①** |
| 3 | 在名单里点武将行（单选点一下即回 / 多选勾选） | 行号从 0 起；名字走 OCR，不稳 |
| 4 | 多选时再点「決定」(绿) | 单选无此步 |

版面（全部实拍 `explore/S72_yusong_picker.jpg` 现量，⛔ 非目测）
----------------------------------------------------------------
  · 名字列 x≈280，每行 OCR 读出一个名字（前面偶尔带勾选框字形「中」）
  · 数据行 y = 413 / 444 / 474 ……，行距 **30**（`PICK_ROW_X`/`PICK_ROWS_Y0` 来自 `cmdscreen`）
  · 表头行 y≈392（能力/命令/步兵/骑兵），解析时跳过
  · 底栏提示在 y≈940，写「請選擇執行武將」（OCR 常读成 `请遥择轨行武将`）

三类字段三种待遇（项目定案，与 `targetlist` 一致）
--------------------------------------------------
  · 武将名 = **选择器文字** ⇒ 过 `namelock` 三级判定；
    `candidate` **只给参考，禁止据此点人**（要点就用 row，别用名字）
  · 名字读不出 ⇒ `unread`，**不删不猜**
  · 本屏没有数字列要读（兵糧/資金在命令界面顶部，不在这弹窗）

⛔⛔ 与 `targetlist` 一样的核心不变式（测试里盯）
-----------------------------------------------
  · 名单是**屏幕内容**，行号是**稳定身份**；名字 OCR 会抖（`龔都`↔`龚都`、`嚴政`↔`晟政`）
    ⇒ 选谁一律按 **row / 锁回的 canonical**，名字只给人看
  · 锁不上 = 不点。绝不拿 `candidate`/`unknown` 的行去点
"""
from __future__ import annotations

import re
import time

import cv2
import numpy as np

from . import namelock, ocr

# ══ 版面常量（来自 `cmdscreen.PICK_*`，这里再固化一份，使本模块自洽）══
PICK_ROWS_Y0 = 413
"""名单第一行（廖化）中心 y。现量 `explore/S72`（輸送单选）：413/444/474。"""
PICK_ROW_PITCH = 30
"""行距。现量 444-413 = 444-? ⇒ 30（与「都市武将」/「一覽」同一套）。"""
PICK_ROW_X = 290
"""点名字即可勾选的 x（同 `cmdscreen.PICK_ROW_X`）。"""

NAME_X0, NAME_X1 = 255, 360
"""名字列取字范围。现量名字左边界 268（含勾选框「中」在 268）/ 280（名字本体）。"""
NAME_Y0, NAME_Y1 = 396, 640
"""名字列 y 范围。跳过表头(392)，上沿到 ~8 行(413+30*7=623)。"""

CAPTION_Y = 940
"""底栏提示 y。与 `targetlist.CAPTION_REG` 同一行。"""
CAPTION_X0, CAPTION_X1 = 320, 1080

CHECKBOX_GLYPHS = "中口■□▣☆★●○◉✓✔"
"""名字前偶尔带勾选框字形，解析时剥掉（实测 `中廖化` = 勾选框 + 廖化）。"""

HINT_PICK_MARKS = (
    # ⭐ 单选 picker（輸送）底栏原话，OCR 变体多
    "請選擇執行武將", "请选择执行武将", "请遥择执行武养", "择执行武",
    "選擇執行武將", "轨行武將", "轨行武将", "择执行武养", "行武將", "行武将",
    # ⭐ 多选 / 目标武将 picker 的底栏（門禁更宽，覆盖「選擇武將」类提示）
    "請選擇武將", "请选择武将", "请遥择武将", "选擇武将", "選擇武將", "选择武将",
)
"""门禁①：底栏提示必须命中这些之一。**这是游戏自己写的「我在等选武将」信号**，
比任何坐标/颜色判据都硬。OCR 变体实测有 `请遥择轨行武将`/`请择行武将`
（错到 3 个字）⇒ 表里既有长句也有 `行武將` 这种短锚。"""

PICK_DECIDE = (839, 743)
"""多选时「決定」绿色按钮回退坐标（`cmdscreen.PICK_DECIDE`）。
⚠️ 只在 `decide=True` 且帧内检到绿按钮时才点；检不到就如实报、不点。"""

PICK_BTN_BAND = (720, 762)
"""「決定/中止」按钮带的 y 范围（同 `cmdscreen.PICK_BTN_BAND`）。"""


# ══════════════════════════════════════════════════════════════════════
# 帧内 OCR 辅助（全部喂 BGR 帧，纯离线、不碰游戏）
# ══════════════════════════════════════════════════════════════════════

def _grab(hwnd):
    """抓一帧（BGR）。在线时从游戏窗口取；离线测试时整函数被 monkeypatch 掉。"""
    from . import ocr as _ocr
    return _ocr._grab_bgr(hwnd)


def _ocr_boxes(bgr, upscales=(2, 3)) -> list[dict]:
    out: list[dict] = []
    for up in upscales:
        try:
            out += ocr.read(bgr, upscale=up)
        except Exception:
            pass
    return out


def _text_near_y(bgr, y: int, tol: int = 14,
                 x_range: tuple[int, int] | None = None) -> str:
    """**全屏 OCR 之后按 (y,x) 命中**取某一行的文字（同 `targetlist._text_near_y`）。

    ⭐⭐ 本仓定案手法，⛔ **不许裁小图单独 OCR**（裁小图 OCR 漏行是已知错法）。
    """
    out = []
    for b in _ocr_boxes(bgr, upscales=(2, 3)):
        t = (b.get("text") or "").strip()
        if not t or abs(b["y"] - y) > tol:
            continue
        if x_range and not (x_range[0] <= b["x"] <= x_range[1]):
            continue
        out.append((b["x"], t))
    return "".join(t for _x, t in sorted(out))


def bottom_hint(bgr) -> str:
    """底栏提示原文。判「在不在选将弹窗」，⛔ 判不了选了第几行。

    ⛔⛔ **血案来源（同 `targetlist`）**：本函数若用「裁小图单独 OCR」会读成空串，
    害门禁把"明明开着的弹窗"判成"没开到"。⇒ 一律**全屏 OCR 按 y≈940 命中**。
    """
    y = CAPTION_Y
    t = _text_near_y(bgr, y, tol=16,
                      x_range=(CAPTION_X0, CAPTION_X1))
    return t


# ══════════════════════════════════════════════════════════════════════
# 门禁
# ══════════════════════════════════════════════════════════════════════

def check_picker(bgr) -> dict:
    """门禁：现在开着「選擇武將」弹窗吗？

    判据 = **底栏说明字命中 `HINT_PICK_MARKS`**（`請選擇執行武將` /
    `請選擇武將` 这类游戏自己写的状态）。这是这屏自己的内容，
    ⛔ 不用 `go`/`info`/`func` 锚点（一览类屏保留战略面外框会假阳性，
    见「全武將一覽」血案）。

    为什么只用底栏、不硬加"名字列有内容"作第二票：
      底栏是游戏**权威信号**；若连底栏都读不出，那本就不是弹窗（或 OCR 抖到
      认不出），此时**宁可拒掉也不瞎点**。反向夹具（干净面/都市武将屏/命令界面/
      一覽屏）的底栏都不含这些字 ⇒ 零误收。
    """
    hint = bottom_hint(bgr)
    hit = [m for m in HINT_PICK_MARKS if m in hint]
    rows = _name_rows(bgr)
    out = {"ok": bool(hit), "hint": hint, "hint_hit": hit,
           "name_rows": len(rows), "why": ""}
    if not hit:
        out["why"] = ("底栏读到 %r，没有「選擇/執行武將」的任何锚点 ⇒ "
                      "**不在选将弹窗**，什么都不点。" % hint)
    return out


# ══════════════════════════════════════════════════════════════════════
# 解析名单
# ══════════════════════════════════════════════════════════════════════

def _name_boxes(bgr) -> list[dict]:
    """名字列区域里的 OCR 框（x∈[NAME_X0,NAME_X1]、y∈[NAME_Y0,NAME_Y1]）。"""
    return [b for b in _ocr_boxes(bgr)
            if NAME_X0 <= (b.get("x") or -1) <= NAME_X1
            and NAME_Y0 <= (b.get("y") or -1) <= NAME_Y1]


def _name_rows(bgr) -> list[int]:
    """返回可见行槽位的 y；OCR 漏掉某行时保留该槽位为 unread。

    选将窗口的数据行位置是固定网格（413/443/473...）。旧实现只收集
    有文字的 OCR y，导致首行漏读后把后续武将错误编号成 row=1、2。
    现在先把命中的 OCR y 对齐到固定网格，再从 row 0 补到最后一个命中槽位；
    这只补可见列表中的物理行，不猜姓名。
    """
    ys = [b["y"] for b in _name_boxes(bgr) if (b.get("text") or "").strip()]
    if not ys:
        return []
    aligned = []
    for y in ys:
        slot = int(round((int(y) - PICK_ROWS_Y0) / PICK_ROW_PITCH))
        if slot >= 0:
            aligned.append(slot)
    if not aligned:
        return []
    max_slot = min(max(aligned), 7)
    return [PICK_ROWS_Y0 + i * PICK_ROW_PITCH for i in range(max_slot + 1)]


def _row_name(boxes, y: int) -> str | None:
    """取某一行武将名。**全屏 OCR 按 (y,x) 命中**，⛔ 不裁小图。

    剥掉名字前偶尔带的勾选框字形（`中廖化`→`廖化`），只留纯汉字；
    多尺度可能给出不同读法（如 `中廖化` / `廖化`）⇒ 取出现最多的、且最长。
    """
    cand = []
    for b in boxes:
        if abs(b["y"] - y) > 14:
            continue
        t = (b.get("text") or "").strip()
        if not t:
            continue
        # 剥勾选框字形（只剥**开头**的，避免误伤真名）
        t2 = re.sub(r'^[' + re.escape(CHECKBOX_GLYPHS) + r']+', '', t)
        t2 = "".join(ch for ch in t2 if "\u4e00" <= ch <= "\u9fff")
        if len(t2) >= 2:
            cand.append(t2)
    if not cand:
        return None
    best = max(set(cand), key=lambda t: (cand.count(t), len(t)))
    return best


def parse_picker(bgr, officers=None) -> dict:
    """解析「選擇武將」弹窗：每行是哪个武将 + 行号。

    `officers` 是候选武将表（给 namelock 用，形如
    `["龔都"]` 或 `[{"name":"龔都","aka":["龚都"]}, …]`）。不传就退化成
    "只给原文 + candidate"，**不许据此点人**。
    """
    gate = check_picker(bgr)
    out: dict = {"ok": False, "gate": gate,
                 "single_select_note": ("本弹窗可能是单选（輸送）或多选（设施类）。"
                                        "单选点一下即回、无「決定」；多选才需 `decide=True`。")}
    if not gate["ok"]:
        out["stage"] = "gate"
        out["why"] = gate["why"]
        return out

    boxes = _name_boxes(bgr)
    rows_ys = _name_rows(bgr)
    raws = [_row_name(boxes, y) for y in rows_ys]

    locks = None
    if officers:
        locks = namelock.lock_many([r for r in raws], officers, unique=True)

    rows, unread = [], []
    for i, y in enumerate(rows_ys):
        row_idx = int(round((y - PICK_ROWS_Y0) / PICK_ROW_PITCH))
        raw = raws[i]
        item = {"row": row_idx, "y": y, "raw": raw,
                "ocr_variants": sorted({t for t in
                                        [b.get("text") or "" for b in boxes
                                         if abs(b["y"] - y) <= 14] if t})}
        if raw is None:
            item["status"] = "unread"
            item["officer"] = None
            item["note"] = ("这行有字（OCR 在名字列命中过）但**读不出名字** ⇒ "
                            "**不是空行**，是 OCR 没认出来。⛔ 不要当它不存在、不要猜。")
            unread.append(row_idx)
        elif locks is not None:
            lk = locks[i]
            item["status"] = lk["status"]
            item["officer"] = lk.get("locked") if lk["status"] == "locked" else None
            item["lock"] = lk
        else:
            item["status"] = "candidate"
            item["officer"] = None
            item["note"] = "没给候选武将表 ⇒ 只能给原文，⛔ 不许据此点人"
        rows.append(item)

    rows.sort(key=lambda r: r["row"])
    out.update({"rows": rows, "n_rows": len(rows), "unread": unread,
                "ok": True, "stage": "parse"})
    # 核心不变式：locked 的行必须给出 officer，candidate/unread 必须为 None
    bad = [r["row"] for r in rows
           if (r["status"] == "locked") != (r["officer"] is not None)]
    if bad:
        out["invariant_violation"] = ("locked 与 officer 不一致的行 %s" % bad)
    return out


# ══════════════════════════════════════════════════════════════════════
# 选人（在线动作；离线测试整段 monkeypatch）
# ══════════════════════════════════════════════════════════════════════

def _resolve_name_to_row(parsed_rows, name: str, officers) -> tuple[dict | None, dict]:
    """把请求的名字锁到名单行。返回 (命中的行 dict 或 None, namelock 结果)。

    用同一份 `officers` 表：先锁「请求名」拿到 canonical，再在
    `parsed_rows`（已用同一表锁过）里找那个 canonical 的行。
    ⛔ 锁不上（candidate/unknown）⇒ 返回 None，调用方**零点击**。
    """
    lk = namelock.lock(name, officers)
    if lk["status"] != "locked":
        return None, lk
    canon = lk["locked"]
    for r in parsed_rows:
        if r.get("status") == "locked" and r.get("officer") == canon:
            return r, lk
    # canonical 合法但不在本弹窗名单里（例如要选的人这屏没列出）
    return None, lk


def _find_decide(bgr):
    """帧内找「決定」绿色按钮（多选才需要）。找不到返回 None。"""
    b = bgr[:, :, 0].astype(int)
    g = bgr[:, :, 1].astype(int)
    r = bgr[:, :, 2].astype(int)
    mask = (g > 80) & (g - r > 40) & (g - b > 20)
    band = mask[PICK_BTN_BAND[0]:PICK_BTN_BAND[1], 700:1010]
    if not band.any():
        return None
    n, _l, st, _c = cv2.connectedComponentsWithStats(
        np.ascontiguousarray(band.astype(np.uint8)), 8)
    cands = []
    for k in range(1, n):
        bx, by, bw, bh, area = (int(v) for v in st[k])
        if 30 <= bw <= 220 and 16 <= bh <= 70 and area >= 300 \
           and area / max(1, bw * bh) >= 0.25:
            cands.append((area, bx + 700, by + PICK_BTN_BAND[0], bw, bh))
    if not cands:
        return None
    cands.sort(reverse=True)
    _a, x, y, bw, bh = cands[0]
    return (x + bw // 2, y + bh // 2)


def select_officer(hwnd, /, *, name: str | None = None, row: int | None = None,
                   names: list[str] | None = None, rows: list[int] | None = None,
                   officers=None, decide: bool = False,
                   max_fix: int = 2) -> dict:
    """在已开的「選擇武將」弹窗里选一个/多个武将。

    ⭐ **按名字选**是主用法：`name="龔都"` / `names=["龔都","嚴政"]`，
    模块把名字锁回名单行（靠 `officers` 候选表），再点那一行。
    **按行号选**是后备：`row=0` / `rows=[0,2]`（行号 0 起，稳定身份）。

    ⛔ **锁不上 = 不点**：`name` 锁成 candidate/unknown ⇒ 零点击、如实报。
    ⛔ **门禁不过 = 零点击**：弹窗没开就什么都不按。

    `decide=True`：多选场景点完后再点「決定」(绿)。**单选（輸送）不要传**——
    那种弹窗点一下即回、没有「決定」。

    全部在线动作（`_grab`/`winio.click_client`）在离线测试里被 monkeypatch 掉，
    **本函数本体不碰游戏**；它只决定"点哪个 y、点几下"。
    """
    from . import winio

    out: dict = {"ok": False, "clicks": [], "why": ""}

    # ── 参数合法性（互斥/组合校验）──────────────────────────────
    if (name is not None) + (row is not None) + \
       (names is not None) + (rows is not None) != 1:
        out["why"] = ("必须且只能传一组：name / row / names / rows"
                      "（实得 name=%r row=%r names=%r rows=%r）"
                      % (name, row, names, rows))
        return out
    req_names = [name] if name is not None else (names or [])
    req_rows = [row] if row is not None else (rows or [])

    # ── 门禁 ─────────────────────────────────────────────────
    bgr = _grab(hwnd)
    if bgr is None:
        out["stage"] = "grab"
        out["why"] = "抓不到画面 ⇒ 不知道弹窗在不在，**一个键都不按**"
        return out
    gate = check_picker(bgr)
    out["gate"] = gate
    if not gate["ok"]:
        out["stage"] = "gate"
        out["why"] = gate["why"]
        return out

    parsed = parse_picker(bgr, officers)
    out["parsed"] = {"n_rows": parsed.get("n_rows"), "rows": parsed.get("rows")}
    if not parsed["ok"]:
        out["stage"] = "parse"
        out["why"] = parsed.get("why", "解析失败")
        return out

    rows_data = parsed["rows"]

    # ── 解析目标行 ───────────────────────────────────────────
    targets: list[dict] = []
    if req_names:
        if officers is None:
            out["stage"] = "resolve"
            out["why"] = ("按名字选人必须传 `officers` 候选表（否则无法把名字锁回真名）"
                          "⇒ 不点。")
            return out
        for nm in req_names:
            hit, lk = _resolve_name_to_row(rows_data, nm, officers)
            out.setdefault("resolve", []).append(
                {"request": nm, "status": lk["status"],
                 "locked": lk.get("locked"), "row": (hit or {}).get("row")})
            if hit is None:
                out["stage"] = "resolve"
                out["why"] = ("名字 %r 锁不成可点身份（status=%s，最像=%s）⇒ "
                              "**不点任何行**。⛔ 拿不准就换用 row。"
                              % (nm, lk["status"], lk.get("nearest")))
                return out
            targets.append(hit)
    else:
        for rr in req_rows:
            if not isinstance(rr, int) or rr < 0:
                out["stage"] = "resolve"
                out["why"] = "row=%r 非法（必须 >=0 的整数）⇒ 不点。" % (rr,)
                return out
            hit = next((d for d in rows_data if d["row"] == rr), None)
            if hit is None:
                out["stage"] = "resolve"
                out["why"] = ("row=%d 不在名单里（本屏 0..%d，共 %d 行）⇒ 不点。"
                              % (rr, (rows_data[-1]["row"] if rows_data else -1),
                                 len(rows_data)))
                return out
            targets.append(hit)

    # ── 逐个点行 ─────────────────────────────────────────────
    out["want"] = [t["row"] for t in targets]
    for t in targets:
        winio.click_client(hwnd, PICK_ROW_X, t["y"], settle=0.30)
        time.sleep(0.4)
        out["clicks"].append({"row": t["row"], "y": t["y"], "x": PICK_ROW_X})
    out["ok"] = True
    out["stage"] = "done"

    # ── 多选：点「決定」 ─────────────────────────────────────
    if decide:
        pos = _find_decide(bgr)
        out["decide_button"] = pos
        if pos is None:
            out["ok"] = False
            out["stage"] = "decide"
            out["why"] = ("要求 `decide=True` 但帧内检不到绿色「決定」按钮 ⇒ "
                          "**没提交任何东西**。可能这是单选弹窗（无需決定），"
                          "去掉 decide=True 重试。")
            return out
        winio.click_client(hwnd, pos[0], pos[1], settle=0.35)
        time.sleep(0.5)
        out["clicks"].append({"decide": list(pos)})
        out["note"] = "已选 %d 行并点了「決定」。" % len(targets)
    else:
        out["note"] = ("已选 %d 行。⚠️ 未点「決定」—— 单选弹窗点行即回、"
                        "无需決定；多选需在调用时传 decide=True。" % len(targets))
    return out
