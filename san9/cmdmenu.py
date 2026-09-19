"""命令菜单的定位与解读（主菜单 6 项 + 子菜单若干项，含「置灰=不可执行」）。

## 为什么要做这件事

说明书 P.13 写「无法执行的命令以灰色表示」。**这是 agent 每旬最需要的免费状态信号**
—— 不用读内存、不用开界面，看一眼菜单就知道哪些命令现在能做。

它也解释了一批"进不去"的怪现象：命令置灰时**光标仍然停得上去**，
只是按 enter 无事发生（帧差 ≈ 24.7，而真正进入界面是 42~53）。

## 实测几何（2026-09-17 夜，南皮，客户区 1280×960）

```
主菜单列  x_left ≈ 600   宽 ≈ 70    6 项（固定）  行距 35
子菜单列  x_left ≈ 683   宽 ≈ 76    项数不定      行距 35  ← 比主菜单右移 83
两列 y 起点相同
```

⚠️ **绝对位置随都市在屏幕上的位置变，一律现测，不硬编码。**

## 颜色判据（BGR，两条件，实测无灰区）

| 类别 | 中位色 | 彩色度 max−min | 平均亮度 |
|---|---|---|---|
| 可用（子菜单蓝） | (214,107,57) | **157** | 126 |
| 可用（主菜单绿） | (41,107,41) | **66** | 63 |
| 子菜单展开时的主项（高亮） | (222,123,74) | 148 | 139 |
| **置灰不可用** | (98,98,97) | **1** | **97** |
| 非按钮（面板底/地图） | (34,58,42) | 24 | **44** |

→ **可用 = 彩 > 40；置灰 = 彩 <= 20 且均亮 >= 80；其余 = 不是按钮（列表到此为止）。**
只看彩色度会漏掉面板底（彩 24），必须配"均亮"这条，两条一起才干净。

## ⚠️ 读之前必须把鼠标移开菜单

鼠标悬停会把菜单项变成高亮色（和"禁用灰"完全不同，但会污染"可用"的判定基准）。
`read_menu()` 自己不移动鼠标（那是会改变游戏状态的动作），调用方先移。
"""

from __future__ import annotations

import numpy as np

from . import lexicon, ocr, winio

MAIN_X_GAP = 83          # 子菜单列相对主菜单列右移（实测）
PITCH = 35               # 行距
ITEM_H = 30
MAX_MAIN = 6             # 主菜单恒为 6 项
MAX_SUB = 9
ENABLED_CF = 40          # 彩 > 此值 = 可用
GREY_CF = 20             # 彩 <= 此值 且
GREY_BRIGHT = 80         # 均亮 >= 此值 = 灰色禁用按钮
SEARCH_Y = (140, 900)    # 只在战略面地图区找，避开顶栏和底部提示栏
MOUSE_PARK = (320, 820)  # 读菜单前把鼠标移到这里（实测不压菜单、不触发悬停）


def _channels(img: np.ndarray):
    b = img[:, :, 0].astype(int)
    g = img[:, :, 1].astype(int)
    r = img[:, :, 2].astype(int)
    return b, g, r


def colorfulness(bgr: np.ndarray) -> np.ndarray:
    """彩色度 = max(B,G,R) − min(B,G,R)。灰的恒为 0，彩色的 >= 60。"""
    b, g, r = _channels(bgr)
    return (np.maximum(np.maximum(b, g), r)
            - np.minimum(np.minimum(b, g), r)).astype(np.uint8)


def _menu_button_mask(img: np.ndarray) -> np.ndarray:
    """菜单按钮的掩码 —— 只有这两种特定的色（实测），地形基本不撞。"""
    b, g, r = _channels(img)
    blue = (abs(b - 210) < 45) & (abs(g - 110) < 40) & (abs(r - 60) < 36)
    green = (abs(b - 41) < 26) & (abs(g - 105) < 32) & (abs(r - 41) < 26)
    m = (blue | green).astype(np.uint8)
    m[:SEARCH_Y[0], :] = 0
    m[SEARCH_Y[1]:, :] = 0
    return m


def _blobs(mask: np.ndarray, min_area: int = 300) -> list[tuple[int, int, int, int]]:
    import cv2

    m = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 9), np.uint8))
    n, _lab, st, _ce = cv2.connectedComponentsWithStats(m, 8)
    out = []
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if a >= min_area and 45 <= w <= 200 and 18 <= h <= 38:
            out.append((int(x), int(y), int(w), int(h)))
    return out


