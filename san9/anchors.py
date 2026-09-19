"""锚点（anchor）—— "这个画面上本来该有的按钮，现在还在不在"。

为什么需要这一层
================
判断"画面通不通畅"（有没有东西挡在路上）用图像识别是可以做得**很稳**的，
但前提是判据选对。我在这上面走了很大弯路，把结论钉在这里：

❌ **不要用"弹窗长什么样"做判据。**
   我曾经拿弹窗的装饰边框花纹当模板在整屏里搜，实测：
   真弹窗 0.866，战略面误报 0.799~0.812 —— 分差只有 0.037，阈值纯靠硬卡。
   而且弹窗类型无穷，一种新弹窗就得重新调参。

✅ **要用"我预期的东西不见了"做判据。**
   战略面右下角永远有「進行」。它被挡住 = 有东西挡在路上。实测：

       干净战略面        1.000 / 0.992      ← 通畅
       都市面板打开      0.314 / 0.315      ← 遮挡
       真实事件弹窗      0.342 / 0.344      ← 遮挡

   分差 3 倍。而且这个判据**不需要认识任何一种弹窗** —— 弹窗长什么样都无所谓，
   它只要"挡住了"就会被抓到。

另外一个必须记住的坑：**匹配必须限制在预期位置附近的小窗口里搜**。
整屏搜索时，`TM_CCOEFF_NORMED` 会在**平坦低纹理区域**产生极高的假分数
（实测一张表格的渐变边框能在整屏搜索里拿到 0.964）。限定 ±40px 就没这个问题，
顺带还快得多。
"""
from __future__ import annotations

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANCHORS_PATH = os.path.join(ROOT, "config", "anchors.json")
TPL_DIR = os.path.join(ROOT, "config", "templates")

# 搜索窗口半径（客户区像素）。按钮位置是固定的，40px 够了。
SEARCH_R = 40

# 匹配位置**允许偏离预期中心的距离上限**。
#
# 为什么必须有这道闸：`TM_CCOEFF_NORMED` 在平坦低纹理区域会产生很高的假分数。
# 实测一块表格的渐变边框在整屏搜索里拿到过 0.964 —— 那次我据此点下去，
# 点在了表格上，而工具报的是"成功"。加上位置约束后，假匹配即使分数高，
# 只要落点偏离超过这个值就不认。
#
# 教训：**判据要带两个条件（像不像 + 位置对不对），只看分数一定会出事。**
MAX_DRIFT = 14

DEFAULT_ANCHORS: dict[str, list[dict]] = {
    # `identity` 锚点回答"我在这个画面上吗"，`required` 锚点回答"这个画面通畅吗"。
    #
    # 为什么要用锚点认画面，而不是整屏指纹：
    # 实测同是战略面，地图换到南皮、季节换成夏天之后，指纹距离从 0.008
    # 直接跳到 0.166 —— 认不出了。因为指纹里包含了地图区域，而地图会滚、
    # 季节会变。顶栏的「情報 / 功能」按钮则钉死在原地，跟地图无关。
    "strategy": [
        # ⚠️ 模板文件名一律用纯 ASCII。
        # 起因：`go_進行.jpg` 文件名含中文时，`cv2.imwrite` **静默失败**（返回 False 不报错），
        # 导致"重裁了模板"其实没写盘，排查了很久。读写图片统一走 `annotate.save_img` /
        # `learn.load_rgb`，且文件名只用 ASCII。
        {"name": "go", "template": "templates/go.jpg", "center": [1212, 862],
         "min_score": 0.75, "required": True, "identity": False},
        {"name": "info", "template": "templates/info.jpg", "center": [1132, 28],
         "min_score": 0.75, "required": False, "identity": True},
        {"name": "func", "template": "templates/func.jpg", "center": [1218, 28],
         "min_score": 0.75, "required": False, "identity": True},
    ],
}


class AnchorHit(dict):
    """一个锚点的探测结果。dict 是为了方便直接 json 序列化。"""

    @property
    def found(self) -> bool:
        return bool(self.get("found"))

    def __repr__(self) -> str:
        return (f"<{self.get('name')} {'✓' if self.found else '✗'}"
                f" {self.get('score', 0):.3f} @{self.get('xy')}>")


