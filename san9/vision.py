"""界面识别：判断"现在游戏停在哪个画面"。

为什么不用 OCR 做这件事
----------------------
OCR 慢、会错字、对动画敏感。而《三国志9》的界面是**固定布局**的，
同一个画面永远长一个样。所以用"降采样灰度签名 + 最近邻"就够了：
几毫秒出结果，而且不会把「確定」认成别的东西。

这套东西的用途只有一个：**让状态机知道自己在哪**。
需要读文字的地方（事件弹窗、军师进言）才真的上 OCR / 多模态。
"""
from __future__ import annotations

import base64
import json
import os
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCREENS_PATH = os.path.join(ROOT, "config", "screens.json")

SIG_CELL = 8  # 签名格子边长（像素）
BIN_THRESHOLD = 170  # 二值化阈值：亮于此值算"高亮像素"

# 只对这几块"UI 骨架"做签名，整屏比对是没用的 ——
# 实测发现三国志9 的菜单背景画**会换**，而且菜单面板是**半透明**的，
# 背景画会透过来污染像素值。整屏比对时，"同一界面跨会话"(0.147) 甚至比
# "不同界面"(0.031) 的差异还大，完全不可用。
#
# 但菜单文字是亮金色压在暗底上 —— 二值化之后只剩**文字布局**，
# 背景画怎么变都不影响。实测同段内相邻帧差异 0.000、跨会话 0.035。
#
# 坐标用比例表示，跟分辨率解耦：(x0, y0, x1, y1)
#
# 顶栏那一条**必须从 x=0.20 开始** —— 左上角 0.00~0.16 是"184年2月上旬 張角"
# 这种随旬变化的信息，带进去的话战略面指纹每旬都会漂移。
UI_REGIONS = [
    (0.00, 0.150, 0.40, 0.660),  # 左侧面板：菜单列表 / 地图
    (0.20, 0.000, 0.99, 0.075),  # 顶栏（避开左上角的日期）：疆界/情報/功能
    (0.22, 0.270, 0.80, 0.620),  # 画面中央：**弹窗就盖在这里**
]
# 第三块是"弹窗哨兵"：事件弹窗一盖上来，中央区的指纹立刻对不上，
# 于是 identify() 会返回 None（认不出），而不是错误地报成"战略面"。
# 代价：如果地图大幅平移，中央区也会变 —— 但自动化流程里我们只点按钮，
# 不动地图，所以这个代价可以接受。


def signature(bgra: bytes, w: int, h: int, cell: int = SIG_CELL) -> bytes:
    """对 UI 骨架区域做二值化签名：只关心"哪里是亮的"，不关心背景。"""
    parts = []
    for fx0, fy0, fx1, fy1 in UI_REGIONS:
        x0, y0 = int(fx0 * w), int(fy0 * h)
        x1, y1 = int(fx1 * w), int(fy1 * h)
        parts.append(_bin_block(bgra, w, h, x0, y0, x1, y1, cell))
    return b"".join(parts)


def region_signature(bgra: bytes, w: int, h: int,
                     box: tuple[int, int, int, int]) -> bytes:
    """只对某个矩形区域取签名（比例坐标 0..1）。"""
    x0, y0, x1, y1 = box
    return _bin_block(bgra, w, h, int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h))


