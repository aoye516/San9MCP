"""都市命令菜单：点都市 → 六大类 → 子命令。

这一层是内政和军事的入口，也是"能不能真的玩"的分界线。

说明书依据（`docs/04-游戏机制速查.md` 第三节）
------------------------------------------------
- P.8：「用滑鼠點選自勢力的設施（都市或港口等等）或部隊，**命令的選單便會顯示**。」
  → 菜单是**点出来的**，不会自己弹。
- P.9：「命令時の画面表示：點選設施即會顯示。」「簡易説明：將游標靜置於各命令上便會顯示。」
  「設施情報：顯示下達命令的設施情報。」
- P.13：「命令只有未行動的武將才可執行」「無法執行的命令以灰色表示」

实测几何（客户区 1280×960，见 `config/menus.json`）
---------------------------------------------------
点都市后，在点击点右下方出现一列竖排菜单：

    主菜单 6 项，中心 x=706，首项 y=411，项间距 33
        0 設施 / 1 軍事 / 2 人材 / 3 計略 / 4 外交 / 5 任免

点「設施」后，右侧再展开一列：

    子菜单，中心 x=800，首项 y=408，项间距 34
        0 巡察 / 1 商業 / 2 開墾 / 3 修築 / 4 徵兵 / 5 訓練 / 6 買進 / 7 賣出

**子菜单项数会变**：实测都市（平原）只有 8 项 ——「撤除」不出现
（说明书：撤除是「拆掉自勢力所建的設施」，都市不适用）。
且「買進/賣出」当时是**灰色**（该都市没有商人，见说明书 P.14 条件 D）。

所以：**不要按索引点，要按名字定位。** 每项都存了模板，
在当前画面里搜名字模板，分数够且位置对才点（沿用 anchors 的双条件判据）。
"""
from __future__ import annotations

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MENUS_PATH = os.path.join(ROOT, "config", "menus.json")
TPL_DIR = os.path.join(ROOT, "config", "templates", "citymenu")

# 主菜单顺序（与说明书 P.8 的六项一致）
MAIN_ITEMS = ["設施", "軍事", "人材", "計略", "外交", "任免"]

# 各子菜单的项（说明书 P.8 的命令清单）
# ⚠️ 实测（见探索区 F-057）：在**普通都市**上「設施」子菜单**只有 8 项**，没有「撤除」
# （巡察/商業/開墾/修築/徵兵/訓練/買進/賣出）。撤除只在选中"自势力所建的设施"时才出现
# （推测，未验证）。索引 0~7 是可靠的，第 8 项保留在表里但普通都市上不存在。
SUBMENU_ITEMS: dict[str, list[str]] = {
    "設施": ["巡察", "商業", "開墾", "修築", "徵兵", "訓練", "買進", "賣出", "撤除"],
    "軍事": ["出征", "建設", "輸送"],
    "人材": ["召來", "移動", "探索", "登庸"],
    "計略": ["離間", "燒夷", "奪取", "流言", "偽報", "擾亂", "激勵", "救援"],
    "外交": ["贈與", "請求", "交還", "勸降"],
    "任免": ["委任", "軍師", "爵位", "褒獎", "授與", "沒收", "處斬", "流放"],
}

DEFAULT_GEOMETRY = {
    "main": {"cx": 706, "y0": 411, "pitch": 33, "search_r": 60},
    "sub": {"cx": 800, "y0": 408, "pitch": 34, "search_r": 60},
    "min_score": 0.80,
    "max_drift": 14,
}


def load_geometry() -> dict:
    if os.path.exists(MENUS_PATH):
        with open(MENUS_PATH, "r", encoding="utf-8") as f:
            g = json.load(f)
        for k, v in DEFAULT_GEOMETRY.items():
            if k not in g:
                g[k] = v
        return g
    os.makedirs(os.path.dirname(MENUS_PATH), exist_ok=True)
    with open(MENUS_PATH, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_GEOMETRY, f, ensure_ascii=False, indent=2)
    return dict(DEFAULT_GEOMETRY)


