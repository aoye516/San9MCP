"""离线中文 OCR —— 用来把界面上的**名字读出来**。

为什么需要它
------------
之前所有"按名字指定"都做不到：城市名、武将名、資金数字都读不出来，
所以 `city.*` 只能吃行号。有了 OCR 才能把 `{"city": "平原", "officers": ["張角"]}`
这种人类/agent 自然表达的参数落到具体行上。

实现选型
--------
`rapidocr-onnxruntime`：纯 CPU、onnx 模型自带、离线、中文识别够用，
而且**直接吃 numpy 数组**（不碰文件路径 → 天然绕开 OpenCV 的非 ASCII 路径静默失败）。

用法
----
    from san9 import ocr
    for it in ocr.read_rows(hwnd, (1100, 300, 1230, 700)):
        print(it["text"], it["y"])      # 右侧城市列表：原文 + 行中心
    ocr.read_topbar(hwnd)               # 顶栏：年月/君主/信望/資金/兵糧
"""

from __future__ import annotations

import os
import re
import time

import cv2
import numpy as np

from . import lexicon, paths, winio

_ENGINE = None
# 小字要放大再识别 —— 游戏里的字只有 12~16px，直接喂给 OCR 会掉字
UPSCALE = 3


def engine():
    global _ENGINE
    if _ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR

        _ENGINE = RapidOCR()
    return _ENGINE


def _prep(bgr: np.ndarray, upscale: int = UPSCALE) -> np.ndarray:
    img = bgr
    if upscale != 1:
        img = cv2.resize(img, (img.shape[1] * upscale, img.shape[0] * upscale),
                         interpolation=cv2.INTER_LANCZOS4)
    return np.ascontiguousarray(img)


def read(bgr: np.ndarray, upscale: int = UPSCALE) -> list[dict]:
    """识别一张 BGR 图，返回 [{'text','box','score','x','y'}]（坐标已还原回原图）。"""
    res, _ = engine()(_prep(bgr, upscale))
    out: list[dict] = []
    for box, text, score in (res or []):
        xs = [p[0] / upscale for p in box]
        ys = [p[1] / upscale for p in box]
        out.append({"text": str(text), "score": float(score),
                    "box": [int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))],
                    "x": int(sum(xs) / len(xs)), "y": int(sum(ys) / len(ys))})
    out.sort(key=lambda d: (d["y"], d["x"]))
    return out


def read_screen(hwnd: int, region: tuple[int, int, int, int] | None = None,
                upscale: int = UPSCALE) -> list[dict]:
    """抓当前画面（可只取 region=(x0,y0,x1,y1)）并识别。"""
    w, h, buf = winio.grab(hwnd)
    img = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)[:, :, :3].copy()
    off = (0, 0)
    if region:
        x0, y0, x1, y1 = region
        img = img[y0:y1, x0:x1]
        off = (x0, y0)
    items = read(img, upscale=upscale)
    for it in items:                      # 还原成整屏坐标
        it["x"] += off[0]
        it["y"] += off[1]
        it["box"] = [it["box"][0] + off[0], it["box"][1] + off[1],
                     it["box"][2] + off[0], it["box"][3] + off[1]]
    items.sort(key=lambda d: (d["y"], d["x"]))
    return items


# ---------------------------------------------------------------- 语义读取

def read_rows(hwnd: int, region: tuple[int, int, int, int]) -> list[dict]:
    """按行归并的读数 —— 把同一水平线上被切成多块的文字拼回一行。

    返回 [{'text','y','x0','x1','items'}]，y 从小到大。
    """
    items = read_screen(hwnd, region)
    rows: list[dict] = []
    for it in items:
        placed = False
        for r in rows:
            if abs(it["y"] - r["y"]) <= 14:      # 同一行
                r["items"].append(it)
                r["y"] = int(np.mean([i["y"] for i in r["items"]]))
                placed = True
                break
        if not placed:
            rows.append({"y": it["y"], "items": [it]})
    out = []
    for r in rows:
        r["items"].sort(key=lambda i: i["x"])
        out.append({
            "text": "".join(i["text"] for i in r["items"]),
            "y": r["y"],
            "x0": min(i["box"][0] for i in r["items"]),
            "x1": max(i["box"][2] for i in r["items"]),
            "items": r["items"],
        })
    out.sort(key=lambda r: r["y"])
    return out


