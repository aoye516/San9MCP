"""给截图加"可信标尺"，让坐标读取不再依赖显示端缩放。

为什么需要它
------------
我（模型）看到的是**被显示端缩放过的图**：真实 1280×960 的截图会被压到约 1080 宽
（系数约 0.84）。我若直接从这种图上"目测"某个按钮的像素位置，得到的坐标 =
`真实坐标 × 0.84` —— 离原点越远偏得越多，在 (1200, 860) 附近能差 200px 以上。

这个坑已经造成过四次"点了没反应"，而且每次都以为是别的原因（DPI / 焦点 / 按键卡住）。

解法
----
在**原始分辨率**的图上画网格线，**标签写真实坐标值**。
均匀缩放会保持线的相对位置，所以显示端怎么缩放都不影响"线在哪" ——
我只要读标签，读到的就是真实坐标。

    线画在真实 x=1200 处，标签写 "1200"。
    显示端把它压到像素 1012 上显示，但标签还是 "1200"。
    → 我读 "按钮靠近 1200 这条线"，就是对的。

配套规矩（写进项目记忆）
------------------------
1. 要看位置 → 用 `san9.annotate.shot()`，不要直接看裸截图。
2. 能程序化检测的（颜色块 / 形状 / 模板）→ **优先程序化检测**，人眼只做最后确认。
   实测：`find_green_blob()` 定位「進行」得到 (1212,862)，一次就对。
"""

from __future__ import annotations

import os

import cv2
import numpy as np

from . import paths, winio

MARK = (255, 0, 255)      # 品红：竖向（x）
MARK2 = (0, 255, 255)     # 黄色：横向（y）
CROSS = (0, 0, 255)       # 红色：标记点


def save_img(path: str, bgr: np.ndarray, quality: int = 95) -> str:
    """保存图片，**对非 ASCII 路径安全**。

    `cv2.imwrite` 遇到非 ASCII 路径会返回 False 而且不报错（静默失败）。
    踩过两次：一次是目录名「三国志9」，一次是文件名 `go_進行.jpg`。
    所以统一走 `cv2.imencode` + Python 自己写字节 —— Python 的 open() 不怕 Unicode。
    """
    ext = os.path.splitext(path)[1].lower() or ".png"
    params = [cv2.IMWRITE_JPEG_QUALITY, quality] if ext in (".jpg", ".jpeg") else []
    ok, buf = cv2.imencode(ext, np.ascontiguousarray(bgr), params)
    if not ok:
        raise RuntimeError(f"编码失败: {path}")
    with open(path, "wb") as f:
        f.write(buf.tobytes())
    return path


def grid(bgr: np.ndarray, step: int = 100, label_step: int | None = None) -> np.ndarray:
    """在**原分辨率**图上画网格，标签写真实坐标值。输入/输出都是 BGR。"""
    img = np.ascontiguousarray(bgr).copy()
    h, w = img.shape[:2]
    label_step = label_step or step
    for x in range(0, w, step):
        major = (x % label_step == 0)
        cv2.line(img, (x, 0), (x, h - 1), MARK if major else MARK2, 1)
        if major:
            cv2.putText(img, str(x), (x + 3, 16), cv2.FONT_HERSHEY_SIMPLEX,
                        0.45, MARK, 1, cv2.LINE_AA)
    for y in range(0, h, step):
        major = (y % label_step == 0)
        cv2.line(img, (0, y), (w - 1, y), MARK2 if major else MARK2, 1)
        if major:
            cv2.putText(img, str(y), (3, y + 16), cv2.FONT_HERSHEY_SIMPLEX,
                        0.45, MARK2, 1, cv2.LINE_AA)
    return img