# 模板文件名用 ASCII —— 中文文件名会让 cv2.imwrite 静默失败（踩过）。
ASCII_NAMES: dict[str, str] = {
    "設施": "shisetsu", "軍事": "gunji", "人材": "jinzai",
    "計略": "keiryaku", "外交": "gaiko", "任免": "ninmen",
    "巡察": "junsatsu", "商業": "shogyo", "開墾": "kaikon", "修築": "shuchiku",
    "徵兵": "chouhei", "訓練": "kunren", "買進": "kaishin", "賣出": "uridashi",
    "撤除": "tekijo", "出征": "shussei", "建設": "kensetsu", "輸送": "yusou",
    "召來": "shorai", "移動": "idou", "探索": "tansaku", "登庸": "touyou",
    "離間": "rikan", "燒夷": "shoui", "奪取": "dasshu", "流言": "ryugen",
    "偽報": "giho", "擾亂": "jouran", "激勵": "gekirei", "救援": "kyuen",
    "贈與": "zouyo", "請求": "seikyu", "交還": "koken", "勸降": "kanjou",
    "委任": "inin", "軍師": "gunshi", "爵位": "shakui", "褒獎": "houshou",
    "授與": "juyo", "沒收": "bosshu", "處斬": "shozan", "流放": "ruhou",
}


def tpl_path(which: str, index: int, name: str) -> str:
    slug = ASCII_NAMES.get(name, str(index))
    return os.path.join(TPL_DIR, f"{which}_{index}_{slug}.jpg")


def locate(bgra: bytes, w: int, h: int, which: str, name: str,
           geom: dict | None = None) -> dict:
    """在画面里找某个菜单项。返回 {found, score, xy, drift, name, index}。

    **不假定菜单在哪**，全屏搜模板，再用"列结构"自校验。

    为什么不能按固定位置搜（踩过）：
    菜单是锚定在**都市图形**上的，不是锚定在鼠标点击点上。实测两个都市位置：

        都市 (669,398) → 菜单首项 (706,411)   偏移 (+37, +13)
        都市 (663,628) → 菜单首项 (701,607)   偏移 (+38, -21)

    横向偏移稳定（+37~38），**纵向偏移会变**（+13 / -21），因为菜单是贴着城市
    图形画的。所以"按固定坐标开窗口"一定会在某些城市/视角下失效。

    自校验的做法：找到目标项后，再找第 0 项（設施），要求
        两者 x 相差 ≤ 6px  ，
        两者 y 相差 ≈ index × pitch（容差 8px）
    这样即使全屏搜出现假匹配（平坦区假分数这个坑踩过），也过不了列结构这一关。
    """
    import cv2
    import numpy as np

    from .learn import load_rgb

    g = geom or load_geometry()
    items = MAIN_ITEMS if which == "main" else SUBMENU_ITEMS.get(_submenu_of(name), [])
    try:
        idx = items.index(name)
    except ValueError:
        return {"found": False, "name": name, "error": "不是该菜单的项"}
    p = tpl_path(which, idx, name)
    if not os.path.exists(p):
        return {"found": False, "name": name, "error": f"模板缺失 {p}"}
    frame = np.frombuffer(bgra, dtype=np.uint8).reshape(h, w, 4)[:, :, :3][:, :, ::-1]

    def best_of(i: int) -> tuple[float, int, int]:
        pp = tpl_path(which, i, items[i])
        if not os.path.exists(pp):
            return 0.0, -1, -1
        t = np.ascontiguousarray(load_rgb(pp)[:, :, ::-1])
        x = cv2.matchTemplate(frame, t, cv2.TM_CCOEFF_NORMED)
        _, mx, _, loc = cv2.minMaxLoc(x)
        return float(mx), int(loc[0] + t.shape[1] // 2), int(loc[1] + t.shape[0] // 2)

    score, cx, cy = best_of(idx)
    pitch = g[which]["pitch"]
    min_score = g[which].get("min_score", g["min_score"])
    if score < min_score:
        return {"found": False, "name": name, "score": round(score, 4),
                "error": f"分数 {score:.3f} 低于阈值 {min_score}"}
    if idx != 0:
        s0, c0x, c0y = best_of(0)
        if s0 < min_score:
            return {"found": False, "name": name, "score": round(score, 4),
                    "error": f"第 0 项「{items[0]}」没找到（{s0:.3f}），列结构不成立"}
        dy, dx = abs((cy - c0y) - idx * pitch), abs(cx - c0x)
        if dx > 6 or dy > 8:
            return {"found": False, "name": name, "score": round(score, 4),
                    "error": (f"列结构校验失败：与第 0 项 x 差 {dx}px（应≤6）、"
                              f"y 差 {dy}px（应≈{idx*pitch}）—— 疑似假匹配")}
    return {"found": True, "score": round(score, 4), "xy": [cx, cy],
            "name": name, "index": idx, "which": which}


def _submenu_of(name: str) -> str:
    for cat, items in SUBMENU_ITEMS.items():
        if name in items:
            return cat
    return ""