TOPBAR_REGION = (60, 0, 1080, 60)   # 年月 / 君主 / 信望 / 資金 / 兵糧（避开右侧按钮栏）

# ---------------------------------------------------------------- 顶栏数字字段（**分段读**）
# ⭐ 2026-09-19：三个数字**单独抠出来读**，取代"整行读"。
#
# **为什么必须分段**（实测，2026-09-19）：
#   整行 OCR 把 `資金 30631` 读成 `306317` —— **多读一位、虚高 10 倍**，
#   而且三个尺度读得**一模一样** → 原来那套 `low_conf`（靠多尺度一致性）
#   **完全不触发**。反方向也见过：`16004`→`104`（丢 2 位）、`15554`→`1554`（丢 1 位）。
#   把字段单独抠出来读（区域小、无邻近文字干扰）后，**6 个尺度全部一致且正确**：
#   信望 `185` · 資金 `30631` · 兵糧 `82980`。
#
# **区域依据**：客户区固定 **1280×960**（硬事实），顶栏各字段是**独立的框、位置固定**，
# 不随内容变化。x 范围**刻意只含数字、不含标签**（切到标签会读进杂字符）。
TOPBAR_FIELDS = {
    "faith": (524, 4, 584, 58),     # 「信望」右边的数字
    "gold":  (626, 4, 716, 58),     # 「資金」右边的数字
    "food":  (755, 4, 850, 58),     # 「兵糧」右边的数字
}


def _topbar_raw(hwnd: int, up: int) -> str:
    items = read_screen(hwnd, TOPBAR_REGION, upscale=up)
    items.sort(key=lambda d: (d["y"], d["x"]))
    return " ".join(i["text"] for i in items)


_MONTH_OCR_FIX = (("上甸", "上旬"), ("中甸", "中旬"), ("下甸", "下旬"),
                  ("上句", "上旬"), ("中句", "中旬"), ("下句", "下旬"))


def _parse_topbar(raw: str) -> dict:
    # ⚠️ **先把「下旬」的 OCR 错字掰回来再解析。**
    # 踩过：raw = `184年3月下句张角信至182青金16504兵程94350` →
    #   1) `([上中下])[甸旬]` 不匹配「句」→ month 读成 None
    #   2) 那个多出来的「句」还被 ruler 正则 `([\u4e00-\u9fff]{2,3})信[至望]`
    #      吞进去 → ruler 变成「句张角」
    # 旬 = 勹+日，句 = 勹+口，这套小字几乎必然混。
    norm = raw
    for a, b in _MONTH_OCR_FIX:
        norm = norm.replace(a, b)

    out: dict = {"raw": raw, "year": None, "month_num": None, "month": None,
                 "date": None, "ruler": None,
                 "faith": None, "gold": None, "food": None}
    m = re.search(r"(\d{2,4})\s*年", norm)
    if m:
        out["year"] = m.group(1) + "年"
    # 月份数字：「184年3月」里的 3 —— 少了它，日期就只剩「184年下旬」，信息不全
    m = re.search(r"年\s*(\d{1,2})\s*月", norm)
    if m:
        out["month_num"] = int(m.group(1))
    m = re.search(r"([上中下])[甸旬]", norm)
    if m:
        out["month"] = m.group(1) + "旬"
    if out["year"]:
        out["date"] = (out["year"]
                       + (("%d月" % out["month_num"]) if out["month_num"] else "")
                       + (out["month"] or ""))

    # ruler：⚠️ **必须先把日期那段从串里切掉再匹配**。
    # 否则「下旬」的「旬」会被 `([\u4e00-\u9fff]{2,3})信[至望]` 吞进去，
    # 名字变成「旬张角」（第一次修 month 时就踩了这个）。
    after = re.sub(r"^\s*\d{2,4}\s*年", "", norm)
    after = re.sub(r"^\s*\d{1,2}\s*月", "", after)
    after = re.sub(r"^\s*[上中下][甸旬]", "", after).strip()
    m = re.search(r"([\u4e00-\u9fff]{2,3})信[至望]", after)
    if m:
        out["ruler"] = m.group(1)

    m = re.search(r"信[至望]\s*([\d,]+)", norm)
    if m:
        out["faith"] = int(m.group(1).replace(",", ""))
    m = re.search(r"[青資責]金\s*([\d,]+)", norm)
    if m:
        out["gold"] = int(m.group(1).replace(",", ""))
    m = re.search(r"兵[程糧]\s*([\d,]+)", norm)
    if m:
        out["food"] = int(m.group(1).replace(",", ""))
    if norm != raw:
        out["raw_norm"] = norm
    return out


