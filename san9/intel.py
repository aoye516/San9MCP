# -*- coding: utf-8 -*-
"""情報 → 一覽屏（全武將 / 全都市 / …）的读取。

## 这个模块补的是什么缺口

`san9_officers` 只能回答「这城**还有几个人**能下令」（`在任 X/Y`），
回答不了「**是哪几个**」。而"派谁去做事"必须知道名字 ⇒ 名册要从「情報 → 全武將」一覽读。

## 设计：解析与动手**彻底分开**

- `parse_officer_list(bgr, roster)` —— **纯函数**，喂一帧图出一份名册。
  可以完全离线回归（夹具 `explore/intel/i5_wujiang.jpg`），**改解析逻辑不用碰游戏**。
- `open_list(hwnd, which)` / `close_list(hwnd)` —— 动手部分，**一次调用只做一件事**。

## 实测版面（`scripts/probe_intel_officer_layout.py` 现量，⛔ 不是目测）

帧 `explore/intel/i5_wujiang.jpg`（1280×960，184年4月中旬，張角势力）：

    表头行 y≈294：武將291 統率370 武力438 智力509 政治580 學得649 健康720 寶物790 身分860
    数据行 y = 354 起，**行距 30**，屏内 13 行
    所属设施列 x≈1146~1167（右侧那一竖列）
    底部说明 y≈944：「請選擇欲觀看其情報的武將。」← 这是**判据字**

## ⛔ 三条纪律（都是踩过的坑）

1. **能力值数字不读。** 已查证（`scripts/probe_intel_officer_glyph.py`）：
   这个屏的字号与顶栏**不是同一套**（切出的字形高度混 5~14、宽度混 2~8），
   拿顶栏模板库去套命中率只 **4.8%**，而那 4 个"命中"距离**全是 7 = 贴着上限**
   —— 正是"擦边命中大概率是错的"。⇒ 要读能力值必须**为这个屏自建模板库**，
   现在不读，`abilities` 一律 `null` + 说明原因。**宁可没有，不要错的。**
2. **名字是"选择器文字"** ⇒ 一律过 `namelock`，只有 `locked` 级才算可点身份。
   `candidate` 级如实标出来，**不许当成定论**。
3. **漏读必须报出来。** 实测有一行（y≈564）名字 OCR 读不出 —— 那一行的
   能力值在屏上是有的。所以要**按固定行表逐行检查**，读不出就报 `unread`，
   ⛔ **不许因为"名字读不到"就把这行从名册里删掉**（那会让 agent 以为这城人更少）。
"""
from __future__ import annotations

import collections
import os
import time

from . import namelock, ocr, paths

# ── 版面常量（全部由 probe_intel_officer_layout.py 在实拍帧上现量）
HEAD_Y = 294
"""表头行 y。核对用：这一行必须能读到「武將/統率/武力/智力/政治」中的几个。"""

ROW0_Y = 354
ROW_PITCH = 30
N_ROWS = 13
"""一屏 13 行。⚠️ 不代表势力只有 13 人 —— 一覽屏**可能要翻页**，见 `parse_officer_list` 的 `page_note`。"""

ROW_TOL = 12
"""归行容差。行距 30 ⇒ 12 既够宽（吸收多尺度抖动）又不会串到邻行。"""

COL_NAME_X = 291
COL_NAME_HALF = 34
"""武将名列。半宽 34 ≈ 到「統率」列(370)的中点，不会读进能力值。"""

# ⛔⛔ 2026-09-19 **删除 `COL_AT_X` / `COL_AT_HALF`（"所属设施列"）—— 那一列不存在。**
#
# 我最初以为屏幕右侧 x≈1146~1167 那一竖列是「所属设施」，写进了 `at_raw`。
# 第一次跑就露馅：`韓暹` 在名册里是「南皮」，`at_raw` 却给「無平原」。
# `scripts/probe_intel_at_column.py` 量出三条硬证据：
#   ① y 间距是 **25 / 50 / 51**，不是数据行的 30 ⇒ 不同步
#   ② 共 **16** 个 y，数据行 **13** 个 ⇒ 个数就对不上
#   ③ 内容是 平原/南皮/北海/濮陽/小沛/樂陵港/安德港/… ⇒ 这是**地图上的城名标注**
# 那是一覽屏右侧压着的**小地图**，跟表格毫无关系。
#
# 教训（与 `商人='駐在'` 同一类）：**"看起来像那一列"不是证据。**
# 想要所属设施，正确做法是点一覽屏的「所屬」栏目按钮切换栏目
# （说明书 P.11：全武將有「能力 / 所屬 / 命令 / 個人」四个栏目），
# 那才是真的所属设施列 —— 现在没做，就**不给这个字段**。

