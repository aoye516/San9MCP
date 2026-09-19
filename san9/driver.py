"""路径表：把语义动作翻译成点鼠标的序列。

这是整个项目最核心的"资产"。游戏界面是固定的，所以每一个操作都可以
预先录成一条固定路径，之后所有决策都建立在它上面。

路径表存在 config/paths.json，形如：

    "facility.patrol": [
        {"kind": "click",  "xy": [184, 596], "wait": 0.5, "note": "設施"},
        {"kind": "click",  "xy": [ 96, 600], "wait": 0.8, "note": "巡察"},
        {"kind": "rows",   "want": "{officers}", "wait": 0.5},
        {"kind": "click",  "xy": [840, 700], "wait": 0.8, "note": "執行"},
        {"kind": "wait_screen", "screen": "strategy", "timeout": 8}
    ]

步骤类型
--------
click       点客户区坐标
key         按键（esc/enter/F1…）
wait        干等
wait_screen 阻塞直到界面识别成某个名字（这是"校验"，不许省略）
rows        在武将列表里按名字勾选若干行（需要 config/officer_rows.json 的名字→行号映射）
confirm     点"是/確定"
esc_to      连按 Esc 直到回到某个界面

没标定过的路径会抛 NotCalibrated，并且明确告诉你缺什么 —— 不会"猜着点"。
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from typing import Any

from . import vision, winio

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATHS_PATH = os.path.join(ROOT, "config", "paths.json")
ROWS_PATH = os.path.join(ROOT, "config", "officer_rows.json")


class NotCalibrated(RuntimeError):
    """路径没标定过。附带怎么标定。"""


class ButtonNotFound(RuntimeError):
    """模板匹配找不到按钮 —— 说明当前不在预期界面，或者录的时候点歪了。"""


IF_TEMPLATE_MIN = 0.70
"""`if_template` 条件点击的阈值。

