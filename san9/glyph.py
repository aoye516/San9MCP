# -*- coding: utf-8 -*-
"""字形模板查表读数字 —— **不用 OCR**。

    from san9 import glyph
    r = glyph.read_digits(bgr, (626, 4, 716, 58))
    # → {"value": 30631, "text": "30631", "ok": True, "chars": [...]}

## 为什么不用 OCR（这是本模块存在的全部理由）

游戏界面是**确定性渲染** —— 没有光照、没有透视、没有抖动，字体是固定位图字体。
实测（`scripts/probe_glyph_live.py` / `build_glyph_atlas.py`）：

- `資金 30631` 切出 5 个字符，第 1 与第 4 个位图 **md5 完全相同**（两个 `3`）
- `信望 185` 的 `8` 与 `兵糧 82980` 的两个 `8`，md5 **三者相同**（跨字段一致）
- 同一画面连抓两次，区域**逐字节相同**
- 9 张样本 41 个字符建表，**零冲突**（同一字形永远对应同一数字）

⇒ 这是**查表**问题。OCR 是拿概率模型去猜一个确定的答案，于是产生了一整类
"看着正常其实是错的"读数：`30631→306317`（多吐一位）· `4/4→47`（吞斜杠）·
`273302→25`（只读前两位）。这些都**不触发 low_conf**，因为模型自己很确信。

查表不一样：**要么认出来，要么明确说"这个字形没见过"**，不会编一个像样的错数。

## 匹配用汉明距离，不是 md5 相等

⚠️ md5 太脆。实测同一数字的不同变体（jpg 压缩 / 背景亮度不同）之间
**只差 1~3 个像素（共 98 个）**，而不同数字差得远得多
⇒ 用「最近邻 + 间隔检查」：最近的模板要足够近（`MAX_DIST`），
并且要比第二近的明显更近（`MIN_MARGIN`），否则算**认不出**。

## 边界：只管纯数字字段

汉字有几千个，不适合穷举模板 ⇒ **中文仍然走 OCR + 词典纠错**。
本模块只接管「顶栏三个数字」「面板数值列」这类**字符集只有 0-9 和 /** 的字段，
那也正是错得最要命的地方（数字错了 agent 会照着错数据决策）。
"""
from __future__ import annotations

import json
import os

import numpy as np

from . import paths

ATLAS_REL = "kb/glyph_atlas_digits.json"

BIN_THR = 140
"""二值化阈值。前景是亮字、背景是暗底，实测 140 能干净分开。"""

MIN_AREA = 6
MIN_W = 2
MIN_H = 5
"""连通块的最小尺寸 —— 比这更小的当噪点（主要来自 jpg 压缩）。"""

MAX_DIST = 7
"""与最近模板允许的最大汉明距离（不同像素数）。

**这个数有实测依据，不是拍的**：`scripts/dump_glyph_margins.py` 跑 63 帧顶栏、
748 个成功命中，距离分布 `0×537 / 1×96 / 2×34 / 3×59 / 4×19 / 5×2 / 6×1`，
**max=6**。取 7 = 刚好容下全部已验证命中，再多一像素就算没见过。
⚠️ 原来拍的 8 留了太多擦边空间（面板值列探查时正好出现 `dist=8` 的擦边命中，
那种"刚好卡在上限"的命中大概率是错的）。
超过它 = **这个字形没见过**，报 `unknown`，**不猜**。

⛔ 往上调之前先跑 `dump_glyph_margins.py`，用数据说话。
"""

MIN_MARGIN = 12
"""最近与次近的距离差下限。差得不够多 = 两个数字都像 ⇒ 报 `ambiguous`。

**实测依据**：同上 748 个命中，间隔分布 **min=25**、最常见 29~34。
不同数字的位图差异极大（数字之间本来就不像），所以真实间隔根本不会小。
取 12 ≈ 实测最小值的一半 = 既不误杀，又能在"两个模板真的难分"时报警。
原来拍的 3 太松（3 像素之差就敢定论）。
"""

_atlas: dict | None = None


def _load_atlas() -> dict:
    """懒加载模板库。库里是 `{key: {digit, w, h, bits}}`。"""
    global _atlas
    if _atlas is not None:
        return _atlas
    p = os.path.join(paths.DATA, ATLAS_REL)
    if not os.path.exists(p):
        _atlas = {"glyphs": {}, "why": "模板库不存在：%s（跑 scripts/build_glyph_atlas.py 建）" % p}
        return _atlas
    with open(p, encoding="utf-8") as f:
        raw = json.load(f)
    tpl = []
    for k, v in (raw.get("glyphs") or {}).items():
        bits = np.array([[int(c) for c in row] for row in v["bits"].split("|")],
                        dtype=np.uint8)
        tpl.append({"key": k, "digit": v["digit"], "bits": bits,
                    "shape": bits.shape})
    _atlas = {"glyphs": tpl, "bin_thr": raw.get("bin_thr", BIN_THR)}
    return _atlas


def reload_atlas() -> int:
    """重新加载模板库（建库后不必重启进程）。返回模板数。"""
    global _atlas
    _atlas = None
    return len(_load_atlas().get("glyphs") or [])


