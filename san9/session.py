"""游戏会话：把一个"活的游戏"包装成 agent 能用的对象。

核心承诺
--------
**agent 的世界里只有"旬"这一个时间单位。**

调用 `end_turn()`，工具负责把期间发生的一切 —— 时间流动、战斗报告、
事件弹窗、外交使者来访、军师进言、单挑演出 —— 全部处理掉，
直到重新回到"可以下命令的战略面"。

这是"长流程能玩下来"的关键：脏活不外包给 agent。agent 只管
"这一旬我要做什么"，不用管 UI 噪音。

两个模式
--------
fair（默认）  遇到需要判断的弹窗就**停下来交给 agent**，保证不同模型面对
              完全相同的信息，benchmark 才公平。
auto          用固定规则自动应答（规则写死在 config/answers.json），
              给规则基线用。跨模型对比时必须两端一致，别混用。
"""
from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime

import numpy as np

from . import actions as A
# ⚠️ cmdscreen 必须在列表里：`_dispatch` 的 city.* 分支直接用它。
# 漏了它的话，任何 city.* 动作都会在运行时报 NameError（而不是 import 时报），
# 极难发现 —— 曾经就是这样：过旬能跑，一下命令就崩。
from . import (anchors, boot, cmdscreen, driver, lexicon, ocr, paths,
               savefile, vision, winio)

ROOT = paths.PROJECT
STATE_DIR = paths.DATA
SNAP_DIR = paths.SNAPS
RUNS_DIR = paths.RUNS
POLICY_PATH = os.path.join(ROOT, "config", "screen_policy.json")
ANSWERS_PATH = os.path.join(ROOT, "config", "answers.json")

# ---------------------------------------------------------------- 弹窗判定
#
# ⛔ 绝不用"弹窗长什么样"做判据（边框花纹模板匹配试过：真弹窗 0.866、
#    战略面误报 0.799~0.829，分差只有 0.037，纯靠硬卡阈值，还误报卡死过）。
#    弹窗类型无穷，一种新弹窗就得重新调参。
#
# ✅ 用一个**免费的无图像判据**：按一下 Esc，看画面变不变。
#    · 普通消息框（角色台词之类）→ Esc 一按就关，帧差大
#    · **有选项的弹窗 → Esc 完全无效**（实测「兵法修行」连按 4 次纹丝不动）
#    所以"Esc 无效"本身就说明它是需要拍板的东西 —— 而且这个判据
#    天然不会替 agent 做决定：只有确认 Esc 无效之后我们才去查规则。
ESC_NO_CHANGE = 0.5

PERFORM_TOPBAR = (60, 8, 1010, 52)
"""判"在不在过旬演出"时读的顶栏区域（避开右侧按钮）。"""

PERFORM_MARKS = ("金", "兵", "信")
"""正常战略面顶栏一定有这三种字（資金/兵糧/信望）。演出帧的顶栏是进度条 +「靜止」，
一个都读不到 → 判为演出中。用单字而不是整词，是因为这套字体下整词几乎必然读错。"""
"""按 Esc 前后帧差 <= 此值 = 画面纹丝不动 = Esc 无效 = "有选项的弹窗"。"""

DIALOG_WATCH = (370, 290, 910, 620)
"""中央"亮底对话框"的观察窗（实测确认框正好落在这块里）。"""

HOVER_PARK = (640, 945)
"""处理遮挡前先把鼠标挪到这儿（底部提示栏，悬停它不会弹任何面板）。

⚠️ 为什么必须做这件事：鼠标停在都市上会弹出「都市情報」面板（说明书 P.9
「將游標置於都市上即可檢視」），**它盖住画面而且 Esc 关不掉** —— Esc 关的是弹窗，
不是悬停。实测长跑里连续两次 stall 都是它（OCR 出「南皮…張角…耐久…士兵」）。
把鼠标挪开是免费的，而且能一次消掉这一整类假遮挡。
"""

DIALOG_BRIGHT = 170
"""算"亮像素"的灰度阈值。亮底对话框是米白色，地图是暗绿/暗蓝。"""

DIALOG_MIN_BLOB = 10000
DIALOG_W_RANGE = (300, 600)
DIALOG_H_RANGE = (150, 320)
DIALOG_FILL_RANGE = (0.25, 0.85)
"""观察窗里最大连通亮块要满足这四条，才算"中央盖着一块亮底对话框"。

| 画面 | area | 外框 | fill |
|---|---|---|---|
| 过旬确认框 | 51004 | 540x184 | 0.51 |
| 修行事件弹窗 | 62033 | 428x213 | 0.68 |
| 干净战略面 | 1807 | 132x**29** | 0.47 |
| 命令菜单打开 | 1074 | 540x**56** | 0.04 |
| 命令执行界面 | 1297 | 540x**27** | 0.09 |
| 过旬演出 | 196 | 41x**8** | 0.60 |
| **合战演出（齊射）** | **77** | 16x14 | 0.34 |

⭐ **起决定作用的是"高度 >= 150"**：所有非对话框的外框高都 < 60，而对话框是 184 / 213。
只按面积会漏掉合战里"齊射"白闪那几帧（整片中心被打亮）——
fill 上限 0.85 是给那种纯白闪留的第二道闸。
"""

DIALOG_REGIONS = [(415, 325, 875, 595),       # 弹窗本体（实测覆盖整个框，含最后一行）
                  (300, 420, 1020, 790),      # 偏下、更长的文本
                  (280, 140, 1050, 800)]      # 兜底：大范围（会读进地图文字，只兜底用）
"""OCR 弹窗文字的区域，**从紧到松**。

⚠️ 区域必须**把整个框包住**：实测「修行」弹窗有三行字，关键词「修行」
在第一行和第三行，而当时用的框只盖到第二行 → OCR 读到「…就能學得步兵」，
关键词一个都没命中，判定"认不出"。所以别抠紧，宁可多带点边。
"""


def _frame_diff(a, b) -> float:
    """两张帧的平均绝对差。和 vision.distance 同一口径，包一层防止 None。"""
    if a is None or b is None:
        return 0.0
    try:
        return float(vision.distance(a, b))
    except Exception:
        return 0.0


# 界面角色。标定出来的界面名要归到这几类里。
DEFAULT_POLICY = {
    "dialog": "ask",          # 事件弹窗：必须停下来交给 agent 决策
    "blocked": "ask",         # 战略面被遮挡（弹窗或面板）：不猜，交出去
    "strategy": "ready",
    "city_menu": "ready",
    "progress": "busy",
    "battle_report": "busy",
    "event": "ask",
    "envoy": "ask",
    "advisor": "ask",
    "duel": "busy",
    "info": "menu",
    "save_menu": "menu",
    "main_menu": "menu",
}