def _glyph_mod():
    """惰性导入 `san9.glyph` —— 顶部导入会和 `glyph` 里的 `paths` 形成环。"""
    from . import glyph as _g
    return _g


def _grab_bgr(hwnd: int) -> np.ndarray | None:
    try:
        w, h, buf = winio.grab(hwnd)
        return np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)[:, :, :3].copy()
    except Exception:
        return None


def read_digit_field(bgr: np.ndarray, region: tuple[int, int, int, int],
                     scales: tuple[int, ...] = (4, 5, 6)) -> dict:
    """**只读一个纯数字字段**（抠出区域单独 OCR），多尺度投票。

    返回 `{value, text, readings, stable, digits, why}`。

    ⛔ **只接受纯数字的读数** —— 读到杂字符说明区域切错了（切进了标签），
       那种读数**直接丢弃**，宁可返回 `None` 让上层退回整行结果，
       也不要把脏值混进来。
    ⛔ **必须多尺度一致才算 `stable`** —— 不一致就是不确定，标出来。
    """
    x0, y0, x1, y1 = region
    sub = bgr[y0:y1, x0:x1]
    readings: list[tuple[int, str]] = []
    for up in scales:
        try:
            items = read(sub, upscale=up)
        except Exception:
            continue
        t = "".join((i.get("text") or "").strip() for i in items).strip()
        if t and t.isdigit():
            readings.append((up, t))
    if not readings:
        return {"value": None, "text": None, "digits": None,
                "readings": [], "stable": False, "why": "该区域没读到纯数字"}
    texts = [t for _, t in readings]
    uniq = set(texts)
    # 投票：出现次数最多的那个；平票时取**位数最多**的（实测失败模式以"少读"居多）
    best = sorted(uniq, key=lambda t: (-texts.count(t), -len(t)))[0]
    return {"value": int(best), "text": best, "digits": len(best),
            "readings": [{"up": u, "text": t} for u, t in readings],
            "stable": len(uniq) == 1, "votes": len(texts),
            "why": None if len(uniq) == 1 else "多尺度读数不一致：%s" % sorted(uniq)}