def cross(img: np.ndarray, xy: tuple[int, int], label: str = "",
          size: int = 26) -> np.ndarray:
    """在（真实坐标）处画十字和方框。"""
    x, y = int(xy[0]), int(xy[1])
    cv2.drawMarker(img, (x, y), CROSS, cv2.MARKER_CROSS, size * 2, 2)
    cv2.rectangle(img, (x - size, y - size), (x + size, y + size), CROSS, 2)
    if label:
        cv2.putText(img, f"{label} ({x},{y})", (x + size + 4, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, CROSS, 1, cv2.LINE_AA)
    return img


def shot(tag: str, folder: str = "kb", with_grid: bool = True,
         points: list[tuple[tuple[int, int], str]] | None = None,
         hwnd: int | None = None) -> str:
    """抓当前游戏画面，画上标尺（和可选标记点），存成 PNG，返回路径。

    PNG 而不是 JPG：这类标注图要反复看，不能有压缩噪点干扰小目标辨认。
    """
    hwnd = hwnd or winio.find_game("San9WPK.exe").hwnd
    w, h, buf = winio.grab(hwnd)
    img = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)[:, :, :3].copy()
    if with_grid:
        img = grid(img)
    for xy, label in (points or []):
        img = cross(img, xy, label)
    out = os.path.join(paths.DATA, folder)
    os.makedirs(out, exist_ok=True)
    p = os.path.join(out, f"{tag}.png")
    # ⚠️ 必须走 save_img：cv2.imwrite 在**非 ASCII 路径**下会用系统的 ANSI 代码页开文件，
    # 中文 tag 会被写成 GBK 乱码（实测 `設施` → `瑷柦`，踩了三次）。
    # 原来这里直接 imwrite，逼得所有调用方只敢用 ASCII tag —— 根因修掉，不必再躲。
    save_img(p, np.ascontiguousarray(img))
    return p


# ---------------------------------------------------------------- 程序化定位

def blobs(bgr: np.ndarray, mask: np.ndarray, min_area: int = 250,
          region: tuple[int, int, int, int] | None = None) -> list[dict]:
    """按掩码找连通块，按面积从大到小返回。"""
    m = mask.astype("uint8")
    if region:
        keep = np.zeros_like(m)
        x0, y0, x1, y1 = region
        keep[y0:y1, x0:x1] = m[y0:y1, x0:x1]
        m = keep
    n, lab, st, ce = cv2.connectedComponentsWithStats(m, 8)
    out = []
    for i in range(1, n):
        a = int(st[i, cv2.CC_STAT_AREA])
        if a < min_area:
            continue
        out.append({"area": a, "xy": (int(ce[i][0]), int(ce[i][1])),
                    "w": int(st[i, 2]), "h": int(st[i, 3]),
                    "box": (int(st[i, 0]), int(st[i, 1]), int(st[i, 2]), int(st[i, 3]))})
    out.sort(key=lambda z: -z["area"])
    return out


def green_mask(bgr: np.ndarray) -> np.ndarray:
    """亮绿（游戏的「第一个/肯定」按钮、以及「進行」八边形）。"""
    b, g, r = bgr[:, :, 0].astype(int), bgr[:, :, 1].astype(int), bgr[:, :, 2].astype(int)
    return (g > 110) & (g - r > 45) & (g - b > 35)


def blue_mask(bgr: np.ndarray) -> np.ndarray:
    """亮蓝（部分界面的「中止」按钮、顶部 情報/功能）。"""
    b, g, r = bgr[:, :, 0].astype(int), bgr[:, :, 1].astype(int), bgr[:, :, 2].astype(int)
    return (b > 110) & (b - r > 40) & (b - g > 25)


def red_mask(bgr: np.ndarray) -> np.ndarray:
    """亮红（部分界面的「否」按钮、红色标签）。"""
    b, g, r = bgr[:, :, 0].astype(int), bgr[:, :, 1].astype(int), bgr[:, :, 2].astype(int)
    return (r > 110) & (r - g > 45) & (r - b > 35)


def text_rows(bgr: np.ndarray, region: tuple[int, int, int, int],
              thr: int = 130, min_run: int = 6) -> list[tuple[int, int, int]]:
    """检出某区域里的"文字行"（用于测量列表行位置，别再猜行距）。

    返回 [(y起点, y终点, 该行亮像素峰值)]，按 y 排序。
    """
    x0, y0, x1, y1 = region
    sub = bgr[y0:y1, x0:x1]
    gray = sub[:, :, 0] * 0.114 + sub[:, :, 1] * 0.587 + sub[:, :, 2] * 0.299
    rows = (gray > thr).sum(axis=1)
    segs: list[list[int]] = []
    for i, v in enumerate(rows):
        if v >= min_run:
            y = y0 + i
            if segs and y - segs[-1][1] <= 2:
                segs[-1][1] = y
                segs[-1][2] = max(segs[-1][2], int(v))
            else:
                segs.append([y, y, int(v)])
    return [(a, b, c) for a, b, c in segs]