CAPTION_REG = (400, 928, 1000, 960)
CAPTION_MARKS = ("武將", "武将", "情報", "情报", "選擇", "选择")
"""底部说明文字的判据。实测读到「請選擇欲觀看其情報的武將。」

⚠️ **必须核实"我到底在哪个屏"** —— 情報菜单里「全武將」和「全部隊」「全都市」
是相邻项，点错一项会打开别的一覽屏，**版面完全不同**。
（`journal.py` 已经因为「進行記錄」和「年表」相邻而吃过这个教训。）
"""

LIST_KINDS = {
    "officer": {"menu_words": ("全武將", "全武将"), "caption": CAPTION_MARKS},
    "city": {"menu_words": ("全都市",), "caption": ("都市",)},
}
"""支持的一覽屏。目前只实现 `officer` 的解析；`city` 先占位（菜单词已验证可读）。"""


def _ocr_boxes(bgr, upscales=(2, 3, 4)) -> list[dict]:
    """多尺度并集的 OCR 框。一覽屏字比顶栏大，2~4 倍够用。"""
    out: list[dict] = []
    for up in upscales:
        try:
            out += ocr.read(bgr, upscale=up)
        except Exception:
            pass
    return out


def _col_text(boxes, y: int, cx: int, half: int) -> str | None:
    """取 (行 y, 列 cx) 这一格的文字：同格多尺度读数里**取最长的那个**。

    取最长的理由：小字 OCR 的典型失败是**少读一个字**（`張燕`→`燕`），
    很少多读。最长的那个信息最全。
    """
    cand = [(b["text"] or "").strip() for b in boxes
            if abs(b["y"] - y) <= ROW_TOL and abs(b["x"] - cx) <= half
            and (b["text"] or "").strip()]
    if not cand:
        return None
    cand.sort(key=len, reverse=True)
    return cand[0]


def check_caption(bgr, kind: str = "officer") -> dict:
    """核实"当前这一帧到底是不是那个一覽屏"。

    ⛔ 这是**硬门禁**：核不过就不许解析。
    否则别的屏（全部隊 / 全都市 / 选将列表）的城名人名会落进同样的行位被当成名册
    —— `atoms.read_panel_from` 就是因为缺这道门禁，把 `士兵='廖化'` 端出去过。
    """
    marks = LIST_KINDS.get(kind, {}).get("caption") or CAPTION_MARKS
    txt = ""
    try:
        for up in (3, 4):
            txt += "".join(i["text"] or "" for i in
                           ocr.read(bgr[CAPTION_REG[1]:CAPTION_REG[3],
                                        CAPTION_REG[0]:CAPTION_REG[2]], upscale=up))
    except Exception:
        pass
    hit = [m for m in marks if m in txt]
    return {"ok": bool(hit), "caption": txt.strip(), "hit": hit,
            "why": "" if hit else
                   ("底部说明读到 %r，没命中 %s 中任何一个 ⇒ "
                    "**当前不是「%s」一覽屏**（点错菜单项？还没开？）"
                    % (txt.strip(), list(marks), kind))}


def check_header(boxes) -> dict:
    """核实表头行在位（第二道门禁，与底部说明互相独立）。"""
    words = ("武將", "武将", "統率", "统率", "武力", "智力", "政治")
    got = []
    for b in boxes:
        if abs(b["y"] - HEAD_Y) > ROW_TOL:
            continue
        t = (b["text"] or "")
        got += [w for w in words if w in t]
    got = sorted(set(got))
    return {"ok": len(got) >= 3, "found": got,
            "why": "" if len(got) >= 3 else
                   ("表头行 y≈%d 只读到 %s（<3 个）⇒ 版面对不上，停手不解析"
                    % (HEAD_Y, got))}


