# -*- coding: utf-8 -*-
"""情報 → 進行記錄：每旬"发生了什么"的摘要。用户的提示，实测可用。

## 实测事实（2026-09-17 夜，全部有截图/像素证据）

- 右上「情報」按钮 **= (1101, 33)**（OCR 文字框中心；锚点给的 1132/28 是模板中心，偏 30px）
- 点一下**打开**下拉菜单；菜单项 x 中心 ≈1117，首项 y≈93，**行距 29.4**
- 第 9 项「進行記錄」**y≈334**（运行时用 OCR 现量，不写死）
- ⛔ **Esc 关不掉这个下拉菜单**（并排图证据：干净/点开/挪鼠标/Esc 四帧逐像素比对，
  菜单在 IMM→AFTER→ESC 三帧里完全一样）
  ✅ 关它的办法：**再点一次情報**（已验）、点另一个顶栏按钮（已验）、点地图空白（同类）
- ✅ **Esc 能关掉「進行記錄」屏幕**（实测退出成功）
- 「進行記錄」屏幕右下「關閉」**= (1159, 884)**；文字区 x105-870, y120-700，字大、好读

## 血泪教训（写在这里防止再犯）

判断"关掉了没有"**必须跟干净基线比**。拿"菜单开着的帧"和"操作后的帧"比，
变化=0 表示**没变（还开着）**，我却读成"关掉了"——方向反了，白折腾一轮。

用法：
    from san9 import journal
    r = journal.read(hwnd)     # -> {'ok','lines','note','item_xy'}
"""
import re
import time

import numpy as np

from . import cmdscreen, ocr, winio

INFO_BTN = (1101, 33)
MENU_REG = (1040, 45, 1215, 420)      # 情报下拉菜单所在区
MENU_INFO = (1050, 55, 1200, 410)     # 菜单区（判"开没开"用）
JOURNAL_TEXT = (105, 120, 870, 700)   # 進行記錄的文字区
JOURNAL_CLOSE = (1159, 884)
CAPTION_REG = (250, 888, 1020, 952)
"""進行記錄屏幕底部的说明文字（实测「畫面上顯示的是進行記錄。」）。"""

CAPTION_MARKS = ("進行", "记", "記錄", "记錦")
""""進行記錄"屏幕的判据字。⚠️ 菜单里「進行記錄」和「年表」是**相邻两项**
（实测 y=334 / y=364，行距 29.4），菜单纵向位置还会随界面变 —— 点错一项就会
打开年表。所以**开完必须核实**，不能假定点对了。"""
PARK = (640, 700)
OPEN_WAIT = 2.4
ITEM_WAIT = 3.0
MENU_PIX = 8000
LINE_PITCH = 16

# 这套小字 OCR 必然读错一两个字。下面是**戰報高频词**的定点修复
# （比 lexicon 的通用纠错更狠，但只在这一处用，避免污染其它读数）。
FIXES = [
    (r"際(?=[受發被])", "隊"), (r"际(?=[受發發被])", "隊"),
    (r"^郸|郸(?=[受損])", "郭"), (r"^到被(?=何進)", "劉鮮被"),
    (r"掠報|掠报|嫁報|嫁报", "據報"), (r"掠报", "據報"),
    (r"^陈留|^陳留", "陳留"), (r"陈留", "陳留"),
    (r"谢(?=[施發行])", "對"), (r"际(?=[發發])", "隊"),
    (r"澈軍|澈军", "敵軍"), (r"套取", "奪取"),
    (r"齐射|舞射|奔射|齊身", "齊射"), (r"發幼|發助|發勁|發劲", "發動"),
    (r"康了", "庸了"), (r"登康", "登庸"),
    (r"张(?=[梁郃良])", "張"), (r"张梁", "張梁"), (r"张邻|張邻", "張郃"),
    (r"邻|郵(?=靖)", "鄧"), (r"袁衔|袁術|袁衔", "袁術"),
    (r"際受損", "隊受損"),
    (r"齐射|齊身", "齊射"),
    (r"齐", "齊"), (r"受损", "受損"), (r"擊", "擊"),
    (r"白馬港《》|白马港<）|白馬港（\)", "白馬港"),
    (r"耐久度降至", "耐久度降至"),
    (r"烧夷|燒夷", "燒夷"), (r"激励", "激勵"),
    (r"金減", "金錢減"), (r"金减少", "金錢減少"),
    (r"的的", "的"),
]