def binarize(sub: np.ndarray, thr: int = BIN_THR) -> np.ndarray:
    gray = sub.astype(np.int32).sum(axis=2) // 3
    return (gray > thr).astype(np.uint8)


def split_glyphs(mask: np.ndarray) -> list[dict]:
    """连通块切字符，按 x 排序。

    ⚠️ **不按固定网格切** —— 实测字符宽度是 4/6/7/8 混合（`1` 只有 4px），
    等宽切一定会切歪。
    """
    import cv2

    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    out = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < MIN_AREA or w < MIN_W or h < MIN_H:
            continue
        out.append({"x": int(x), "y": int(y), "w": int(w), "h": int(h),
                    "bits": (lab[y:y + h, x:x + w] == i).astype(np.uint8)})
    out.sort(key=lambda d: d["x"])
    return out


def match_glyph(bits: np.ndarray) -> dict:
    """单个字形 → 数字。返回 `{digit, dist, margin, status}`。

    `status` ∈ `matched` / `unknown`（没见过）/ `ambiguous`（两个都像）。
    ⛔ 任何非 `matched` 的情况 `digit` 都是 `None` —— **不猜**。
    """
    tpl = _load_atlas().get("glyphs") or []
    if not tpl:
        return {"digit": None, "status": "no_atlas", "dist": None, "margin": None}
    cands = []
    for t in tpl:
        if t["shape"] != bits.shape:      # 形状不同直接跳过（宽高就是强特征）
            continue
        cands.append((int((t["bits"] != bits).sum()), t["digit"], t["key"]))
    if not cands:
        return {"digit": None, "status": "unknown", "dist": None, "margin": None,
                "why": "没有同尺寸(%dx%d)的模板" % (bits.shape[1], bits.shape[0])}
    cands.sort()
    best_d, best_dig, best_key = cands[0]
    # 次近：**只看数字不同的候选**（同一数字的多个变体不算竞争者）
    other = [c for c in cands if c[1] != best_dig]
    second_d = other[0][0] if other else None
    margin = None if second_d is None else second_d - best_d
    if best_d > MAX_DIST:
        return {"digit": None, "status": "unknown", "dist": best_d,
                "margin": margin, "nearest": best_dig,
                "why": "最近模板距离 %d > 上限 %d ⇒ 这个字形没见过" % (best_d, MAX_DIST)}
    if margin is not None and margin < MIN_MARGIN:
        return {"digit": None, "status": "ambiguous", "dist": best_d,
                "margin": margin,
                "why": "最近(%s,d=%d)与次近(%s,d=%d)差 %d < %d ⇒ 两个都像"
                       % (best_dig, best_d, other[0][1], second_d, margin, MIN_MARGIN)}
    return {"digit": best_dig, "status": "matched", "dist": best_d,
            "margin": margin, "key": best_key}


def read_digits(bgr: np.ndarray, region: tuple[int, int, int, int]) -> dict:
    """读一个**纯数字**区域 → `{value, text, ok, chars, why}`。

    ⛔ **全有或全无**：只要有一个字符没认出来，`ok=False` 且 `value=None`。
       宁可交白卷，也不返回一个缺位的数字 —— 缺位的 `273302→2732` 比
       读不出来危险得多（调用方看不出它是错的）。
    """
    x0, y0, x1, y1 = region
    sub = bgr[y0:y1, x0:x1]
    glyphs = split_glyphs(binarize(sub))
    chars = []
    digits = []
    for g in glyphs:
        m = match_glyph(g["bits"])
        m["at"] = (x0 + g["x"], y0 + g["y"], g["w"], g["h"])
        chars.append(m)
        digits.append(m.get("digit"))
    if not glyphs:
        return {"value": None, "text": None, "ok": False, "chars": [],
                "n_glyphs": 0, "why": "该区域切不出任何字形（是空的？还是区域错了？）"}
    if any(d is None for d in digits):
        bad = [c for c in chars if c.get("digit") is None]
        return {"value": None, "text": None, "ok": False, "chars": chars,
                "n_glyphs": len(glyphs),
                "why": "%d/%d 个字形认不出（%s）⇒ 整个读数作废，不猜"
                       % (len(bad), len(glyphs),
                          "；".join(c.get("why") or c["status"] for c in bad[:3]))}
    text = "".join(digits)
    return {"value": int(text), "text": text, "ok": True, "chars": chars,
            "n_glyphs": len(glyphs),
            "max_dist": max(c["dist"] for c in chars),
            "min_margin": min([c["margin"] for c in chars if c["margin"] is not None]
                              or [None])}


def atlas_info() -> dict:
    """模板库现状（几个字形、覆盖哪些数字）。"""
    tpl = _load_atlas().get("glyphs") or []
    by: dict[str, int] = {}
    for t in tpl:
        by[t["digit"]] = by.get(t["digit"], 0) + 1
    return {"n_glyphs": len(tpl), "by_digit": dict(sorted(by.items())),
            "missing": [d for d in "0123456789" if d not in by],
            "path": os.path.join(paths.DATA, ATLAS_REL)}