def read_topbar(hwnd: int, scales: tuple[int, ...] = (4, 5, 6)) -> dict:
    """读顶栏。返回 {'raw','year','month','ruler','faith','gold','food'[, 'low_conf']}。

    两个必须容忍的坑：

    1. 正则写的是 OCR 的**常见错字形态**（信至/青金/兵程/上甸）——
       这套字体的小字几乎必然读错一两个字。

    2. ⚠️ **数字会掉位。** 实测把 `51172` 读成 `5112`（少一位），
       我还据此编出"资金神秘减少 4.6 万"的假解释，白追了一轮。
       所以现在**多尺度各读一遍，数字字段的位数必须一致**；
       不一致时取**位数最多**的那个（实测失败模式是"少读"而不是"多读"），
       并在返回里标 `low_conf`，让调用方知道这个值可疑。
    """
    parses: list[dict] = []
    for up in scales:
        try:
            parses.append(_parse_topbar(_topbar_raw(hwnd, up)))
        except Exception:
            continue
    if not parses:
        return {"raw": "", "year": None, "month_num": None, "month": None,
                "date": None, "ruler": None,
                "faith": None, "gold": None, "food": None}
    out: dict = {"raw": parses[0]["raw"]}
    low: list[str] = []
    for key in ("year", "month_num", "month", "date", "ruler",
                "faith", "gold", "food"):
        vals = [p[key] for p in parses if p[key] is not None]
        if not vals:
            out[key] = None
            continue
        if all(str(v) == str(vals[0]) for v in vals):
            out[key] = vals[0]
        elif key in ("faith", "gold", "food"):
            out[key] = max(vals, key=lambda v: len(str(v)))
            low.append(key)
        else:
            out[key] = vals[0]

    # ⭐⭐ **分段读校正**（2026-09-19）—— 顶栏三个数字**单独抠出来再读一遍**。
    #
    # 为什么：整行 32 个字符挤在一起时识别质量会掉，实测 `資金 30631`
    # 被整行读成 `306317`（**多一位、虚高 10 倍**），而三个尺度错得**一模一样**
    # → 上面那套"多尺度一致才算数"的 `low_conf` **根本不会触发**。
    # 抠出来单独读之后，实测 **6 个尺度全部一致且正确**。
    #
    # 所以这里：**以分段读为准**；同时把"整行读成了什么"记进 `corrected`，
    # 让这条链路能**持续暴露整行读的错**（本来是个看不见的坑）。
    bgr = _grab_bgr(hwnd)
    if bgr is not None:
        seg = {k: read_digit_field(bgr, reg, scales)
               for k, reg in TOPBAR_FIELDS.items()}
        out["fields_read"] = {k: {"text": v["text"], "stable": v["stable"]}
                              for k, v in seg.items()}
        corrected: list[dict] = []
        for key, v in seg.items():
            if v["value"] is None:
                # 分段没读到 → 保留整行结果，但明确标出来
                low.append(key)
                continue
            if out.get(key) is not None and out[key] != v["value"]:
                # ⚠️ 两个来源矛盾。**不加进 `low_conf`** ——
                # 实测分段读在 2 帧 × 3 字段 × 6 尺度 = **36 次读数全部一致且正确**，
                # 而整行读**已被证明会错**（306317 / 104 / 1554 都是它干的）。
                # 既然分段更可信，就不该因为"整行不同意"而让 agent 犹豫；
                # 记进 `corrected` 让这条错误**可见**就够了。
                corrected.append({"field": key, "full_line": out[key],
                                  "segmented": v["value"]})
            out[key] = v["value"]
            if not v["stable"]:
                low.append(key)      # 分段自己都不一致 → 那才是真不确定
        if corrected:
            out["corrected"] = corrected

        # ⭐⭐⭐ **字形查表校正**（2026-09-19）—— 三层里的**最高优先级**。
        #
        # 三层是：字形查表 > 分段 OCR > 整行 OCR。为什么字形排最前：
        #
        # 游戏是确定性渲染的固定位图字体 ⇒ 读数字是**查表**问题，不是识别问题。
        # 实测（`scripts/test_glyph_vs_ocr.py`，63 帧其中 60 帧未参与建表）：
        #   · 字形与 OCR 在**所有**双方都给出读数的字段上 **0 处不一致**
        #   · 顶栏全黑（灰度 max 63~128，一个前景像素都没有）的 7 帧里，
        #     OCR **凭空生成了 4 个五位数**（51411 / 16104 / 17561 / 17811），
        #     字形全部交白卷 —— 这就是排它在前的全部理由
        #   · 命中余量极大：最近距离 1~2，与次近数字差 27~31（不是勉强认出）
        #
        # ⛔ 字形交白卷时**不回退到 OCR 的值** —— 交白卷的两种成因
        # （区域是空的 / 出现没见过的字形）下，OCR 给的数都不可信。
        # 此时保留分段 OCR 的结果但标 `low_conf`，让 agent 知道这个值悬着。
        glyph_read: dict[str, dict] = {}
        glyph_fixed: list[dict] = []
        for key, reg in TOPBAR_FIELDS.items():
            try:
                g = _glyph_mod().read_digits(bgr, reg)
            except Exception as e:                  # 模板库缺失等 ⇒ 静默降级到 OCR
                glyph_read[key] = {"ok": False, "why": "字形模块异常：%s" % e}
                continue
            glyph_read[key] = {"text": g["text"], "ok": g["ok"],
                               "n_glyphs": g["n_glyphs"],
                               "max_dist": g.get("max_dist"),
                               "why": g.get("why")}
            if not g["ok"]:
                low.append(key)
                continue
            if out.get(key) is not None and out[key] != g["value"]:
                glyph_fixed.append({"field": key, "ocr": out[key],
                                    "glyph": g["value"]})
            out[key] = g["value"]
            # 字形认出来了 ⇒ 这个字段是**确定**的，把 OCR 打的疑标撤掉
            low = [k for k in low if k != key]
        out["glyph_read"] = glyph_read
        if glyph_fixed:
            out["glyph_fixed"] = glyph_fixed

    if low:
        out["low_conf"] = sorted(set(low))
    return out