def clean(line: str) -> str:
    """把战报行的常见误读修回来。"""
    t = line
    for pat, rep in FIXES:
        t = re.sub(pat, rep, t)
    return t.strip()


def _frame(hwnd):
    w, h, buf = winio.grab(hwnd)
    return np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)[:, :, :3].copy()


def _nch(a, b, region=None) -> int:
    d = np.abs(b.astype(int) - a.astype(int)).mean(axis=2)
    if region:
        x0, y0, x1, y1 = region
        d = d[y0:y1, x0:x1]
    return int((d > 25).sum())


def _park(mouse_park=PARK):
    winio.move_cursor_input(*mouse_park)
    time.sleep(0.3)


def menu_is_open(hwnd, base=None) -> bool:
    """菜单开没开 —— **跟干净基线比**（base=None 时现抓一帧当基线，
    只在"确定当前是干净面"时用）。"""
    cur = _frame(hwnd)
    ref = base if base is not None else _frame(hwnd)
    return _nch(ref, cur, MENU_INFO) > MENU_PIX


MENU_WORDS = ("全官爵", "全势力", "全軍圖", "全都市", "全地域", "全部隊",
              "全武将", "全武將", "全宝物", "全寶物", "進行記錄", "年表", "全")
"""菜单里**一定**会出现的字 —— 用来核实"菜单真的开了"。

⚠️ 判据必须是**内容**，不是面积。踩过：点情報之后右侧面板的刷新也会让
菜单区变化 >8000 像素，于是判据说"开了"，实际没开 → 接着去点菜单项坐标，
点到的其实是地图，**什么都没发生**（用户看到的就是"点了半天啥也没点到"）。
"""


def open_menu(hwnd, tries: int = 2) -> bool:
    """点开情報下拉菜单。返回是否真的开了（**以 OCR 读到菜单里的字为准**）。"""
    for _k in range(tries):
        _park()
        winio.click_client(hwnd, INFO_BTN[0], INFO_BTN[1], settle=0.45)
        time.sleep(OPEN_WAIT)
        _park()
        for _x, _y, t in scan_menu(hwnd):
            if any(w in t for w in MENU_WORDS):
                return True
        time.sleep(0.8)          # 没开成 → 再点一次（它是开关式）
    return False


def close_menu(hwnd) -> bool:
    """关掉下拉菜单：**再点一次情報**（Esc 在这里无效，别用）。"""
    opened = _frame(hwnd)
    _park()
    winio.click_client(hwnd, INFO_BTN[0], INFO_BTN[1], settle=0.45)
    time.sleep(OPEN_WAIT)
    _park()
    after = _frame(hwnd)
    return _nch(opened, after, MENU_INFO) > MENU_PIX


# ⛔⛔ 2026-09-19 **删除** `ITEM_FALLBACK = (1117, 334)`。
# 那是"OCR 读不到菜单项就拿硬编码坐标点"的写法 —— **属于禁止的盲点**。
# 当时的辩解是"后面有开屏核实拦着"。但核实只能保证**不出脏数据**，
# 保证不了**不点错**：点错会打开「年表」之类的别的屏，把界面留脏，还得再收拾。
# 定格规矩：**定位不到 → 什么都不点 → 如实报错**。
# （这一项平时是 OCR 现量出来的：菜单项 x 中心 ≈1117，首项 y≈93，行距 29.4。）


def scan_menu(hwnd):
    """把菜单区的 OCR 结果按行归并。返回 [(x, y, text)]，供诊断与查找。"""
    rows: dict[int, tuple[int, int, str]] = {}
    for reg in ((1040, 45, 1215, 420), (1050, 45, 1200, 420),
                (1070, 55, 1170, 400)):
        for up in (3, 4, 5, 6):
            try:
                items = ocr.read_screen(hwnd, reg, upscale=up)
            except Exception:
                continue
            for it in items:
                t = (it["text"] or "").strip()
                if len(t) < 2:
                    continue
                k = round(it["y"] / 14)
                if k not in rows or len(t) > len(rows[k][2]):
                    rows[k] = (it["x"], it["y"], t)
    return [(v[0], v[1], v[2]) for k, v in sorted(rows.items())]