class GameSession:
    def __init__(self, mode: str = "fair", run_id: str | None = None,
                 dry_run: bool = False):
        self.mode = mode
        self.dry_run = dry_run
        self.hwnd: int | None = None
        self.run_id = run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
        self.run_dir = os.path.join(RUNS_DIR, self.run_id)
        paths.ensure()
        os.makedirs(self.run_dir, exist_ok=True)

        self.table = driver.PathTable()
        self.book = vision.ScreenBook()
        self.anchors = anchors.AnchorBook()
        self.policy = self._load(POLICY_PATH, DEFAULT_POLICY)
        self.answers = self._load(ANSWERS_PATH, {})
        self.drv: driver.Driver | None = None

        self.turn_index = 0  # 从开局算起的旬数
        # 「進行」消失多久才当成"在等决策"。过旬演出几秒就过，
        # 弹窗会一直停着 —— 用时间长短区分这两件事，不需要认识弹窗类型。
        self.blocked_grace = 14.0
        self.perform_grace = 150.0
        """「过旬演出」允许的最长时长。超了才判停（动画关掉后演出仍是静止帧）。"""
        # 画面停住多久之后，开始用 Esc 试探（甩掉游戏自己弹出来的都市菜单）
        self.escalate_after = 3.0
        # 画面"在动"的阈值（24x18 粗糙指纹的平均差）。水面波光这类小动画
        # 远低于它，过旬演出远高于它 —— 两头都有很大余量。
        self.motion_eps = 0.004
        self.last_dialog: dict | None = None
        self.pending: list[dict] = []
        self.events: list[dict] = []
        # 卡住时的留证截图，一张都不能丢 —— 事后复盘全靠它
        self.stall_shots: list[str] = []
        self._obs_log = open(os.path.join(self.run_dir, "obs.jsonl"), "a", encoding="utf-8")
        self._act_log = open(os.path.join(self.run_dir, "actions.jsonl"), "a", encoding="utf-8")

    @staticmethod
    def _load(path: str, default):
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default, f, ensure_ascii=False, indent=2)
        return dict(default)

    # ------------------------------------------------------------ 生命周期

    def attach(self) -> bool:
        w = winio.find_game("San9WPK.exe")
        if w is None:
            self.hwnd = None
            self.drv = None
            return False
        self.hwnd = w.hwnd
        self.drv = driver.Driver(
            self.hwnd, self.table, self.book, self.dry_run,
            journal=os.path.join(self.run_dir, "input.jsonl"),
            label=os.path.basename(self.run_dir))
        return True

    def launch(self, to_main_menu: bool = True) -> bool:
        """走启动器进游戏。注意：直接跑 San9WPK.exe 会立刻退出。"""
        hwnd = boot.launch()
        if not hwnd:
            return False
        self.attach()
        if to_main_menu:
            self.click_launcher_game()
            self.attach()
        return self.hwnd is not None

    @staticmethod
    def click_launcher_game() -> bool:
        """启动器上的 GAME 按钮是标准 Win32 按钮，直接发 BM_CLICK 比点坐标稳。"""
        import ctypes
        import ctypes.wintypes as wt

        u = winio.user32
        lp = None
        for w in winio.list_windows():
            if "LAUNCHER" in w.cls:
                lp = w.hwnd
                break
        if lp is None:
            return False
        found = []

        @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        def cb(h, _):
            n = u.GetWindowTextLengthW(h)
            t = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(h, t, n + 1)
            if t.value.upper() == "GAME":
                found.append(h)
            return True

        u.EnumChildWindows(lp, cb, 0)
        if not found:
            return False
        u.SendMessageW(found[0], 0x00F5, 0, 0)  # BM_CLICK
        time.sleep(6)
        return True

    def focus(self) -> bool:
        if self.hwnd is None:
            return False
        return winio.focus(self.hwnd)

    # ------------------------------------------------------------ 观测

    def view(self) -> dict:
        """一次抓屏，同时给出三件事：界面名、**够不够通畅**、锚点详情。

        为什么需要第 2 项
        -----------------
        实战踩到的：点了一个都市之后，底部会弹出部队列表，**把右下角的
        「進行」按钮整个盖住**。这时候界面指纹仍然说"还在战略面"（距离 0.0008），
        于是 `end_turn` 照原样去点 (1212,863) —— 点在表格上，什么也没发生，
        却返回 `advanced: 0` 让人以为推进了。

        界面指纹回答的是"这是哪个画面"，回答不了"这个画面上我预期的东西
        还在不在"。所以用锚点（`san9/anchors.py`）单独问一次。

        `clear=False` = "有东西挡在路上" —— 弹窗、菜单、面板都算。
        **不需要认识是哪种弹窗**，这是这个判据最值钱的地方。
        """
        if self.hwnd is None:
            return {"screen": None, "distance": 1.0, "clear": False,
                    "anchors": [], "size": [0, 0]}
        w, h, buf = winio.grab(self.hwnd, use_window_dc=False)
        # 先问锚点"在不在战略面"。为什么不信指纹：指纹里含地图区域，
        # 地图一滚、季节一换就失效（实测同是战略面，换到南皮的夏天从
        # 0.008 跳到 0.166 直接认不出）。顶栏按钮是钉死的，跟地图无关。
        name = self.anchors.identify(buf, w, h, "strategy")
        clear, state = (True, [])
        dist = 0.0
        if name:
            clear, state = self.anchors.is_clear(buf, w, h, name)
        else:
            # 不是战略面 —— 退回到指纹，看看是不是别的已知画面
            name, dist, _ = self.book.identify(buf, w, h)
            if name:
                clear, state = self.anchors.is_clear(buf, w, h, name)

        # ⚠️ **锚点不够用**：确认框在画面**正中央**，盖不住右下角的「進行」——
        # 实测「將結束黃巾軍的戰略面，您確定嗎？」弹出来时 go 分数还有 0.91。
        # 只看锚点会把"停在确认框上"当成"干净战略面"，于是 `_handle_blockage`
        # 永不触发，而后面所有点击都被模态框吃掉（实测帧差 0.0），整局僵死。
        #
        # 判据用**中央区的亮底面积占比**：亮底对话框是米白色（亮），
        # 地图是暗绿/暗蓝。实测 有框 0.389 vs 地图 0.010 / 顶栏 0.061 —— 6 倍余量。
        arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)
        gray = (arr[:, :, 0] * 0.114 + arr[:, :, 1] * 0.587
                + arr[:, :, 2] * 0.299)
        x0, y0, x1, y1 = DIALOG_WATCH
        win = (gray[y0:y1, x0:x1] > DIALOG_BRIGHT).astype(np.uint8)
        bright_frac = float(win.mean())
        blob = bw = bh = 0
        fill = 0.0
        if win.any():
            import cv2

            _n, _lab, st, _ce = cv2.connectedComponentsWithStats(
                np.ascontiguousarray(win), 8)
            i = max(range(1, max(_n, 2)), key=lambda k: st[k][4]) if _n > 1 else 0
            if i:
                blob = int(st[i][4])
                bw, bh = int(st[i][2]), int(st[i][3])
                fill = blob / max(1, bw * bh)
        dialog = bool(
            blob >= DIALOG_MIN_BLOB
            and DIALOG_W_RANGE[0] <= bw <= DIALOG_W_RANGE[1]
            and DIALOG_H_RANGE[0] <= bh <= DIALOG_H_RANGE[1]
            and DIALOG_FILL_RANGE[0] <= fill <= DIALOG_FILL_RANGE[1])

        return {"screen": name, "distance": round(dist, 4),
                "clear": bool(clear) and not dialog,
                "anchors": state, "size": [w, h],
                "dialog": dialog, "dialog_blob": blob,
                "dialog_box": [bw, bh], "dialog_fill": round(fill, 2),
                "center_bright": round(bright_frac, 3),
                "frame": vision.frame_hash(buf, w, h)}

    def screen(self) -> str | None:
        """当前界面名。被遮挡时返回 "blocked"。

        优先级：**先用锚点判"通不通"，再谈"是哪个画面"**。
        因为一个被弹窗盖住的战略面，指纹上是战略面，实际什么也做不了 ——
        按界面名走流程会一路错下去（会把事件整个跳过去）。
        """
        v = self.view()
        if v["screen"] and not v["clear"]:
            self.last_dialog = {"anchors": [dict(a) for a in v["anchors"]]}
            return "blocked"
        self.last_dialog = None
        return v["screen"]

    def _is_clear(self) -> bool:
        """当前是不是"能下命令的干净战略面"。"""
        v = self.view()
        return v["screen"] == "strategy" and bool(v["clear"])

    def clear_blockage(self, max_tries: int = 2, key: str = "esc",
                       label: str = "clear_blockage",
                       use_menu: bool = True) -> bool:
        """把挡在战略面上的东西按掉。**阶梯式**，返回是否真的清干净了。

        ⚠️ **只有在"确定没有未决问题"时才允许调用。** 对一个有选项的决策弹窗按
        Esc，等价于替 agent 做了决定（通常等于"否"）—— 那是最坏的错误类型：
        静默地把决策做掉，agent 还以为没发生过。

        阶梯（实测踩出来的顺序）：
          1. **Esc 一次** —— 普通消息框（如角色台词）关得掉；
             有选项的弹窗 Esc 完全无效（实测「兵法修行」连按 4 次帧差极小）
          2. **点地图空白处** —— ❗**Esc 关不掉都市命令菜单**（连按 4 次帧差 0.5），
             只有点空白才关得掉（帧差 ≈ 24.9）。这一步交给 `cmdscreen.close_menu`，
             它内部会逐点试并**每一步都验 `go` 锚点**，不验就等于"以为关掉了其实没有"
          3. 再补两次 Esc —— 兜住"点了空白反而开了别的东西"的情况
        """
        if self.drv is None:
            return False
        self.drv.label = label
        if self._is_clear():
            return True

        self.drv.key(key, 1, 0.5)
        if self._is_clear():
            return True

        if use_menu:
            try:
                if cmdscreen.close_menu(self.hwnd):
                    return True
            except Exception as e:          # 清障碍不能把整局带崩
                self.events.append({"type": "clear_blockage_error",
                                    "label": label, "error": str(e)})

        for _ in range(max_tries):
            self.drv.key(key, 1, 0.5)
            if self._is_clear():
                return True
        return False

    # ------------------------------------------------------------ 混合弹窗应答

    @staticmethod
    def _go_score(v: dict) -> float | None:
        return next((a["score"] for a in (v.get("anchors") or [])
                     if a["name"] == "go"), None)

    def _handle_blockage(self, where: str, allow_answer: bool = True) -> dict:
        """战略面被挡住时的**统一**处理。返回 {'cleared': bool, 'kind': ...}。

        这是本轮新增的核心：原来两处（旬前清理 / 吞事件循环）各写一套，
        而且只会按 Esc —— 碰到"有选项的弹窗"必然停住，长流程就断在这儿。

        三段式：
          1. **Esc 试一次** → 普通消息框关得掉，直接过
          2. **Esc 无效**（帧差 <= ESC_NO_CHANGE）→ 判定为有选项的弹窗 →
             OCR 文字 → 查 `config/answers.json` 规则 → 答 → **验证真的过去了**
          3. **规则没命中** → 不停下了事：把弹窗整屏图 + OCR 文字**沉淀进待校验区**，
             然后才 `cleared=False` 交给 agent

        `allow_answer=False`（fair 模式）时只做第 1 步，绝不替 agent 拍板。
        """
        v0 = self.view()
        if v0["screen"] == "strategy" and v0["clear"]:
            return {"cleared": True, "kind": "already_clear"}
        before = v0["frame"]

        # ---- 0) 先把鼠标挪开
        #
        # 鼠标停在都市/部队上会弹出信息面板，**盖住画面而且 Esc 关不掉**
        # （Esc 关的是弹窗，不是悬停）。实测长跑里连续两次 stall 都是这个。
        # 挪鼠标是免费的，先做 —— 这一条能消掉一整类假遮挡。
        try:
            winio.move_cursor_input(*HOVER_PARK)
            time.sleep(0.35)
            v0b = self.view()
            if v0b["screen"] == "strategy" and v0b["clear"]:
                return {"cleared": True, "kind": "unhover"}
            v0 = v0b
            before = v0["frame"]
        except Exception as e:
            self.events.append({"type": "unhover_error", "error": str(e)})

        # ---- 0.5) 分清"游戏自己在忙"和"在等我拍板"
        #
        # 过旬演出、行军、战报期间「進行」本来就会消失，此时画面**在动**。
        # 这时候按 Esc 是纯干扰（实测会把"过旬演出"误判成"有选项的弹窗"）。
        #
        # ⚠️ 但**"画面在动"不能压过"中央有对话框"**：实测「修行」事件弹窗
        # 弹出时，地图上正好有一支部队在行军，画面一直在动 —— 结果被当成
        # "游戏在忙"，既不按 Esc 也不应答，白等 15 秒就停了。
        # 所以先看有没有亮底对话框；**有对话框就优先处理，不管地图动没动**。
        if not v0.get("dialog") and self._moving():
            return {"cleared": False, "kind": "busy",
                    "why": "画面在动且中央没有对话框 —— 游戏自己在跑"}

        # ---- 1) Esc 试一下
        self.drv.label = f"{where}/esc"
        self.drv.key("esc", 1, 0.5)
        v1 = self.view()
        if v1["screen"] == "strategy" and v1["clear"]:
            return {"cleared": True, "kind": "esc"}
        esc_diff = _frame_diff(v1["frame"], before)

        # ---- 2) Esc 无效 = 有选项的弹窗
        if esc_diff <= ESC_NO_CHANGE:
            if not allow_answer:
                return {"cleared": False, "kind": "choice_dialog",
                        "esc_diff": esc_diff,
                        "why": "识别为有选项的弹窗，但当前模式不替 agent 拍板"}
            r = self._answer_choice_dialog(where)
            if r.get("ok"):
                # ⚠️ `r` 里也有 `cleared` 键（那是"答完之后战略面干净了吗"），
                # 直接 `{**r}` 会把它盖掉 —— 曾经就是这样：答成功了却报 False，
                # 上层以为没清干净就停了一旬。所以**显式写在最后**。
                out = {"kind": r.get("kind", "answered"), **r}
                out["cleared"] = True
                return out
            # 认不出 → 不当成"清不掉的失败"，而是"看不懂就等"。
            # 依据（用户实测）：**过旬進行中不需要任何操作**，所以看不懂的正确反应
            # 是继续等它自己走完，而不是把这一旬判死。演出/合战会自己结束。
            return {"cleared": False, "kind": "dialog_unrecognized", **r}

        # ---- 3) Esc 有效果但没清干净 → 走"点空白地图"那条阶梯
        if self.clear_blockage(max_tries=2, label=f"{where}/cleanup"):
            return {"cleared": True, "kind": "esc_then_menu", "esc_diff": esc_diff}
        return {"cleared": False, "kind": "still_blocked", "esc_diff": esc_diff}

    def _moving(self, gap: float = 0.5) -> bool:
        """画面是不是在动 = 游戏自己在跑（过旬演出/行军/战报）。

        用和吞事件循环同一个判据（`motion_eps`），别另立一套。
        """
        try:
            w, h, buf = winio.grab(self.hwnd, use_window_dc=False)
            f1 = vision.frame_hash(buf, w, h)
            time.sleep(gap)
            w, h, buf = winio.grab(self.hwnd, use_window_dc=False)
            f2 = vision.frame_hash(buf, w, h)
            return float(vision.distance(f2, f1)) > self.motion_eps
        except Exception:
            return False

    def wait_until_clear(self, timeout: float = 45.0, where: str = "wait") -> bool:
        """等回"能下命令的干净战略面"。**忙的时候不瞎动手，只等。**

        policy / play 的收尾都走这个：游戏自己在跑的时候按 Esc / 点地图
        只会添乱（实测把过旬演出误判成弹窗）。
        """
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self._is_clear():
                return True
            if self._moving():
                continue
            if self.ensure_clear(where):
                return True
        return self._is_clear()

    def _match_answer_rule(self, text: str) -> dict | None:
        """`answers.json` 的 `rules`：`any` 里任一关键词出现在弹窗文字里就算命中。"""
        for r in (self.answers.get("rules") or []):
            for kw in (r.get("any") or []):
                if kw and kw in text:
                    return r
        return None

    def _answer_choice_dialog(self, where: str) -> dict:
        """对"Esc 关不掉"的弹窗：OCR → 查规则 → 答 → **验证**。

        验证是硬要求：`answer()` 只保证"键按下去了"，不保证游戏接受了。
        所以答完必须看到 `go` 锚点回来或画面变了才返回 ok，否则如实报失败。
        """
        text = ""
        for reg in DIALOG_REGIONS:
            try:
                items = ocr.read_screen(self.hwnd, reg, upscale=4)
            except Exception:
                items = []
            t = "".join(i["text"] for i in items).strip()
            if len(t) > len(text):
                text = t
            # 第一条能命中规则的就不用再往下读（省时间）；读满 24 字也够判断了
            if self._match_answer_rule(text) or len(text) >= 24:
                break
        fixed, conf, _how = lexicon.correct(text) if text else ("", 0.0, "")

        rule = self._match_answer_rule(text)
        shot = self.shot(f"dialog_{where.split('/')[-1]}")

        if rule is None:
            self.events.append({"type": "dialog_unknown", "where": where,
                                "ocr": text, "ocr_fixed": fixed, "shot": shot})
            self._file_dialog_finding(where, text, shot)
            return {"ok": False, "kind": "unknown_dialog", "ocr": text,
                    "ocr_fixed": fixed, "shot": shot,
                    "why": "弹窗认不出（规则没命中）—— 已沉淀进待校验区，交给 agent"}

        choice = int(rule.get("choice", 0))
        v_before = self.view()
        self.drv.label = f"{where}/answer#{choice}"
        self.answer(choice)                      # 键盘 Right×N + Enter
        time.sleep(1.5)
        v_after = self.view()
        moved = _frame_diff(v_after["frame"], v_before["frame"]) > 0.004
        cleared = v_after["screen"] == "strategy" and v_after["clear"]
        rec = {"type": "dialog_answer", "where": where, "ocr": text,
               "rule": rule.get("id"), "choice": choice,
               "verified": bool(cleared or moved),
               "go_before": self._go_score(v_before),
               "go_after": self._go_score(v_after), "shot": shot}
        self.events.append(rec)

        if not (cleared or moved):
            return {"ok": False, "kind": "answer_no_effect", "ocr": text,
                    "choice": choice, "rule": rule.get("id"), "shot": shot,
                    "why": "选项答了但画面纹丝不动 —— 不假装成功"}
        return {"ok": True, "kind": "answered", "ocr": text, "choice": choice,
                "rule": rule.get("id"), "cleared": bool(cleared), "shot": shot}

    def _file_dialog_finding(self, where: str, text: str, shot: str) -> None:
        # 同一条弹窗文字只沉淀一次（合战演出一旬能刷出 16 次同样的 OCR 结果）
        key = (text or "")[:40]
        if not hasattr(self, "_filed_dialogs"):
            self._filed_dialogs: set = set()
        if key in self._filed_dialogs:
            return
        self._filed_dialogs.add(key)
        """认不出的弹窗 → 自动沉淀进待校验区。

        **第一次停下是探索，第二次就认识了。** 卡住这件事本身也是产出，
        不能白停 —— 这是用户明确要求的（"卡点要探索，不能一点进展都没有"）。
        """
        try:
            from . import findings

            d = os.path.join(findings.ROOT, "dialogs")
            os.makedirs(d, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            keep = os.path.join(d, f"{stamp}.png")
            if shot and os.path.exists(shot):
                shutil.copy2(shot, keep)
            findings.add(
                claim=f"遇到一个认不出的弹窗（{where}）：OCR 到的文字是「{text[:40]}」",
                method="按 Esc 帧差 <= 0.5 判定为「有选项的弹窗」；再整屏 OCR 取文字；"
                       "整屏图已存 dialogs/",
                area="dialog",
                evidence={"shot": keep, "ocr": text, "where": where},
                status="unverified",
                review="待复核：这是哪一类弹窗？有几个选项、各自什么意思、"
                       "默认该选哪个（选错会不会破坏局面）")
            self.events.append({"type": "dialog_filed", "issue": keep})
        except Exception as e:              # 沉淀失败不能影响主流程
            self.events.append({"type": "finding_error", "error": str(e)})

    def role(self, screen: str | None) -> str:
        if screen is None:
            return "unknown"
        return self.policy.get(screen, "unknown")

    def ensure_clear(self, where: str = "ensure", allow_answer: bool = True) -> bool:
        """公开入口：把画面弄回"能下命令的干净战略面"，返回是否成功。

        policy / play 这类外部调用方用这个，不要直接碰 `_handle_blockage`。
        """
        if self._is_clear():
            return True
        try:
            return bool(self._handle_blockage(where,
                                             allow_answer=allow_answer)["cleared"])
        except Exception as e:
            self.events.append({"type": "ensure_clear_error", "where": where,
                                "error": str(e)})
            return False

    def shot(self, tag: str = "obs") -> str:
        paths.ensure()
        w, h, buf = winio.grab(self.hwnd, use_window_dc=False)
        name = f"{self.run_id}_{self.turn_index:05d}_{tag}.png"
        return winio.save_png(os.path.join(paths.SHOTS, name), w, h, buf)

    def observe(self, scope: str = "brief", with_shot: bool = False) -> dict:
        """取一次观测。

        scope:
          brief  默认。摘要 + 当前界面 + 待处理事件。长流程里绝大多数时候用这个。
          full   全量。开局和每 N 旬重新规划时用。

        诚实说明：现在 `world` 字段来自存档块表，**只有块级摘要**，
        都市/武将的字段级解析还没做完（Phase 2）。agent 应该看
        `completeness` 字段决定信任程度。
        """
        scr = self.screen()
        obs: dict = {
            "turn": {
                "index": self.turn_index,
                "phase": self.role(scr),
                "mode": self.mode,
            },
            "view": {
                "screen": scr,
                "role": self.role(scr),
            },
            "pending": list(self.pending),
            "recent_events": self.events[-8:],
            "completeness": "partial",
            "sources": ["screen", "save.chunks"],
        }

        obs["world"] = self._world_summary(scope)
        if with_shot:
            obs["view"]["shot"] = self.shot("obs")
        if not self.pending and self.events and scope == "brief":
            obs["recent_events"] = self.events[-5:]
        self._obs_log.write(json.dumps(obs, ensure_ascii=False) + "\n")
        self._obs_log.flush()
        return obs

    def _world_summary(self, scope: str) -> dict:
        try:
            saves = savefile.list_saves()
            if not saves:
                return {"error": "没有存档"}
            main = saves.get("D_Sav000.S9") or sorted(saves.values())[0]
            sf = savefile.SaveFile(main)
            out = {
                "save": os.path.basename(main),
                "chunks": {c.cid: c.size for c in sf.chunks},
                "note": "块级摘要；字段级解析待 Phase 2",
            }
            if scope == "full":
                out["map"] = sf.dump_map()
            return out
        except Exception as e:
            return {"error": str(e)}

    # ------------------------------------------------------------ 回合推进

    def end_turn(self, n: int = 1, auto_answer: bool | None = None) -> dict:
        """过 n 旬。这是长流程的心脏。

        返回 {"advanced": k, "stopped_at": <界面名或 None>, "question": {...}}
        stopped_at 非空表示需要 agent 决策，处理完再调一次即可。
        """
        auto = (self.mode == "auto") if auto_answer is None else auto_answer
        advanced = 0
        for _ in range(max(1, n)):
            r = self._one_turn(auto)
            if r.get("stopped_at"):
                r["advanced"] = advanced
                return r
            advanced += 1
            self.turn_index += 1
            self.snapshot()
        return {"advanced": advanced, "stopped_at": None, "turn": self.turn_index}

    def _blocked_question(self, why: str, v: dict) -> dict:
        """战略面被遮挡 → 交出去。带上锚点详情，让 agent 知道是哪个按钮不见了。"""
        q = {
            "kind": "blocked",
            "why": why,
            "screen": v.get("screen"),
            "anchors": [dict(a) for a in (v.get("anchors") or [])],
            "turn": self.turn_index,
            "shot": self.shot("blocked"),
            "hint": ("画面被挡住了，看不到「進行」。"
                     "如果是残留下来的面板/菜单，用 game.dismiss 关掉再重试；"
                     "如果是游戏事件，用 game.answer 选一个选项。"),
        }
        self.pending.append(q)
        return {"stopped_at": "blocked", "question": q, "reason": why,
                "advanced": None}

    def _one_turn(self, auto: bool) -> dict:
        assert self.drv is not None, "还没 attach 到游戏"

        # 1. 回到"干净战略面"。
        #
        #    关键区分：
        #      pending 为空 **且** 指纹认出这就是战略面（只是被面板盖住）
        #                     → 这个遮挡是我自己留下的（点过都市弹出来的部队
        #                       列表盖住了「進行」）→ 按 Esc 清掉，自愈
        #      其它情况（指纹认不出 / 有未决问题）
        #                     → **不盲动**，交给 agent
        #
        #    为什么"认不出就交出去"是安全的：实测这个游戏的选择弹窗（是/否）
        #    连按 4 次 Esc 都不会消失，必须用 Enter 答；而普通消息框 Esc 能关。
        #    所以"Esc 清得掉"本身就说明它是面板/消息框，不是我该替 agent 决策的东西。
        #    ⚠️ 补一条实测教训：刚答完一个弹窗时，游戏会进入**过旬演出**
        #    （画面在动、进度条在走），这是"它自己在跑"，不是"被挡住"。
        #    所以遇到 busy 要**等**，不能当遮挡处理 —— 否则每答一次弹窗就白停一旬。
        t_clean = time.time()
        while True:
            v = self.view()
            if v["screen"] == "strategy" and v["clear"]:
                break
            if self.pending:
                return self._blocked_question("上一轮的问题还没回答，不能盲动", v)
            if v["screen"] != "strategy":
                return self._blocked_question(
                    f"不在战略面上（识别为 {v['screen'] or '未知'}），不猜着按 Esc", v)
            # 战略面被盖住：走统一的遮挡处理（Esc → 认弹窗 → 答/沉淀）
            r = self._handle_blockage("end_turn/step1", allow_answer=auto)
            if r.get("cleared"):
                break
            if r.get("kind") == "busy" and time.time() - t_clean < 90:
                time.sleep(1.0)
                continue
            if not r.get("cleared"):
                return self._blocked_question(
                    f"战略面被遮挡，清不掉（{r.get('kind')}）："
                    f"{r.get('why') or r.get('ocr') or ''}", self.view())

        # 2. 点「進行」
        #    注意：ButtonNotFound 必须被捕获并转成"停下来问"，不能让它冒出去 ——
        #    否则一次锚点找不到就把整局崩掉。而且要**如实报告**没点成，
        #    绝不"点了但失败却返回成功"。
        self.drv.label = "end_turn/press-go"
        try:
            self.drv.advance_turn()
        except TimeoutError:
            pass
        except driver.ButtonNotFound as e:
            return self._blocked_question(f"点不到「進行」：{e}", self.view())
        except driver.NotCalibrated as e:
            raise SystemExit(str(e))
        self.drv.label = "end_turn/absorb"

        # 3. 吞掉期间发生的一切，直到重新回到**能下命令的**战略面
        #
        #    这一步的核心判据只有两条，都不需要认识任何画面：
        #      · 能下命令 = 认得战略面 **且** 锚点齐全（「進行」在）
        #      · 该不该停下 = 画面**停住不动**多久了
        #
        #    为什么要"停住多久"这一条：过旬演出、行軍、战斗报告期间，
        #    「進行」本来就会被进度条顶掉，此时"看不到進行"完全正常 ——
        #    那时候画面在动。真正等我们拍板的弹窗会让画面纹丝不动。
        #    所以不能用"有没有進行"直接判定"需要决策"，会每隔一旬就误停。
        t0 = time.time()
        prev_frame = None
        still_since = time.time()
        last_esc = 0.0
        failures = 0          # 遮挡处理连续失败几次了
        perform_hits = 0      # 顶栏连续几次看起来像"演出"
        perform_since = None  # 演出开始时间（用来设上限，避免永远等下去）
        while time.time() - t0 < 300:
            v = self.view()
            s = v["screen"]
            if s == "strategy" and v["clear"]:
                return {"stopped_at": None}

            if prev_frame is not None and \
                    vision.distance(v["frame"], prev_frame) > self.motion_eps:
                still_since = time.time()  # 画面在动 → 游戏自己在跑，继续等
            prev_frame = v["frame"]

            # 画面停住了一会儿，而且「進行」不在 —— 先按一次 Esc 探一下。
            #
            # 为什么这里按 Esc 是安全的：实测这个游戏里
            #   · 普通消息框（如张角回应）→ Esc 关得掉
            #   · **有选项的弹窗（是/否）→ Esc 完全无效，必须用 Enter 答**
            #     （实测「兵法修行」弹窗连按 4 次 Esc 纹丝不动）
            # 所以"Esc 清得掉"这件事本身，就把"该我清掉的残留"和
            # "必须交给 agent 拍板的决策"分开了 —— 而且绝不会替 agent 做决定。
            #
            # ⚠️ 尚未验证：如果这里是「都市命令菜单」（点都市会打开它，
            # 而它会盖住「進行」），Esc 应该也能关掉。**要实测确认，别假设。**
            still = time.time() - still_since

            # ---- 「过旬演出」判据：必须先判这个，再判"是不是卡住"。
            #
            # 用户关掉动画后，演出帧**完全静止**（不再是"画面在动"），
            # 于是"停住 14 秒 = 在等我决策"被误触发 —— 长跑在第 0 旬就停。
            # 正确反应是**等**：游戏自己在演，期间不需要任何操作。
            # 要求连续两次命中才认，避免单次 OCR 抖动误判。
            if self._topbar_looks_like_perform():
                perform_hits += 1
            else:
                perform_hits = 0
            if perform_hits >= 3:
                if perform_since is None:
                    perform_since = time.time()
                    self.events.append({"type": "perform",
                                        "turn": self.turn_index})
                still_since = time.time()
                if time.time() - perform_since > self.perform_grace:
                    return self._blocked_question(
                        f"演出画面持续超过 {self.perform_grace:.0f} 秒还没结束"
                        f"（顶栏一直读不到 信望/資金/兵糧）", v)
                time.sleep(0.6)
                continue
            perform_since = None

            if still > self.escalate_after and time.time() - last_esc > 2.5:
                last_esc = time.time()
                # **卡住时先留证，再动手。**
                # 这是吃了教训：我曾经只看一张事后截图，就断言"这是游戏每旬
                # 自动弹出来的都市菜单"，结论是错的。现在凡是异常一律先存图，
                # 事后对着图和输入日志一起看 —— 不许靠推断。
                path = self.shot("stall")
                self.events.append({"type": "stall", "turn": self.turn_index,
                                    "still": round(still, 1), "screen": s,
                                    "go": next((a["score"] for a in v["anchors"]
                                                if a["name"] == "go"), None),
                                    "shot": path})
                self.stall_shots.append(path)
                r = self._handle_blockage("end_turn/absorb", allow_answer=auto)
                # ⚠️ **每次处理完都把 still 归零**：处理一次要 OCR（好几秒），
                # 不归零的话 still 会直接冲过 blocked_grace 把这一旬判死 ——
                # 实测「修行」弹窗就是这样：正 OCR 的时候被判了"停住 15 秒"。
                still_since = time.time()
                if r.get("cleared"):
                    continue
                if r.get("kind") in ("busy", "dialog_unrecognized"):
                    # ⚠️ **"游戏自己在跑"和"看不懂的画面"都不算失败。**
                    # 过旬演出/行军/合战持续时间不定，看不懂时正确反应是继续等 ——
                    # 用户实测确认：过旬進行中不需要任何操作。
                    # （曾经把 busy 算成失败：连续 3 次就把整局判死，
                    #   现象是"停在过旬演出上"，看起来毫无道理。
                    #   合战演出更狠：一旬能刷出 16 次"认不出的弹窗"。）
                    continue
                failures += 1
                if failures <= 2:
                    continue              # 给两三次机会，别一次慢 OCR 就收摊

            if s and self.role(s) == "ask":
                q = {"screen": s, "turn": self.turn_index, "shot": self.shot("ask")}
                if not auto:
                    self.pending.append(q)
                    return {"stopped_at": s, "question": q, "reason": "需要决策"}
                self._auto_answer(s)
                self.events.append({"type": "auto_answer", "screen": s,
                                    "turn": self.turn_index})
                still_since = time.time()
                continue

            # 判"该不该停下"用**连续失败次数**，不用"停住多久"。
            # 因为每次处理遮挡都会把 still 归零（处理本身要好几秒），
            # 用时间判会永远够不到阈值；而"连着三次都搞不定"是可靠的信号。
            if failures > 2:
                return self._blocked_question(
                    f"连着 {failures} 次没能清掉遮挡，回不到能下命令的"
                    f"战略面（当前识别为 {s or '未知'}）—— 像是在等一个决策",
                    v)

            time.sleep(0.6)
        return {"stopped_at": "timeout", "reason": "一旬超过 150 秒"}
        return {"stopped_at": "timeout", "reason": "一旬超过 120 秒"}

    def _auto_answer(self, screen: str) -> None:
        """固定规则应答。规则写在 config/answers.json，跨模型必须一致。"""
        for xy in self.answers.get(screen, []):
            self.drv.click(tuple(xy), 0.5, f"auto:{screen}")
            return
        self.drv.key("esc", 1, 0.5)

    def _topbar_looks_like_perform(self) -> bool:
        """顶栏看起来像"过旬演出"吗？判据：读到了字，但**信望/資金/兵糧 一个都没读到**。

        为什么需要这条：用户把动画关掉之后，「过旬演出」变成**完全静止的帧**
        （实测那帧是張梁"鎖定目標！以齊射重挫袁術部隊"的立绘 + 顶栏进度条 + 「靜止」）。
        静止 → 帧间无变化 → "停住 14 秒 = 在等我决策"的判据被误触发 →
        长跑在第 0 旬就停，理由还写成"像是在等一个决策"。

        正确反应是**等**：游戏自己在演，不需要任何操作。

        为什么用"读不到"而不是"读得到「靜止」"：静/止 两个字这套字体下极难读。
        ⚠️ 已知代价：**「進行記錄」屏幕的顶栏被压暗，OCR 也是空** → 会被误判成演出，
        白等最多 perform_grace 秒。量过亮度/橙色占比都区分不开这两个屏（实测
        演出 中位44/橙4.3%，進行記錄 中位26/橙9.7%），所以**不硬凑判据**，
        改用"设上限"兜住 —— 误判最多浪费一轮，不会害死长跑。

        ⚠️ OCR 读到空时返回 False —— 不能因为"探针坏了"就把整局当演出卡住。
        """
        if self.hwnd is None:
            return False
        try:
            items = ocr.read_screen(self.hwnd, PERFORM_TOPBAR, upscale=3)
        except Exception:
            return False
        txt = "".join(i["text"] for i in items).strip()
        if not txt:
            # **顶栏一个字都没有 → 就是演出中。**
            # 正常战略面这块一定有「184年9月上旬 張角 信望 資金 兵糧」，
            # 实测演出帧这里是一条橙色进度条 +「靜止」，OCR 一个字都读不出来。
            # （走过的弯路：一开始这里写成"读不到就返回 False，怕探针坏了"，
            #   结果判据在真实演出帧上直接失效 —— 怕坏探针反而把唯一可靠的
            #   信号丢了。"这块区域在正常面上验证过能读"才是可信前提。）
            return True
        return not any(m in txt for m in PERFORM_MARKS)

    def answer(self, choice: int = 0) -> dict:
        """回到需要决策的弹窗并选第 choice 个选项。

        之前这里是个空壳（只清 pending，什么也没点）—— 那是错的：
        调用方会以为决策生效了。现在转发给真正的动作分发。
        """
        return self._dispatch({"op": "game.answer", "choice": choice})

    def dismiss(self) -> dict:
        """关掉残留在画面上的面板/菜单（按 Esc）。

        ⚠️ 不要在有未决决策时调用。
        """
        self.pending.clear()
        ok = self.clear_blockage() if self.drv else False
        return {"op": "game.dismiss", "ok": ok, "screen": self.screen()}

    # ------------------------------------------------------------ 快照

    def snapshot(self, tag: str = "") -> str:
        saves = savefile.list_saves()
        if not saves:
            return ""
        src = saves.get("D_Auto00.S9") or saves.get("D_Sav000.S9")
        name = f"w{self.turn_index:05d}{'_' + tag if tag else ''}.S9"
        dst = os.path.join(SNAP_DIR, name)
        try:
            shutil.copy2(src, dst)
        except Exception:
            return ""
        return dst

    def rollback(self, to_turn: int | None = None) -> dict:
        """回滚到某个快照对应的旬。"""
        snaps = sorted(f for f in os.listdir(SNAP_DIR) if f.endswith(".S9"))
        if not snaps:
            return {"ok": False, "reason": "没有快照"}
        pick = snaps[-1]
        if to_turn is not None:
            cand = [s for s in snaps if s.startswith(f"w{to_turn:05d}")]
            pick = cand[-1] if cand else pick
        dst = savefile.list_saves().get("D_Sav000.S9")
        shutil.copy2(os.path.join(SNAP_DIR, pick), dst)
        self.turn_index = int(pick[1:6])
        return {"ok": True, "restored": pick, "turn": self.turn_index,
                "note": "存档文件已替换，还需在游戏内读档"}

    # ------------------------------------------------------------ 动作

    def act(self, acts: list[dict]) -> list[dict]:
        """执行一批语义动作，逐条返回审计结果。"""
        results = []
        for raw in acts:
            try:
                a = A.validate(raw)
            except A.ActionError as e:
                results.append({"op": raw.get("op"), "ok": False, "error": str(e)})
                continue
            r = self._dispatch(a, raw)
            results.append(r)
            self._act_log.write(json.dumps(
                {"turn": self.turn_index, "action": a, "result": r}, ensure_ascii=False) + "\n")
            self._act_log.flush()
        return results

    def _dispatch(self, a: dict, raw: dict | None = None) -> dict:
        op = a["op"]
        if op in ("turn.end",):
            return self.end_turn(1)
        if op in ("turn.skip",):
            return {"op": op, "ok": True, "note": "未标定，等价于 turn.end"}
        if op == "obs.refresh":
            return {"op": op, "ok": True, "obs": self.observe("brief")}
        if op == "save.snapshot":
            return {"op": op, "ok": True, "path": self.snapshot("manual")}
        if op == "save.rollback":
            return {"op": op, **self.rollback(a.get("to_turn"))}
        if op in ("game.dismiss",):
            # 循环按 Esc 直到画面真的通畅，并且**复核**结果 ——
            # 只按一次然后报 ok:True，是"假装成功"，调用方会被骗。
            return self.dismiss()

        if op == "game.answer":
            # 事件弹窗应答。choice: 0 = 第一个选项（是/允諾），1 = 第二个（否/拒絕）。
            #
            # **用键盘答，不点坐标、不认图像。** 依据是实测：
            #   按下 Enter，那个「是/否」的兵法修行弹窗直接进入了下一步
            #   （「選擇武將」），说明 Enter = 确认当前高亮项，默认高亮第一个。
            #   左右键切换高亮。
            #
            # 这条路的好处是根本性的：
            #   · 不需要知道选项按钮长什么样、在哪 —— 每个弹窗的按钮位置都不一样
            #     （「允諾/拒絕」在 y=611，「是/否」在 y=549，「確認」在 y=513，
            #      死坐标准广不了）
            #   · 不需要标定，新弹窗类型开箱即用
            #   · 快，而且不会点错地方
            if self.drv is None:
                return {"op": op, "ok": False, "error": "还没 attach 到游戏"}
            choice = int(a.get("choice", 0))
            was = self.screen()
            before = self.view()
            trace: list[dict] = []
            if choice > 0:
                self.drv.key("right", choice, 0.25)
                trace.append({"key": "right", "times": choice})
            self.drv.key("enter", 1, 1.5)
            trace.append({"key": "enter"})
            time.sleep(1.2)
            now = self.screen()
            after = self.view()
            moved = bool(before["frame"]) and vision.distance(
                before["frame"], after["frame"]) > self.motion_eps
            if not moved:
                # 按了没反应 = 这个画面不吃键盘，或者根本没有弹窗
                return {"op": op, "ok": False, "kind": "no_effect",
                        "error": "按 Enter 后画面没有任何变化 —— 可能这里不是键盘可答的弹窗",
                        "screen_before": was, "screen_after": now, "trace": trace}
            self.pending.clear()
            self.events.append({"type": "answered", "turn": self.turn_index,
                                "choice": choice, "was": was, "now": now})
            return {"op": op, "ok": True, "choice": choice,
                    "screen_before": was, "screen_after": now, "trace": trace}

        # ---- 设施类命令（巡察/徵兵/開墾…）：走"点城市 → 菜单 → 命令界面 → 选人 → 執行" ----
        #
        # 界面细节全在 `san9/cmdscreen.py`（几何都是量出来的）。
        # 这里只负责把 agent 的语义参数翻译成界面操作，并逐步回报实测数据。
        #
        # ⚠️ 已知缺口：**按武将名字选人还没实现**（需要 OCR 才能把名字对到行号）。
        #    现在支持：`officers` 传行号（int 列表）、或 `all_officers: true` 全选。
        if op in cmdscreen.OP_TO_CMD:
            if self.hwnd is None:
                return {"op": op, "ok": False, "error": "还没 attach 到游戏"}
            raw = raw or {}
            cmd = cmdscreen.OP_TO_CMD[op]
            rows_info = cmdscreen.city_rows(self.hwnd)
            city_row = raw.get("city_row")
            if city_row is None:
                hl = [i for i, r in enumerate(rows_info) if r["highlight"]]
                if not hl:
                    return {"op": op, "ok": False, "kind": "city_not_found",
                            "error": "右侧「移動列表」里认不出当前城市（高亮行），"
                                     "请显式给 city_row",
                            "list_rows": rows_info}
                city_row = hl[0]

            offs = raw.get("officers")
            officer_rows = None
            select_all = bool(raw.get("all_officers")) or offs == "all"
            if isinstance(offs, list) and offs:
                if all(isinstance(x, int) for x in offs):
                    officer_rows = [cmdscreen.PICK_ROWS_Y0 + int(i) * cmdscreen.PICK_ROW_PITCH
                                    for i in offs]
                else:
                    return {"op": op, "ok": False, "kind": "needs_ocr",
                            "error": "按武将名字选人需要 OCR（未实现）。"
                                     "请传行号 int 列表（如 [0,1]）或 all_officers: true。"}

            res = cmdscreen.run_facility_command(
                self.hwnd, city_row, cmd,
                officer_rows=officer_rows, select_all=select_all)
            self.events.append({"type": "cmd", "turn": self.turn_index, "op": op,
                                "cmd": cmd, "city_row": city_row, "ok": res.get("ok")})
            return {"op": op, "ok": bool(res.get("ok")), "cmd": cmd,
                    "city_row": city_row, "delay": getattr(A.OPS.get(op), "delay", None),
                    "steps": res.get("steps"), "failed_at": res.get("failed_at"),
                    "list_rows": len(rows_info), "note": res.get("note")}

        spec = A.OPS.get(op)
        if spec is None:
            return {"op": op, "ok": False, "error": f"{op} 是元操作但未实现"}

        try:
            trace = self.drv.run_path(spec.menu, a)
        except driver.NotCalibrated as e:
            return {"op": op, "ok": False, "error": str(e), "kind": "not_calibrated"}
        except TimeoutError as e:
            return {"op": op, "ok": False, "error": str(e), "kind": "timeout"}
        return {
            "op": op,
            "ok": True,
            "delay": spec.delay,
            "expect": spec.verify,
            "trace": trace,
        }

    def close(self) -> None:
        self._obs_log.close()
        self._act_log.close()