def read_place_list(hwnd: int, region: tuple[int, int, int, int],
                    min_height: int = 8) -> list[dict]:
    """读一列"名字条目"（右侧城市列表 / 選擇武將 的武将列表）。

    两段式：先**程序化检出行的 y 范围**（不靠 OCR 定位），再**逐行裁出来单独 OCR**
    —— 整块喂给 OCR 会掉行（实测 9 行只能读出 5 行），逐行读一个不漏。
    """
    w, h, buf = winio.grab(hwnd)
    img = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)[:, :, :3].copy()
    gray = img[:, :, 0] * 0.114 + img[:, :, 1] * 0.587 + img[:, :, 2] * 0.299
    x0, y0, x1, y1 = region
    band = gray[:, x0:x1]
    rows_ = (band > 110).sum(axis=1)
    segs: list[list[int]] = []
    cur = None
    for y in range(y0, y1):
        if rows_[y] >= 4:
            if cur is None:
                cur = [y, y]
            else:
                cur[1] = y
        else:
            if cur is not None and cur[1] - cur[0] >= min_height - 1:
                segs.append(cur)
            cur = None
    if cur is not None and cur[1] - cur[0] >= min_height - 1:
        segs.append(cur)
    out = []
    pad = 3
    for a, b in segs:
        crop = img[max(0, a - pad):b + pad, x0:x1]
        items = read(crop, upscale=4)
        text = "".join(i["text"] for i in items).strip()
        name, conf, how = lexicon.correct(text)
        out.append({"y": (a + b) // 2, "y0": a, "y1": b,
                    "raw": text, "name": name, "conf": conf, "how": how})
    return out


# ---------------------------------------------------------------- 缓存

CACHE = os.path.join(paths.DATA, "ocr_cache")
os.makedirs(CACHE, exist_ok=True)


def dump(tag: str, items: list[dict]) -> str:
    """把一次识别结果存成文本，便于事后核对。"""
    p = os.path.join(CACHE, f"{tag}.txt")
    with open(p, "w", encoding="utf-8") as f:
        for it in items:
            f.write(f'{it["y"]:>4} {it.get("x", "")!s:>5}  {it["text"]}   '
                    f'({it["score"]:.2f})\n')
    return p


def read_list_multiscale(hwnd: int, region: tuple[int, int, int, int],
                         scales: tuple[int, ...] = (2, 3, 4, 5, 6),
                         drop_score: float = 0.3) -> list[dict]:
    """读一列名字条目 —— **多尺度识别 + 合并投票**。

    为什么这么绕：游戏字体小、且有字间距，单次 OCR 会漏行（实测 9 行只能读出 5 行）。
    但不同放大倍数漏的是**不同的行**，所以跑 5 个尺度取并集，基本能凑齐。

    另外整行 OCR 会在名字前面粘上噪声（"垂平原""晋南皮"），交给 lexicon.correct
    的"包含匹配"处理即可。

    返回 [{'y','name','conf','votes','raws'}]，按 y 排序。**没读出来的行不会出现**，
    调用方要拿行结构去补（见 cmdscreen.city_rows 的"按行距推断"）。
    """
    from rapidocr_onnxruntime import RapidOCR

    w, h, buf = winio.grab(hwnd)
    img = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)[:, :, :3].copy()
    x0, y0, x1, y1 = region
    crop = img[y0:y1, x0:x1]
    eng = RapidOCR(drop_score=drop_score)
    merged: dict[int, list] = {}
    for up in scales:
        big = cv2.resize(crop, (crop.shape[1] * up, crop.shape[0] * up),
                         interpolation=cv2.INTER_LANCZOS4)
        res, _ = eng(big)
        for item in (res or []):
            text = str(item[1])
            ys = [p[1] / up for p in item[0]]
            y = y0 + int(sum(ys) / len(ys))
            name, conf, _how = lexicon.correct(text)
            if conf >= 0.7:
                merged.setdefault(round(y / 12), []).append(
                    {"y": y, "name": name, "conf": conf, "raw": text})
    out = []
    for k in sorted(merged):
        hits = merged[k]
        byname: dict[str, list] = {}
        for h2 in hits:
            byname.setdefault(h2["name"], []).append(h2)
        best = max(byname, key=lambda n: (len(byname[n]),
                                          max(x["conf"] for x in byname[n])))
        ys = [h2["y"] for h2 in hits]
        out.append({"y": int(sum(ys) / len(ys)), "name": best,
                    "votes": len(byname[best]),
                    "conf": max(x["conf"] for x in byname[best]),
                    "raws": [h2["raw"] for h2 in hits]})
    out.sort(key=lambda d: d["y"])
    return out