比 `template`（0.62）严一些：`template` 判错只是"换了个位置点"，
而 `if_template` 判错会**点错目标**（比如把地图中央当成确认框的"是"）。
宁可漏点（跳过）也不要错点。
"""


def _locate_template(step: dict, frame_rgb, radius: int = 60):
    """用模板匹配找按钮。返回 (x, y, score)。"""
    import cv2

    from . import paths
    from .learn import load_rgb

    tpl = load_rgb(paths.resolve(step["template"]))
    fh, fw = frame_rgb.shape[:2]
    th, tw = tpl.shape[:2]
    cx, cy = step.get("hint") or step["xy"]
    x0 = max(0, cx - radius - tw // 2)
    y0 = max(0, cy - radius - th // 2)
    x1 = min(fw, cx + radius + tw // 2)
    y1 = min(fh, cy + radius + th // 2)
    if x1 - x0 < tw or y1 - y0 < th:
        return None, None, 0.0
    win = frame_rgb[y0:y1, x0:x1]
    g1 = cv2.cvtColor(win, cv2.COLOR_RGB2GRAY)
    g2 = cv2.cvtColor(tpl, cv2.COLOR_RGB2GRAY)
    res = cv2.matchTemplate(g1, g2, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    return x0 + loc[0] + tw // 2, y0 + loc[1] + th // 2, float(score)


class PathTable:
    def __init__(self, path: str = PATHS_PATH):
        self.path = path
        self.data: dict[str, list[dict]] = {}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                self.data = json.load(f)

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def get(self, key: tuple[str, ...]) -> list[dict]:
        k = ".".join(key)
        steps = self.data.get(k)
        if steps is None:
            raise NotCalibrated(
                f"路径 {k!r} 还没标定。\n"
                f"  标定方法：python -m san9.calibrate --path {k}\n"
                f"  已标定的路径：{', '.join(sorted(self.data)) or '（无）'}"
            )
        return steps

    def has(self, key: tuple[str, ...]) -> bool:
        return ".".join(key) in self.data

    def record(self, key: tuple[str, ...], steps: list[dict]) -> None:
        self.data[".".join(key)] = steps
        self.save()


class Driver:
    """把步骤序列执行到游戏窗口上。"""

    def __init__(self, hwnd: int, table: PathTable, book: vision.ScreenBook,
                 dry_run: bool = False, journal: str | None = None,
                 label: str = ""):
        self.hwnd = hwnd
        self.table = table
        self.book = book
        self.dry_run = dry_run
        self.trace: list[dict] = []
        # 输入日志：每一次点击/按键**动手之前**先记一条，含当时的画面状态、
        # 「進行」锚点分数、鼠标位置。
        #
        # 为什么必须记这个：我踩过一次 —— 游戏里冒出一个"都市命令面板"，
        # 我只看了一张截图就断言"这是游戏每旬自动弹出来的"，并据此改了设计。
        # 后来发现是**我自己点出来的**（一次锚点假匹配，点在了面板上）。
        # 单张截图无法区分"游戏干的"和"我干的"，而日志可以。
        # **凡是"是不是我干的"这种问题，不许靠推断，去翻日志。**
        self.journal = journal
        self.label = label

    # ------------------------------------------------------------ 输入日志

    def _note_action(self, kind: str, **info) -> None:
        if not self.journal:
            return
        rec: dict = {"t": datetime.now().strftime("%H:%M:%S.%f")[:-3],
                     "label": self.label, "kind": kind, **info}
        try:
            w, h, buf = winio.grab(self.hwnd, use_window_dc=False)
            name, _dist, _ = self.book.identify(buf, w, h)
            from . import anchors as _anchors

            st = _anchors.AnchorBook().state(buf, w, h, "strategy")
            rec["screen"] = name
            rec["go"] = next((a["score"] for a in st if a["name"] == "go"), None)
            pt = winio.wt.POINT()
            winio.user32.GetCursorPos(winio.ctypes.byref(pt))
            rec["cursor"] = list(winio.to_client(pt.x, pt.y, self.hwnd))
        except Exception as e:  # 日志本身不能把正事搞崩
            rec["probe_error"] = repr(e)
        try:
            with open(self.journal, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass

    # ------------------------------------------------------------ 基础

    def click(self, xy: tuple[int, int], wait: float = 0.4, note: str = "") -> None:
        self.trace.append({"kind": "click", "xy": list(xy), "note": note})
        self._note_action("click", xy=list(xy), note=note)
        if not self.dry_run:
            winio.click_client(self.hwnd, xy[0], xy[1])
        time.sleep(wait)

    def key(self, name: str, times: int = 1, wait: float = 0.3) -> None:
        self.trace.append({"kind": "key", "name": name, "times": times})
        self._note_action("key", name=name, times=times)
        if not self.dry_run:
            winio.press(name, times, 0.15)
        time.sleep(wait)

    def screen(self) -> str | None:
        w, h, buf = winio.grab(self.hwnd)
        name, dist, _ = self.book.identify(buf, w, h)
        return name

    def wait_screen(self, want: str | set[str], timeout: float = 10.0,
                    poll: float = 0.3) -> str:
        """阻塞直到界面变成 want。超时抛 TimeoutError —— 绝不静默通过。"""
        wants = {want} if isinstance(want, str) else set(want)
        t0 = time.time()
        last = None
        while time.time() - t0 < timeout:
            last = self.screen()
            if last in wants:
                return last
            time.sleep(poll)
        raise TimeoutError(
            f"等界面 {wants} 超时 {timeout}s，当前识别为 {last!r}。"
            "先用 `python -m san9.cli shot` 看看卡在哪。"
        )

    def esc_to(self, target: str, max_press: int = 6) -> bool:
        """万能逃生：连按 Esc，一旦看到目标界面就停。"""
        for _ in range(max_press):
            if self.screen() == target:
                return True
            self.key("esc", 1, 0.35)
        return self.screen() == target

    # ------------------------------------------------------------ 武将勾选

    def _rows_index(self) -> dict:
        if not os.path.exists(ROWS_PATH):
            raise NotCalibrated(
                "还没有武将列表的行号映射 config/officer_rows.json。"
                "需要先标定一次当前都市的武将列表布局。"
            )
        with open(ROWS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)

    def select_officers(self, names: list[str], wait: float = 0.3) -> None:
        rows = self._rows_index()
        for n in names:
            if n not in rows:
                raise NotCalibrated(
                    f"武将 {n!r} 不在行号映射里。当前已知：{list(rows)[:20]}"
                )
            x, y = rows[n]
            self.click((x, y), wait, note=f"勾选 {n}")

    # ------------------------------------------------------------ 跑路径

    def run_path(self, key: tuple[str, ...], ctx: dict | None = None,
                 officer_rows_hint: list[tuple[int, int]] | None = None) -> list[dict]:
        """执行一条路径。ctx 用来填 `{officers}` `{city_xy}` 这类占位符。"""
        ctx = ctx or {}
        steps = self.table.get(key)
        start = len(self.trace)
        _w0, _h0, _b0 = winio.grab(self.hwnd, use_window_dc=False)
        framesig0 = vision.frame_hash(_b0, _w0, _h0)

        for step in steps:
            kind = step.get("kind")
            wait = step.get("wait", 0.4)

            if kind == "click":
                xy = step["xy"]
                if isinstance(xy, str) and xy.startswith("{"):
                    xy = ctx.get(xy[1:-1])
                    if not xy:
                        raise NotCalibrated(
                            f"路径 {'.'.join(key)} 需要参数 {step['xy']}，但调用方没给")
                tpl = step.get("template")
                if tpl:
                    # 有模板就用模板匹配定位 —— 这样录的时候点歪一点也没关系
                    from .learn import grab_rgb

                    frame = grab_rgb(self.hwnd)
                    x, y, score = _locate_template(step, frame)
                    if x is None or score < 0.62:
                        raise ButtonNotFound(
                            f"找不到按钮「{step.get('note', tpl)}」"
                            f"（期望位置 {xy}，最佳分数 {score:.3f}）。"
                            " 说明当前画面不是这条路径预期的界面。"
                        )
                    self.click((x, y), wait, step.get("note", ""))
                else:
                    self.click(tuple(xy), wait, step.get("note", ""))
            elif kind == "menu_item":
                # 都市命令菜单的一项。**按名字定位，不按索引** ——
                # 实测子菜单项数会变（都市的「設施」子菜单只有 8 项，没有「撤除」），
                # 而且「買進/賣出」会因为没有商人而变灰。按索引点迟早点错。
                from . import citymenu as _cm

                w, h, buf = winio.grab(self.hwnd, use_window_dc=False)
                nm = step["name"]
                if isinstance(nm, str) and nm.startswith("{"):
                    nm = ctx.get(nm[1:-1])
                    if not nm:
                        raise NotCalibrated(
                            f"路径 {'.'.join(key)} 需要参数 {step['name']}，但调用方没给")
                r = _cm.locate(buf, w, h, step.get("which", "main"), nm)
                if not r.get("found"):
                    why = r.get("error") or (
                        f"分数 {r.get('score')}、落点 {r.get('xy')} 偏离预期 {r.get('expect')} "
                        f"{r.get('drift')}px")
                    raise ButtonNotFound(
                        f"菜单项「{nm}」没找到：{why}。"
                        f"子菜单的项会随目标变化（项数/是否变灰），别假设它一定在。")
                self.click(tuple(r["xy"]), wait, step.get("note", f"菜单:{nm}"))
            elif kind == "expect_change":
                # 用"画面有没有真的变"当校验 —— 点了没反应必须报错，不许当成功
                w1, h1, buf1 = winio.grab(self.hwnd, use_window_dc=False)
                d = vision.distance(vision.frame_hash(buf1, w1, h1), framesig0)
                if d < step.get("min", 0.004):
                    raise ButtonNotFound(
                        f"期望画面发生变化，但实测变化量只有 {d:.4f}（阈值 "
                        f"{step.get('min', 0.004)}）—— 说明前面某一步没生效")
            elif kind == "key":
                self.key(step["name"], step.get("times", 1), wait)
            elif kind == "wait":
                time.sleep(step.get("sec", 1.0))
            elif kind == "confirm":
                self.click(tuple(step["xy"]), wait, "確定")
            elif kind == "wait_screen":
                self.wait_screen(set(step["screen"]) if isinstance(step["screen"], list)
                                 else step["screen"], step.get("timeout", 12))
            elif kind == "esc_to":
                ok = self.esc_to(step["screen"], step.get("max_press", 6))
                if not ok:
                    raise TimeoutError(f"esc_to {step['screen']!r} 失败")
            elif kind == "rows":
                offs = ctx.get("officers") or []
                if step.get("want") == "{officers}" and offs:
                    if officer_rows_hint:
                        for x, y in officer_rows_hint[: len(offs)]:
                            self.click((x, y), wait, "勾选武将")
                    else:
                        self.select_officers(offs, wait)
            else:
                raise ValueError(f"未知步骤类型 {kind!r}")
        return self.trace[start:]

    # ------------------------------------------------------------ 宏

    def advance_turn(self, timeout: float = 45.0) -> str:
        """点「進行」并等回到**干净**战略面。

        用锚点定位按钮，不用死坐标。这一步是必须的 —— 实测踩到的坑：

        点了一个都市之后，底部弹出的部队列表**正好盖住右下角的「進行」**。
        照死坐标 (1212,863) 点下去，其实点在表格上，什么也没发生，
        而返回的却是"成功"。用锚点问一句"「進行」还在吗"，被盖住时
        匹配分从 1.000 掉到 0.314 —— 一眼就分出来了。

        另外「確認」对话框也一并处理掉（它在 turn.go 路径里）。
        """
        from . import anchors as _anchors

        steps = self.table.get(("turn", "go"))
        book = _anchors.AnchorBook()

        for st in steps:
            kind = st.get("kind")
            if kind == "click":
                # 「条件点击」：`if_template` 指定的东西真的出现了，才点这一步。
                #
                # 为什么必须有这个：turn.go 的第 2 步是「確認是」，但确认框
                # **不是每次都会弹**。原来的实现只认 click/key/wait，把这句
                # `if_template` 整个忽略了 → 没有确认框时照样点 (613,513)，
                # 而那是**地图的正中央**，很可能点开某个都市的命令菜单，
                # 于是这一旬就卡死在里面（现象：菜单开出来了，谁也回不去）。
                if st.get("if_template"):
                    from .learn import grab_rgb

                    cond = dict(st)
                    cond["template"] = st["if_template"]
                    try:
                        fx, fy, score = _locate_template(cond, grab_rgb(self.hwnd))
                    except Exception as e:
                        # 模板文件读不到 = 标定坏了，必须**大声报错**，
                        # 不能静默当成"条件不满足"跳过（那就变成永远不点确认框了）。
                        raise NotCalibrated(
                            f"`if_template` 的模板读不到：{st['if_template']} —— {e}")
                    if fx is None or score < IF_TEMPLATE_MIN:
                        self.trace.append({"kind": "skip", "why": "if_template",
                                           "template": st["if_template"],
                                           "score": round(float(score or 0.0), 3)})
                        self._note_action("skip", reason="if_template 没出现，跳过这一步",
                                          template=st["if_template"],
                                          score=round(float(score or 0.0), 3))
                        continue
                    self.click((fx, fy), st.get("wait", 0.5),
                               st.get("note", "条件按钮"))
                    continue
                xy = tuple(st["xy"])
                if st.get("anchor"):
                    w, h, buf = winio.grab(self.hwnd, use_window_dc=False)
                    hit = book.find(buf, w, h, st["anchor_screen"], st["anchor"])
                    if hit is None or not hit.found:
                        if hit is None:
                            raise ButtonNotFound(f"锚点「{st['anchor']}」没登记。")
                        why = hit.get("reject") or \
                            f"分数 {hit['score']:.3f} 低于阈值"
                        raise ButtonNotFound(
                            f"锚点「{st['anchor']}」不在：{why}。"
                            f"画面被挡住了 —— 先清干净再点，别盲点。")
                    xy = tuple(hit["xy"])
                self.click(xy, st.get("wait", 0.5), st.get("note", "進行"))
            elif kind == "key":
                self.key(st["name"], st.get("times", 1), st.get("wait", 0.3))
            elif kind == "wait":
                time.sleep(st.get("sec", 1.0))
        return self.wait_screen({"strategy"}, timeout)