def parse_officer_list(bgr, roster=None) -> dict:
    """**纯函数**：一帧「全武將一覽」→ 名册。不碰游戏，可离线回归。

    `roster`：候选表，接受 `["名字"]` 或 `[{"name":…, "aka":[…]}]`。
              **没有候选表时名字一律停在 `unknown`** —— 这是有意的：
              选择器文字没有候选表就不该有可点身份。

    返回：
      - `rows`      —— 逐行 `{row, y, name_raw, name, name_status, at_raw, …}`
                       `name` **只在 `name_status=="locked"` 时非空**
      - `unread`    —— 名字读不出来的行号（**不许静默丢弃**）
      - `locked` / `candidate` / `unknown` —— 三级计数，一眼看出可信度
      - `abilities_note` —— 为什么不给能力值（见模块头纪律 1）
    """
    out: dict = {"ok": False, "rows": [], "unread": [], "why": "",
                 "counts": {}, "abilities": None, "at": None,
                 "at_note": ("所属设施**不给** —— 屏幕右侧那一竖列是**地图城名标注**，"
                             "不是表格的列（y 间距 25/50/51 ≠ 数据行的 30，个数 16≠13）。"
                             "要拿所属设施得先点「所屬」栏目按钮切换栏目（说明书 P.11），未实现。"
                             "证据：scripts/probe_intel_at_column.py"),
                 "abilities_note": ("能力值（統率/武力/智力/政治）**故意不读** —— "
                                    "这个屏的字号与顶栏不是同一套字体，"
                                    "顶栏模板库命中率仅 4.8% 且命中距离贴着上限"
                                    "（= 擦边、可能是错的）。要读必须为本屏自建模板库。"
                                    "证据：scripts/probe_intel_officer_glyph.py")}
    boxes = _ocr_boxes(bgr)
    out["n_boxes"] = len(boxes)

    cap = check_caption(bgr, "officer")
    hdr = check_header(boxes)
    out["caption_check"] = cap
    out["header_check"] = hdr
    if not (cap["ok"] or hdr["ok"]):
        # 两道门禁都不过 ⇒ 肯定不是这个屏
        out["why"] = "不是「全武將」一覽屏。%s / %s" % (cap["why"], hdr["why"])
        return out
    if not hdr["ok"]:
        out["why"] = hdr["why"]
        return out

    # 逐行按**固定行表**取名字。⛔ 只取名字这一列 —— 别的列要么是数字
    # （字号没建库，见纪律 1），要么需要先切栏目（见上面删掉 COL_AT 的说明）。
    raws = []
    for i in range(N_ROWS):
        y = ROW0_Y + ROW_PITCH * i
        raws.append((i, y, _col_text(boxes, y, COL_NAME_X, COL_NAME_HALF)))

    locks = namelock.lock_many([r[2] or "" for r in raws], roster or [], unique=True)
    cnt: collections.Counter = collections.Counter()
    for (i, y, name_raw), lk in zip(raws, locks):
        st = lk["status"]
        cnt[st] += 1
        row = {"row": i, "y": y,
               "name_raw": name_raw,
               "name": lk["locked"],
               "name_status": st,
               "name_nearest": lk["nearest"],
               "name_ratio": lk["ratio"]}
        if st != "locked" and lk["why"]:
            row["name_why"] = lk["why"]
        out["rows"].append(row)
        if not name_raw:
            out["unread"].append(i)

    out["counts"] = dict(cnt)
    out["ok"] = bool(out["rows"])
    out["page_note"] = ("一屏 %d 行。⚠️ 势力武将可能**多于一屏** —— "
                        "本函数只读当前可见的一屏，不翻页。"
                        "行数 == %d 时要怀疑还有下一页。" % (N_ROWS, N_ROWS))
    if out["unread"]:
        out["why"] = ("第 %s 行名字读不出（该行在屏上可能是有人的）⇒ "
                      "如实报 unread，**没有从名册里删掉**" % out["unread"])
    return out