def _bin_block(bgra: bytes, w: int, h: int, x0: int, y0: int, x1: int, y1: int,
               cell: int = SIG_CELL) -> bytes:
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 <= x0 or y1 <= y0:
        return b""
    cx = max(1, (x1 - x0) // cell)
    cy = max(1, (y1 - y0) // cell)
    out = bytearray(cx * cy)
    for j in range(cy):
        ya = y0 + j * (y1 - y0) // cy
        yb = max(ya + 1, y0 + (j + 1) * (y1 - y0) // cy)
        for i in range(cx):
            xa = x0 + i * (x1 - x0) // cx
            xb = max(xa + 1, x0 + (i + 1) * (x1 - x0) // cx)
            hit = 0
            tot = 0
            for y in range(ya, yb):
                row = y * w * 4
                for x in range(xa, xb):
                    p = row + x * 4
                    lum = (bgra[p] * 114 + bgra[p + 1] * 587 + bgra[p + 2] * 299) // 1000
                    tot += 1
                    if lum > BIN_THRESHOLD:
                        hit += 1
            out[j * cx + i] = (hit * 255) // max(tot, 1)
    return bytes(out)


def bright_ratio(sig: bytes) -> float:
    """签名里有亮像素的比例。太低说明这块区域是纯黑的（比如换了个画面），
    这时二值签名没有信息量，不能作为判据。"""
    if not sig:
        return 0.0
    return sum(1 for b in sig if b > 0) / len(sig)


def frame_hash(bgra: bytes, w: int, h: int, gx: int = 24, gy: int = 18) -> bytes:
    """整屏的粗糙灰度指纹，只用来回答一个问题：**画面还在动吗**。

    这是"区分过旬演出和事件弹窗"的关键原语，而且它**不需要认识任何画面**：

        游戏自己在跑（过旬演出 / 行军 / 战斗报告）→ 画面一直在变
        有弹窗停在那儿等我们拍板                → 画面纹丝不动

    采样很稀（24×18 = 432 个点），所以极快，而且对水面波光这种小动画不敏感。
    """
    out = bytearray(gx * gy)
    if not bgra or w <= 0 or h <= 0:
        return bytes(out)
    for j in range(gy):
        y = min(h - 1, (j * h) // gy)
        row = y * w * 4
        for i in range(gx):
            x = min(w - 1, (i * w) // gx)
            p = row + x * 4
            out[j * gx + i] = (bgra[p] * 114 + bgra[p + 1] * 587 + bgra[p + 2] * 299) // 1000
    return bytes(out)


# ---------------------------------------------------------------- 弹窗检测
#
# 为什么单独做这件事
# ------------------
# 实战踩到的：`仕官待遇`（華歆来投）这种事件弹窗弹出来的时候，
# 界面指纹仍然把画面认成"战略面"（距离才 0.0227）—— 因为指纹只看左侧面板和顶栏，
# 而弹窗盖在画面中间。结果工具会以为还在战略面，继续点「進行」，**把事件整个跳过去**。
#
# 所以必须有一个独立的"有没有弹窗"判断，而且优先级要高于指纹。
# 做法：拿弹窗的装饰边框花纹当模板，在整屏里搜。所有弹窗共用这套边框。

DIALOG_MARKER = os.path.join(ROOT, "config", "templates", "dialog_marker.jpg")


def has_dialog(bgra: bytes, w: int, h: int, threshold: float = 0.84):
    """画面中间有没有弹窗。返回 (有没有, 匹配度, (x, y))。

    ⚠️ 已知局限（待改进）：模板匹配的分数区分度不够宽 ——
    真弹窗 0.866，战略面误报 0.799~0.812，阈值卡在 0.84 只是勉强分开。
    位置约束（10%~80% 横向）没能干掉误报，因为误报恰好也落在 (282,268)。

    更好的做法（还没做）：判据应该是"画面中央是不是一块大面积均匀的深色面板"，
    而不是边缘花纹匹配。二值签名对深色面板是瞎的 —— 它只看得见亮色文字。
    """
    import cv2
    import numpy as np

    from .learn import load_rgb

    if not os.path.exists(DIALOG_MARKER):
        return False, 0.0, (0, 0)
    tpl = load_rgb(DIALOG_MARKER)[:, :, ::-1].copy()  # RGB -> BGR
    th, tw = tpl.shape[:2]
    if th > h or tw > w:
        return False, 0.0, (0, 0)
    frame = np.frombuffer(bgra, dtype=np.uint8).reshape(h, w, 4)[:, :, :3][:, :, ::-1]
    res = cv2.matchTemplate(frame, tpl, cv2.TM_CCOEFF_NORMED)
    _, mx, _, loc = cv2.minMaxLoc(res)
    x, y = int(loc[0]), int(loc[1])
    if not (0.10 * w <= x <= 0.80 * w):
        return False, float(mx), (x, y)   # 命中在右侧面板上 —— 那是面板边框，不是弹窗
    return bool(mx >= threshold), float(mx), (x, y)


def distance(a: bytes, b: bytes) -> float:
    """平均绝对差 / 255。0 = 一模一样，1 = 全反。"""
    if len(a) != len(b):
        return 1.0
    return sum(abs(x - y) for x, y in zip(a, b)) / (255 * len(a))


class ScreenBook:
    """已知画面的指纹库，落盘在 config/screens.json。"""

    def __init__(self, path: str = SCREENS_PATH):
        self.path = path
        self.entries: dict[str, dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as f:
                self.entries = json.load(f)
        else:
            self.entries = {}

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.entries, f, ensure_ascii=False, indent=2)

    def learn(self, name: str, bgra: bytes, w: int, h: int,
              note: str = "", region: tuple[int, int, int, int] | None = None,
              overwrite: bool = False) -> None:
        if name in self.entries and not overwrite:
            note = self.entries[name].get("note", note)
        sig = region_signature(bgra, w, h, region) if region else signature(bgra, w, h)
        entry = {
            "sig": base64.b64encode(sig).decode(),
            "w": w, "h": h, "note": note,
        }
        if region:
            entry["region"] = list(region)
        self.entries[name] = entry

    def identify(self, bgra: bytes, w: int, h: int,
                 threshold: float = 0.10, margin: float = 0.55) -> tuple[str | None, float, list]:
        """返回 (最匹配的画面名, 距离, 排名列表)。

        两道关卡，缺一不可：

        * `threshold` 绝对门限 —— 太不像就直接不认
        * `margin` **边距检验** —— 第一名必须比第二名明显更像（至少要低 45%）。
          只靠绝对门限会翻车：主菜单和它的子菜单共用背景画，
          第一名距离很低但第二名也一样低，说明**根本分不开**，这时候
          正确的做法是老实说"认不出"，而不是挑一个看起来最像的。
        """
        ranked = []
        for name, e in self.entries.items():
            if e.get("w") != w or e.get("h") != h:
                continue
            ref = base64.b64decode(e["sig"])
            region = e.get("region")
            cur = region_signature(bgra, w, h, tuple(region)) if region else signature(bgra, w, h)
            ranked.append((name, distance(cur, ref)))
        ranked.sort(key=lambda x: x[1])
        if not ranked:
            return None, 1.0, []
        best, dist = ranked[0]
        if dist > threshold:
            return None, dist, ranked[:5]
        if len(ranked) > 1:
            second = ranked[1][1]
            if second > 1e-6 and dist > margin * second:
                # 第一名没有明显优势 —— 分不开，别硬认
                return None, dist, ranked[:5]
        return best, dist, ranked[:5]