# ---------------------------------------------------------------- 对话框标题

# 第一个是"标题本体"的紧框（实测「巡察」在 (409,231) 附近），命中率最高；
# 后面几个是兜底的大范围。
TITLE_REGIONS = [(330, 198, 560, 262), (300, 150, 950, 300),
                 (340, 170, 900, 280), (280, 140, 1000, 330)]


def read_dialog_title(hwnd: int) -> tuple[str, float]:
    """尽量把对话框左上角的**命令名**读出来，并做闭环校验。

    命令名是封闭集合（`lexicon.COMMANDS`），所以先用"包含"判据直接命中，
    命中不了才退回模糊纠正。多个区域 × 多个尺度各试一遍取最高分 ——
    实测单区域单尺度会偶发读空。
    """
    best = ("", 0.0)
    for reg in TITLE_REGIONS:
        for up in (3, 5, 7):
            try:
                items = read_screen(hwnd, reg, upscale=up)
            except Exception:
                continue
            for it in items:
                t = it["text"]
                if not t:
                    continue
                for w in lexicon.COMMANDS:
                    if w == t or (len(w) >= 2 and w in t):
                        return w, 1.0
                c, conf, _how = lexicon.correct(t[:6])
                # 只有"整条文本几乎就是某个命令名"才算，避免把「命令設施 南皮」当标题
                if conf >= 0.85 and len(t) <= 4 and c in lexicon.COMMANDS:
                    return c, conf
                if conf > best[1]:
                    best = (c, conf)
    return best


# ---------------------------------------------------------------- 本旬发生了什么
#
# ⚠️⚠️ **这个函数已经过时了，优先用 `journal.read()`（情報→進行記錄）。**
#
# 左下角这块是**过旬演出过程中**一行行刷出来的战报 —— **只在演出期间有效**，
# 演出一结束面板就没了；而且单次 OCR 会漏行。
# 过旬结束后读「進行記錄」信息更全（逐条列出本旬每条命令的结果）、可复读、可随时回看。
# 用户 2026-09-18 给这个线索的本意就是"**不用盯过旬过程**"。
# 目前只剩 play.py 里一处调用，而长跑脚本已停用 → 实际等于废弃，保留仅作备用。
#
# 实测（184年8月上旬 过旬演出期间）读到：「報告，華歆與何進平的手下做了密商」
# 「鮑信對華歆所施行的離間之計失敗了」「陳留似乎恢復了正常」
# 「陳留的張梁…齊射！」「袁術軍受損1114！」
REPORT_PANEL = (10, 718, 545, 950)


def read_report_panel(hwnd: int, upscale: int = 4) -> list[str]:
    """读左下角播报面板，返回一行行文本。

    ⛔ **只在过旬演出期间有内容**；过旬结束后请改用 `journal.read(hwnd)`。
    这个函数保留只是为了"演出中想看"这种少数场合。

    ⚠️ 反复读要**并集**：这块面板是逐行刷出来的，单次 OCR 会漏行
    （和城市列表一个道理，见 read_list_multiscale）。
    """
    w, h, buf = winio.grab(hwnd)
    img = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)[:, :, :3].copy()
    x0, y0, x1, y1 = REPORT_PANEL
    rows: dict[int, str] = {}
    for up in (3, 4, 5):
        items = read(img[y0:y1, x0:x1], upscale=up)
        for it in items:
            t = (it["text"] or "").strip()
            if len(t) < 2:
                continue
            key = round(it["y"] / 14)          # 同一行归并
            if len(t) > len(rows.get(key, "")):
                rows[key] = t
    return [rows[k] for k in sorted(rows)]