def open_list(hwnd, kind: str = "officer") -> dict:
    """打开「情報 → 全武將」一覽屏。**一次调用只做这一件事。**

    复用 `journal.py` 已实测通过的菜单机制（那条链跑通过「進行記錄」）：
      ① `journal.open_menu` 点开情報下拉菜单 —— **以 OCR 读到菜单里的字为准**，不看面积
      ② `journal.scan_menu` 现量菜单项坐标 → 找目标项
      ③ ⛔ **定位不到就什么都不点**（`journal.py` 里那个 `ITEM_FALLBACK` 硬编码坐标
         已经被删掉了，这里不许重新引入）
      ④ 点开后**核实开的是不是那个屏**（菜单里「全武將」和「全部隊」「全宝物」相邻，
         点错一项会打开版面完全不同的屏）
    """
    from . import cmdscreen, journal, winio

    words = LIST_KINDS.get(kind, {}).get("menu_words") or ()
    out: dict = {"ok": False, "kind": kind, "why": "", "menu_ocr": [],
                 "item_xy": None}
    if not words:
        out["why"] = "不认识的一覽类型 %r（支持 %s）" % (kind, sorted(LIST_KINDS))
        return out

    if not journal.open_menu(hwnd):
        out["why"] = "情報下拉菜单没打开（点击后菜单区读不到菜单项的字）"
        journal.close_menu(hwnd)
        return out

    seen = journal.scan_menu(hwnd)
    out["menu_ocr"] = [t for _x, _y, t in seen]
    tgt = None
    for x, y, t in seen:
        if any(w in t for w in words):
            tgt = (x, y, t)
            break
    if not tgt:
        # ⛔ 定位不到 → 什么都不点（禁令，见 CONTRIBUTING.md 第 0 条）
        out["why"] = ("情報菜单里**没定位到「%s」这一项**（OCR 实际读到 %s）⇒ "
                      "按规矩**停手不点**。⛔ 不许改成硬编码坐标去试。"
                      % (words[0], out["menu_ocr"]))
        journal.close_menu(hwnd)
        return out

    out["item_xy"] = [tgt[0], tgt[1]]
    out["item_text"] = tgt[2]
    winio.click_client(hwnd, tgt[0], tgt[1], settle=0.5)
    time.sleep(journal.ITEM_WAIT)

    # ⛔⛔ 这里**不能**用 `cmdscreen._go_clear` 当"屏幕有没有换"的判据。
    # 实测（`scripts/probe_go_on_intel_frame.py`，2026-09-19 真机演示暴露）：
    # 「全武將一覽」屏**保留了战略面的整套右上/右下外框** ⇒ 在它上面
    #   go=0.9067（阈值 0.8）· info=0.8392 · func=0.9885 —— 全部命中
    # ⇒ `_go_clear` 返回 True ⇒ 会被误判成"屏幕没换"，把成功的开屏当失败报掉。
    # 对照：勢力一覽 go=0.1639 · 南皮命令菜单 go=0.1300（这两个判得对）
    # ⇒ **是全武將这一屏特殊，不是普遍规律。**
    # 唯一可靠的判据是**这个屏自己的说明字**（caption）—— 下面那步本来就在做。
    bgr = ocr._grab_bgr(hwnd)
    if bgr is None:
        out["why"] = "抓不到画面，无法核实开的是哪个屏 ⇒ 不敢往下读"
        return out
    cap = check_caption(bgr, kind)
    out["caption_check"] = cap
    if not cap["ok"]:
        # 两种情况都落这里：① 屏幕压根没换（还在战略面，菜单可能还开着）
        # ② 换了但开错屏（菜单里「全武將」和「全部隊」「全宝物」相邻）。
        # ⛔ 不去区分也不去猜 —— 两种都是"没拿到想要的屏"，如实报、不往下读。
        out["why"] = ("开出来的不是「%s」一覽屏（也可能屏幕根本没换）。%s"
                      % (words[0], cap["why"]))
        return out
    out["ok"] = True
    return out


def close_list(hwnd, kind: str = "officer", tries: int = 2) -> dict:
    """从一覽屏退回战略面。**Esc 在这类屏上是有效的**（与情報下拉菜单相反）。

    说明书 P.10：「這類畫面裡右鍵 = 返回上一畫面」；`journal.py` 实测 Esc 也行。
    ⛔ 这里**不做"多试几个坐标"的兜底** —— 退不出来就如实报，让调用方决定。

    ⛔⛔ **判据不能用 `cmdscreen._go_clear`**（2026-09-19 真机演示暴露的坑）：
    「全武將一覽」保留战略面外框，`go` 在它上面就有 0.9067 ⇒ `_go_clear` 恒为 True
    ⇒ "退屏成功"会是**假阳性**（根本没验到）。
    正确判据是**这个屏自己的说明字消失了**（caption 不再命中）。
    """
    from . import cmdscreen, winio

    out: dict = {"ok": False, "tries": 0, "why": "", "kind": kind}
    for _ in range(max(1, tries)):
        out["tries"] += 1
        winio.press("escape")
        time.sleep(1.1)
        bgr = ocr._grab_bgr(hwnd)
        if bgr is None:
            out["why"] = "按了 Esc 但抓不到画面，无法核实有没有退出去"
            return out
        cap = check_caption(bgr, kind)
        out["caption_check"] = cap
        if not cap["ok"]:
            # 一覽屏的说明字没了 ⇒ 确实离开了那个屏。
            # 再顺手记一笔 go 分数（**只作参考，不作判据**，理由见上）。
            out["go_clear"] = cmdscreen._go_clear(hwnd)
            out["ok"] = True
            return out
    out["why"] = ("按了 %d 次 Esc，一覽屏的说明字**仍在**（%r）⇒ 没退出去，如实报，"
                  "**不猜坐标去点**。可调 san9_recover。"
                  % (out["tries"], (out.get("caption_check") or {}).get("caption")))
    return out