def _sample(img: np.ndarray, x0: int, yc: int,
            half: int = 7, dx: int = 3, dw: int = 11) -> tuple[tuple, int, int]:
    """取按钮**左侧空白带**的中位色（避开中间的文字）。"""
    yc = int(yc)
    patch = img[max(0, yc - half):yc + half, x0 + dx:x0 + dx + dw]
    if patch.size == 0:
        return (0, 0, 0), 0, 0
    med = np.median(patch.reshape(-1, 3), axis=0)
    cf = int(med.max() - med.min())
    return tuple(int(v) for v in med), cf, int(med.mean())


def classify(img: np.ndarray, x0: int, yc: int) -> str:
    """这一行是 `enabled` / `grey` / `none`（不是按钮）。"""
    _med, cf, bright = _sample(img, x0, yc)
    if cf > ENABLED_CF:
        return "enabled"
    if cf <= GREY_CF and bright >= GREY_BRIGHT:
        return "grey"
    return "none"


def locate_menu_columns(img: np.ndarray) -> dict | None:
    """定位两列按钮。返回 {'main': {...}, 'sub': {...}|None}，找不到返回 None。

    ## 为什么这么绕（踩过的坑）

    1. **不能向上走**：菜单上面是地图/城墙，"中性灰 + 亮度 110"的墙体会被
       `classify` 当成灰色禁用按钮 → 一路往上走停不下来（实测走到 458）。
    2. **首项常常是"高亮态"**，比别的按钮大一圈，会和右列首项粘成一个宽块，
       连通块给不出它的上边界。
    3. **但主菜单恒为 6 项**（说明书 P.13 + 实测），所以：
       从任一已知行**只向下走**到末项（下面一定是暗面板底 → 干净地停），
       首项 = 末项 − 5×35。**向下可靠、向上不可靠**，就用可靠的那半边。
    4. 子菜单的首项和主菜单首项同一行（实测差 ~2px），所以子菜单的网格
       直接继承主菜单的首项。
    """
    boxes = _blobs(_menu_button_mask(img))
    if not boxes:
        return None

    singles = [b for b in boxes if b[2] <= 90]
    xs = sorted(b[0] for b in singles)
    clusters: list[list[int]] = []
    for x in xs:
        if clusters and x - clusters[-1][-1] <= 6:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    cols = [int(np.median(c)) for c in clusters if len(c) >= 3]
    if not cols:
        return None
    cols.sort()

    main_x = cols[0]
    ref_main = min(b[1] for b in singles if abs(b[0] - main_x) <= 6)
    last_main, _n = _walk_down(img, main_x, ref_main)
    first_main = last_main - (MAX_MAIN - 1) * PITCH
    if first_main < SEARCH_Y[0] - 40:
        first_main = ref_main
    main_w = int(np.median([b[2] for b in singles if abs(b[0] - main_x) <= 6] or [72]))
    main = {"x0": main_x, "top": first_main, "n": MAX_MAIN,
            "w": main_w, "pitch": PITCH}

    # 子菜单列：优先用"另一簇"，否则用实测固定偏移 83
    sub_x = None
    for c in cols[1:]:
        if 55 <= c - main_x <= 115:
            sub_x = c
            break
    if sub_x is None:
        cand = main_x + MAIN_X_GAP
        if classify(img, cand, first_main + ITEM_H // 2) != "none":
            sub_x = cand
    sub = None
    if sub_x is not None:
        ref_sub = first_main + 2                     # 实测：右列比左列低约 2px
        last_sub, n_sub = _walk_down(img, sub_x, ref_sub)
        sub_w = int(np.median([b[2] for b in singles if abs(b[0] - sub_x) <= 6] or [76]))
        sub = {"x0": sub_x, "top": ref_sub,
               "n": (last_sub - ref_sub) // PITCH + 1, "w": sub_w, "pitch": PITCH}
    return {"main": main, "sub": sub}


def _walk_down(img: np.ndarray, x0: int, ref_top: int) -> tuple[int, int]:
    """从参考行沿 35px 网格**只向下**走，返回（末项 top, 项数）。

    向下是安全的：菜单下面一定是暗面板底/说明面板，`classify` 干净地返回 `none`。
    """
    last = ref_top
    for _ in range(MAX_SUB + MAX_MAIN):
        cand = last + PITCH
        if cand + ITEM_H > SEARCH_Y[1]:
            break
        if classify(img, x0, cand + ITEM_H // 2) == "none":
            break
        last = cand
    return last, (last - ref_top) // PITCH + 1


def item_center(col: dict, k: int) -> tuple[int, int]:
    return (col["x0"] + col.get("w", 70) // 2,
            int(col["top"] + k * col["pitch"] + ITEM_H // 2))


def read_menu(hwnd: int | None = None, img: np.ndarray | None = None,
              expected: list[str] | None = None, names: bool = True) -> dict:
    """读命令菜单。

    `expected` 给**子菜单**的命令表（`citymenu.SUBMENU_ITEMS[菜单名]`）——
    项序是固定的，有了它连 OCR 都可以不要（`names=False` 时纯几何 + 颜色）。

    返回：
      {'found', 'main': [...], 'sub': [...], 'sub_count',
       'sub_enabled': [可用项名], 'sub_disabled': [置灰项名], 'columns'}

    ⚠️ 调用方**先**把鼠标移到 `MOUSE_PARK`（悬停会污染颜色判定）。
    """
    if img is None:
        w, h, buf = winio.grab(hwnd)
        img = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)[:, :, :3].copy()
    cols = locate_menu_columns(img)
    if cols is None:
        return {"found": False, "main": [], "sub": [], "sub_count": 0,
                "sub_enabled": [], "sub_disabled": [], "columns": None}

    def build(col: dict, exp: list[str], limit: int) -> list:
        rows = []
        for k in range(min(col["n"], limit)):
            x, y = item_center(col, k)
            kind = classify(img, col["x0"], y)
            raw = ""
            if names:
                try:
                    raw = "".join(i["text"] for i in
                                  ocr.read(img[max(0, y - 11):y + 11,
                                               col["x0"] - 2:col["x0"] + col["w"] + 2],
                                            upscale=4))
                except Exception:
                    pass
            nm = _snap(raw, exp, k)
            _med, cf, _br = _sample(img, col["x0"], y)
            rows.append({"index": k, "name": nm, "raw": raw,
                         "enabled": kind == "enabled",
                         "disabled": kind == "grey",
                         "colorfulness": cf, "x": x, "y": y})
        return rows

    main = build(cols["main"], lexicon.MENUS, MAX_MAIN)
    sub = build(cols["sub"], expected or [], MAX_SUB) if cols.get("sub") else []
    return {"found": True, "main": main, "sub": sub, "sub_count": len(sub),
            "sub_enabled": [s["name"] for s in sub if s["enabled"]],
            "sub_disabled": [s["name"] for s in sub if s["disabled"]],
            "columns": cols}


def _snap(raw: str, expected: list[str], k: int) -> str:
    """名字：闭集优先，其次 OCR 纠错，最后退回期望表里的第 k 项。"""
    if expected:
        for w in expected:
            if w == raw:
                return w
        for w in expected:
            if len(w) >= 2 and w in raw:
                return w
        if k < len(expected):
            return expected[k]
    if not raw:
        return ""
    c, conf, _how = lexicon.correct(raw[:6])
    return c if conf >= 0.7 else raw[:6]


# ---------------------------------------------------------------- 高层入口

def open_and_read(hwnd: int, menu_index: int, city_row: int = 0,
                  park: bool = True) -> dict:
    """完整走一遍：切城 → 点都市 → 走到第 menu_index 项 → 展开子菜单 → 读。

    这是 agent「每旬看一眼有哪些命令能做」的入口。
    """
    import time

    from . import citymenu, cmdscreen

    winio.focus(hwnd)
    cmdscreen.select_city_row(hwnd, city_row)
    time.sleep(1.6)
    pos = cmdscreen.CITY_CENTER   # 已验证：选中行后设施在屏幕正中（别用 find_city_on_map）
    if pos is None:
        return {"found": False, "error": "地图上找不到都市"}
    winio.click_client(hwnd, pos[0], pos[1], settle=0.35)
    time.sleep(1.2)
    if park:
        winio.move_cursor_input(*MOUSE_PARK)
        time.sleep(0.3)
    for _ in range(menu_index):
        winio.press("down")
        time.sleep(0.12)
    time.sleep(0.25)
    winio.press("right")
    time.sleep(0.9)
    menu_name = lexicon.MENUS[menu_index] if menu_index < len(lexicon.MENUS) else ""
    exp = citymenu.SUBMENU_ITEMS.get(menu_name)
    r = read_menu(hwnd=hwnd, expected=exp)
    # 定位偶尔会失败（子菜单里可用项少于 3 个时，彩色块聚不成列）——重开一次再读
    for _ in range(2):
        if r.get("found") and r.get("sub"):
            break
        winio.press("left")
        time.sleep(0.4)
        winio.press("right")
        time.sleep(0.9)
        r = read_menu(hwnd=hwnd, expected=exp)
    r["menu"] = menu_name
    return r