class AnchorBook:
    """每个画面登记一组"本该在的按钮"。落盘 config/anchors.json。"""

    def __init__(self, path: str = ANCHORS_PATH):
        self.path = path
        self.data: dict[str, list[dict]] = {}
        self._tpl_cache: dict[str, object] = {}
        self.load()

    # ---------------- 落盘 ----------------

    def load(self) -> None:
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
        else:
            self.data = {k: [dict(a) for a in v] for k, v in DEFAULT_ANCHORS.items()}
            self.save()

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def add(self, screen: str, name: str, template_rel: str,
            center: tuple[int, int], min_score: float = 0.75,
            required: bool = False) -> None:
        lst = self.data.setdefault(screen, [])
        for a in lst:
            if a["name"] == name:
                a.update({"template": template_rel, "center": list(center),
                          "min_score": min_score, "required": required})
                self.save()
                return
        lst.append({"name": name, "template": template_rel, "center": list(center),
                    "min_score": min_score, "required": required})
        self.save()

    # ---------------- 探测 ----------------

    def _tpl(self, rel: str):
        if rel in self._tpl_cache:
            return self._tpl_cache[rel]
        import numpy as np

        from .learn import load_rgb

        p = os.path.join(ROOT, "config", rel)
        if not os.path.exists(p):
            self._tpl_cache[rel] = None
            return None
        arr = load_rgb(p)[:, :, ::-1].copy()  # RGB -> BGR
        self._tpl_cache[rel] = np.ascontiguousarray(arr)
        return self._tpl_cache[rel]

    def state(self, bgra: bytes, w: int, h: int, screen: str) -> list[AnchorHit]:
        """逐个探测锚点。返回列表，每项含 found / score / xy。"""
        import cv2
        import numpy as np

        anchors = self.data.get(screen) or []
        if not anchors:
            return []
        frame = np.frombuffer(bgra, dtype=np.uint8).reshape(h, w, 4)[:, :, :3][:, :, ::-1]
        out: list[AnchorHit] = []
        for a in anchors:
            tpl = self._tpl(a["template"])
            hit = AnchorHit(name=a["name"], found=False, score=0.0, drift=None,
                            xy=list(a["center"]), required=bool(a.get("required")),
                            identity=bool(a.get("identity")))
            if tpl is None:
                hit["error"] = "模板缺失"
                out.append(hit)
                continue
            th, tw = tpl.shape[:2]
            cx, cy = a["center"]
            x0 = max(0, min(w - tw, cx - tw // 2 - SEARCH_R))
            y0 = max(0, min(h - th, cy - th // 2 - SEARCH_R))
            x1 = min(w, x0 + tw + 2 * SEARCH_R)
            y1 = min(h, y0 + th + 2 * SEARCH_R)
            if x1 - x0 < tw or y1 - y0 < th:
                hit["error"] = "按钮超出画面"
                out.append(hit)
                continue
            win = frame[y0:y1, x0:x1]
            res = cv2.matchTemplate(win, tpl, cv2.TM_CCOEFF_NORMED)
            _, mx, _, loc = cv2.minMaxLoc(res)
            lx = int(x0 + loc[0] + tw // 2)
            ly = int(y0 + loc[1] + th // 2)
            drift = ((lx - cx) ** 2 + (ly - cy) ** 2) ** 0.5
            hit["score"] = round(float(mx), 4)
            hit["xy"] = [lx, ly]
            hit["drift"] = round(drift, 1)
            # 两个条件都要满足：**像**（分数）**且**在**对的位置**（偏移）。
            # 只看分数会被平坦区的假匹配骗到。
            hit["found"] = bool(mx >= a.get("min_score", 0.75) and drift <= MAX_DRIFT)
            if mx >= a.get("min_score", 0.75) and drift > MAX_DRIFT:
                hit["reject"] = f"分数够({mx:.3f})但偏离预期中心 {drift:.0f}px —— 判为假匹配"
            out.append(hit)
        return out

    def is_clear(self, bgra: bytes, w: int, h: int, screen: str):
        """画面通畅吗？返回 (通畅, 详情列表)。

        "通畅" = 所有 `required` 锚点都在。默认只有 `go` 是 required，
        所以语义就是"「進行」还在 = 没东西挡着"。
        """
        st = self.state(bgra, w, h, screen)
        if not st:
            return True, st  # 没登记锚点就不拦，别误伤
        clear = all(hit.found for hit in st if hit.get("required"))
        return clear, st

    def identify(self, bgra: bytes, w: int, h: int, screen: str) -> str | None:
        """用锚点判断"我在不在这个画面上"。返回画面名或 None。

        比整屏指纹稳得多：指纹会随地图滚动/季节变化而失效（实测同是战略面，
        换到南皮的夏天距离就跳到 0.166 认不出），而顶栏按钮钉死在原地。
        """
        st = self.state(bgra, w, h, screen)
        ident = [a for a in st if a.get("identity")]
        if not ident:
            return None
        return screen if all(a.found for a in ident) else None

    def find(self, bgra: bytes, w: int, h: int, screen: str,
             name: str) -> AnchorHit | None:
        for hit in self.state(bgra, w, h, screen):
            if hit["name"] == name:
                return hit
        return None