def find_item(hwnd, keywords=("進行", "記錄", "记录")):
    """OCR 现量菜单项，找目标项。找不到返回 None（调用方用兜底坐标）。"""
    for x, y, t in scan_menu(hwnd):
        if any(w in t for w in keywords):
            return (x, y, t)
    return None


def read_lines(hwnd) -> list[str]:
    """读進行記錄的文字列表（多尺度并集）。"""
    rows: dict[int, tuple[int, str]] = {}
    for up in (2, 3, 4):
        try:
            items = ocr.read_screen(hwnd, JOURNAL_TEXT, upscale=up)
        except Exception:
            continue
        for it in items:
            t = (it["text"] or "").strip()
            if len(t) < 2:
                continue
            key = round(it["y"] / LINE_PITCH)
            if key not in rows or len(t) > len(rows[key][1]):
                rows[key] = (it["y"], t)
    return [clean(v[1]) for k, v in sorted(rows.items())]


def _back_to_strategy(hwnd, tries: int = 2) -> bool:
    """从進行記錄屏幕退出来。**这里 Esc 是有效的**（和下拉菜单不同）。"""
    for _ in range(tries):
        _park()
        winio.press("escape")
        time.sleep(1.1)
        if cmdscreen._go_clear(hwnd):
            return True
    for xy in (JOURNAL_CLOSE,):
        winio.click_client(hwnd, xy[0], xy[1], settle=0.5)
        time.sleep(1.4)
        if cmdscreen._go_clear(hwnd):
            return True
    for kind in ("red", "brown"):
        pos = cmdscreen.find_button(hwnd, kind, 830, 940, 1050, 1280)
        if pos:
            winio.click_client(hwnd, pos[0], pos[1], settle=0.5)
            time.sleep(1.4)
            if cmdscreen._go_clear(hwnd):
                return True
    return False


def read(hwnd, want_lines: bool = True) -> dict:
    """打开 情報→進行記錄，读回本旬事件，退出并校验。"""
    out: dict = {"ok": False, "lines": [], "note": "", "item_xy": None}
    if not open_menu(hwnd):
        out["note"] = "情報菜单没打开（点击后菜单区无变化）"
        close_menu(hwnd)
        return out
    seen = scan_menu(hwnd)
    out["menu_ocr"] = [t for _x, _y, t in seen]
    tgt = find_item(hwnd)
    if not tgt:
        # ⛔ 2026-09-19：定位不到 → **什么都不点**，如实报错（原先是拿兜底坐标硬点）
        out["why"] = ("情報菜单里**没定位到「進行記錄」这一项**（OCR 实际读到的是 %s）→ "
                      "按规矩**停手不点**。可重试一次；若反复读不到，"
                      "说明菜单项 OCR 需要修 —— 而不是该硬点一个坐标。"
                      % (out["menu_ocr"],))
        close_menu(hwnd)
        return out
    out["item_xy"] = [tgt[0], tgt[1]]
    winio.click_client(hwnd, tgt[0], tgt[1], settle=0.5)
    time.sleep(ITEM_WAIT)
    if cmdscreen._go_clear(hwnd):          # 还在战略面 = 屏幕没换
        out["note"] = f"点了「{tgt[2]}」但屏幕没换"
        close_menu(hwnd)
        return out
    # ★ 核实打开的到底是不是「進行記錄」—— 菜单里它和「年表」只差一行
    cap = ""
    try:
        cap = "".join(i["text"] for i in
                      ocr.read_screen(hwnd, CAPTION_REG, upscale=4)).strip()
    except Exception:
        pass
    out["caption"] = cap
    if cap and not any(m in cap for m in CAPTION_MARKS):
        out["note"] = f"打开的不是進行記錄（底部文字读到：{cap}）"
        _back_to_strategy(hwnd)
        return out

    if want_lines:
        out["lines"] = read_lines(hwnd)
    back = _back_to_strategy(hwnd)
    out["ok"] = bool(out["lines"]) and back
    if not back:
        out["note"] = "读到了文字，但没能干净退出"
    elif not out["lines"]:
        out["note"] = "屏幕开了但一个字都没读到"
    return out