def _cleanup_after_failed_open(hwnd) -> dict:
    """开屏失败后的收尾。**只做一个能验证的动作，然后如实报。**

    开屏失败时残留形态有两种，而我**不去猜是哪种**：
      ① 下拉菜单还开着（⛔ 这种情况 Esc **无效**，要再点一次「情報」）
      ② 开出了别的屏（这种 Esc 有效）

    做法：先按一次 Esc（覆盖 ②），再看 `go` 通不通；不通就再点一次「情報」（覆盖 ①）。
    ⚠️ 这里 `_go_clear` **可以**当判据 —— 因为此时我们已经不在全武將屏上了
    （那个屏的假阳性问题只影响"判断有没有开成/退出"，不影响"菜单关没关"）。
    ⛔ 仍然不许多落点乱点：全程只有「Esc」和「情報按钮」两个**语义明确**的动作。
    """
    from . import cmdscreen, journal, winio

    out: dict = {"ok": False, "steps": []}
    winio.press("escape")
    time.sleep(1.0)
    if cmdscreen._go_clear(hwnd):
        out["steps"].append("esc → go 通畅")
        out["ok"] = True
        return out
    out["steps"].append("esc → go 仍不通（可能下拉菜单还开着）")
    journal.close_menu(hwnd)          # 再点一次「情報」（开关式）
    time.sleep(0.8)
    out["ok"] = cmdscreen._go_clear(hwnd)
    out["steps"].append("再点情報 → go %s" % ("通畅" if out["ok"] else "仍不通"))
    if not out["ok"]:
        out["why"] = "界面没清理干净 ⇒ 请调 san9_recover 或人工按 Esc"
    return out


def read_officer_list(hwnd, keep_open: bool = False) -> dict:
    """完整一趟：开屏 → 读名册 → 退回战略面。

    ⚠️ 这是**三个会改屏幕的动作串在一起**，按纪律本该拆开。
    允许合并的唯一理由：中间那步是**纯读**，而且开屏/退屏是一对
    （开了不退会把界面留脏，比"分开调用"更危险）。
    ⛔ 但任何一步失败都**立即停下并留证**，不继续往下走。
    """
    out: dict = {"ok": False, "stage": "open", "why": ""}
    op = open_list(hwnd, "officer")
    out["open"] = op
    if not op["ok"]:
        out["why"] = op["why"]
        out["cleanup"] = _cleanup_after_failed_open(hwnd)
        return out

    out["stage"] = "read"
    bgr = ocr._grab_bgr(hwnd)
    if bgr is None:
        out["why"] = "开屏成功但抓不到画面"
        out["cleanup"] = close_list(hwnd)
        return out
    rc = load_roster_config()
    parsed = parse_officer_list(bgr, rc["candidates"])
    out["roster_config"] = {"n": rc.get("n"), "path": rc.get("path"),
                            "meta_status": rc.get("meta_status")}
    out["parsed"] = parsed

    if keep_open:
        # 调用方明确要求留在一覽屏上（人工比对用）⇒ 不退，但**必须说出来**，
        # 否则下一个工具会以为自己在战略面。
        out["stage"] = "kept_open"
        out["close"] = {"ok": True, "skipped": True,
                        "why": "keep_open=True ⇒ **没有退屏**，界面仍停在一覽屏。"
                               "后续任何战略面操作前请先退屏（Esc / san9_recover）。"}
        out["ok"] = bool(parsed.get("ok"))
        if not out["ok"]:
            out["why"] = parsed.get("why") or ""
        return out

    out["stage"] = "close"
    out["close"] = close_list(hwnd)
    out["ok"] = bool(parsed.get("ok")) and bool(out["close"]["ok"])
    if not out["ok"]:
        out["why"] = parsed.get("why") or out["close"].get("why") or ""
    return out


def load_roster_config() -> dict:
    """读 `config/officers.json`（我方名册 + 实测别名）当候选表。

    ⚠️ 这份文件的 `meta.status` 是 `unverified`（目视抄录）——
    它够用来**锁定名字**（候选表只需要"名字大致对"），但**不该当成能力值的来源**。
    """
    import io
    import json
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "config", "officers.json")
    try:
        d = json.load(io.open(p, encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "candidates": [], "why": "读不到 %s：%s" % (p, e)}
    cands = [{"name": o["name"], "aka": o.get("aka") or []}
             for o in d.get("officers") or [] if o.get("name")]
    return {"ok": bool(cands), "candidates": cands,
            "n": len(cands), "path": p,
            "meta_status": (d.get("meta") or {}).get("status"),
            "why": "" if cands else "名册是空的"}
