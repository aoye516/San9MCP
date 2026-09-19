"""軍事 → 出征：逐步驱动（**基础版**：只配「執行武將」+ 士兵数量）。

⭐ 铁律：每个 `step_*` **恰好一次改屏动作**。

真机 2026-09-20 现量（用户在旁）：

    軍事(主菜单1) → 出征(子菜单0)
      → 点「執行武將」→ 「選擇武將」弹窗（**区域内弹窗、多选**）
        → 勾选（**点 x=250**）→ 「決定」
          → ⭐ **游戏自动填好 大將 / 陣形 / 船 / 士兵 / 派生数值** ⇒ 绿「執行」自己就亮了
      → （可选）点「士兵」→ 「輸入數字」对话框 → 设数量 → 「執行」
      → 点绿「執行」→ 进入**目标选择**（地图模式 / 右下「一覽」）

⭐⭐ 本命令与前三条（移動/探索/登庸）**结构完全不同**：

| | 移動/探索/登庸 | **出征** |
|---|---|---|
| 界面 | 居中命令条 | **全屏面板**，左侧一列字段 |
| 彩色标签 | 1~2 个 | **0 个**（字段是灰底框）⇒ `looks_like_move_screen` 判不了 |
| 目标 | 列表 / 弹窗 | **进地图模式 + 右下「一覽」** |
| 基础版要不要配数值 | 探索不用 | 选完武将**全自动**，只有想改才需要 |

⚠️ 所以**不能**用 `looks_like_move_screen` 当出征屏的判据（它要求 1~2 个彩色标签）。
   本模块用**字段行的位置**（`FIELD_Y`）当判据。
"""

from __future__ import annotations

import time

import cv2
import numpy as np

from san9 import cmdscreen, ocr, targetlist, winio


def _grab_int(hwnd):
    """取整幅画面的灰度图（float），用于「这一下屏幕变了没」。抓不到就给全零。"""
    b = ocr._grab_bgr(hwnd)
    if b is None:
        return np.zeros((960, 1280), dtype=float)
    return (b[:, :, 0].astype(float) * 0.114 + b[:, :, 1].astype(float) * 0.587
            + b[:, :, 2].astype(float) * 0.299)

MAIN_INDEX = 1                       # 軍事
SUB_INDEX = 0                        # 出征

# ── 出征屏左列字段（真机现量，行距约 49.7）──────────────────────────
FIELD_LABEL_X = 244
"""点**字段标签**用的 x。

⛔ 实测：点「士兵」的**数值区**(x≈345) **没反应**，必须点**标签**(x≈244)。
"""

FIELD_Y = {
    "執行武將": 206,
    "大將": 255,
    "陣形": 305,
    "船": 355,
    "士兵": 405,
    "資金": 455,
    "士氣": 505,
}
"""左列各字段的行中心 y（客户端坐标，真机现量）。"""

SCREEN_BTN_BAND = (800, 900, 850, 1080)
"""出征屏底部 [執行](绿) / [中止](红) 的搜索带 —— 语义 **(y0, y1, x0, x1)**。

⚠️ 与 `movecmd.MOVE_BTN_BAND`（y 640..800）**不是同一条** ——
   出征屏的按钮更低（y≈834）。
"""

# ── 「選擇武將」弹窗（区域内弹窗）────────────────────────────────────
PICK_TOGGLE_X = 250
"""⭐ 勾选一行的 x。**必须点格子本体**。

⛔⛔ 这里和别的命令**不一样**，实测（2026-09-20）：
   · x=243 → 无效
   · **x=250 → 有效**（行亮度 30 → 89）
   · x=290 → 无效（那是**名字文字区**；而 `cmdscreen.PICK_ROW_X = 290` 是
     「移動」那个弹窗量的，⛔ 别在这里套用）
   ⇒ 本模块用自己的常量，⛔ 不改 `cmdscreen` 里已验证过的那份。
"""

PICK_ROW0_Y = 413
PICK_PITCH = 30
PICK_N_SLOTS = 15
PICK_DECIDE_XY = (837, 745)
"""「決定」按钮（中性灰；**没勾人时点了没反应**，实测验证过）。"""

PICK_ABORT_XY = (950, 745)

# ── 「輸入數字」对话框（士兵数量）──────────────────────────────────
#
# 真容（放大核对过，`shots/dialog_btns_zoom.png` / `dialog_top_zoom.png`）：
#
#     士兵：   15000 → 15000            ← 当前值 → 新值（新值才受编辑）
#     [最小]  ====滑条====  [最大]
#     [後退] [清去]
#       7 8 9
#       4 5 6
#       1 2 3
#       0 00
#     [執行(绿)] [中止(红)]
#
NUM_EXECUTE_XY = (706, 771)
NUM_ABORT_XY = (805, 772)
NUM_MIN_XY = (485, 393)          # 「最小」
NUM_MAX_XY = (797, 393)          # 「最大」
NUM_BACK_XY = (580, 470)         # 「後退」
NUM_CLEAR_XY = (700, 470)        # 「清去」
NUM_KEYS = {
    "7": (559, 529), "8": (637, 529), "9": (717, 530),
    "4": (557, 590), "5": (637, 590), "6": (726, 587),
    "1": (559, 650), "2": (637, 650), "3": (719, 650),
    "0": (577, 709), "00": (697, 710),
}
"""数字键盘（真机现量）。"""


# ── 只读：判在不在出征屏 / 读字段 ───────────────────────────────────

def read_fields(hwnd, bgr=None) -> dict:
    """读左列字段的**值**（只读）。

    用 OCR 取每个字段行右侧的值区（x 340..700）。⛔ 读不出就是 `None`，不猜。

    ⚠️ 这里只做"给人看"的读数 —— **判据不依赖它**（判据用位置）。
    """
    if bgr is None:
        bgr = ocr._grab_bgr(hwnd)
    if bgr is None:
        return {}
    try:
        boxes = [b for b in ocr.read(bgr, upscale=3)
                 if 180 <= int(b["y"]) <= 540 and 330 <= int(b["x"]) <= 700]
    except Exception:
        boxes = []
    out = {}
    for name, y in FIELD_Y.items():
        got = sorted([b for b in boxes if abs(int(b["y"]) - y) <= 16],
                     key=lambda b: int(b["x"]))
        out[name] = "".join((b.get("text") or "").strip() for b in got) or None
    return out


SCREEN_MARK_ROWS = ("大將", "資金", "士氣")
"""出征屏**判据行**（真机四帧实测，x 195..340 的灰度均值）：

| 状态 | 執行武將 | 大將 | 資金 | 士氣 |
|---|---|---|---|---|
| 刚打开（執行武將高亮）| 85.3 | 89.2 | 88.3 | 94.5 |
| 配好后（決定完）| 85.3 | 91.9 | 88.3 | 94.5 |
| 士兵被选中 | **68.3** ⛔ | 91.0 | 87.8 | 93.9 |
| 數字对话框开着 | 31.9 | 43.4 | 41.7 | 44.8 |

⛔⛔ **不能拿「執行武將」当判据** —— 它是**初始被高亮**的那一个，
   高亮一移走（比如点了「士兵」）它的亮度就掉到 **68.3**，阈值 75 就误判成"不是出征屏"
   （2026-09-20 踩实：`san9_deploy(soldiers=8000)` 明明成功了却报"看不出回到出征屏"）。
   同 §16.3 第 13 条：**判"在不在某个屏"要用不随状态变的东西**。
"""

SCREEN_MARK_MIN = 2
"""`SCREEN_MARK_ROWS` 里**至少几行**超过阈值就算在出征屏。

⭐ 取 2/3 而不是 3/3：万一某一行**变成了被高亮的那一个**（亮度会变），
   只要另外两行还亮着就能认出这张屏。
"""

SCREEN_MARK_THR = 75.0
"""判据行的灰度阈值。实测三态都在 **87~94**（对话框态 41~45）⇒ 余量很大。"""


def screen_present(hwnd, bgr=None) -> dict:
    """结构判据：**在不在出征屏上**（只读）。

    ⛔ 不能用 `looks_like_move_screen` —— 它要求 1~2 个**彩色标签**，
       而出征屏**一个都没有**（字段是灰底框）。

    ⭐ 判据 = `SCREEN_MARK_ROWS` 那 3 个**恒亮标签框**里至少 2 个 > 75（见该常量）。
    ⛔ 别拿"数值区"当判据（空屏上大將/陣形/士兵只有 35~41 ⇒ 必然误判，见 §16.3）。
    ⚠️ **數字对话框开着时本判据返回 False**（那时整张屏都被压暗）——
       各步骤内部都先看 `dialog_present`，不会混淆。
    """
    if bgr is None:
        bgr = ocr._grab_bgr(hwnd)
    if bgr is None:
        return {"ok": False, "why": "抓不到画面"}
    g = (bgr[:, :, 0].astype(float) * 0.114 + bgr[:, :, 1].astype(float) * 0.587
         + bgr[:, :, 2].astype(float) * 0.299)
    hits = {}
    for name in SCREEN_MARK_ROWS:
        y = FIELD_Y[name]
        hits[name] = round(float(g[y - 12:y + 12, 195:340].mean()), 1)
    n_ok = sum(1 for v in hits.values() if v > SCREEN_MARK_THR)
    r = {"ok": n_ok >= SCREEN_MARK_MIN, "means": hits, "n_ok": n_ok,
         "need": SCREEN_MARK_MIN}
    if not r["ok"]:
        r["why"] = ("标志行只亮 %d/%d 个（%s）⇒ 不像出征屏。"
                    % (n_ok, len(hits), hits))
    return r


def picker_present(hwnd, bgr=None) -> dict:
    """结构判据：**「選擇武將」弹窗在不在**（只读）。

    判据 = ① 弹窗覆盖区（x 480..1010, y 350..680）是暗面板
          + ② 底栏写着「選擇執行武將」（**必须**，见下）。

    ⛔⛔ 2026-09-20 踩实：只看①会把**「選擇對象」目标表**也判成选将弹窗
       （目标表也是一块大暗面板）⇒ `stage_of` 认错层、`step_select_officers` 的
       前置门形同虚设。加上②（两句底栏提示互斥：`選擇執行武將` vs `請選擇目標`）就分开了。
    """
    if bgr is None:
        bgr = ocr._grab_bgr(hwnd)
    if bgr is None:
        return {"ok": False, "why": "抓不到画面"}
    g = (bgr[:, :, 0].astype(float) * 0.114 + bgr[:, :, 1].astype(float) * 0.587
         + bgr[:, :, 2].astype(float) * 0.299)
    # 弹窗覆盖区：x 480..1010, y 350..680 —— 里面应该有大片深色面板
    cover = float(g[350:680, 480:1010].mean())
    left = float(g[FIELD_Y["執行武將"] - 12:FIELD_Y["執行武將"] + 12, 195:340].mean())
    hint = targetlist.bottom_hint(bgr) or ""
    hint_ok = "武將" in hint
    r = {"ok": (cover < 45 and hint_ok), "cover_mean": round(cover, 1),
         "left_mean": round(left, 1), "hint": hint}
    if not r["ok"]:
        r["why"] = ("覆盖区 mean=%.1f（要 <45）、底栏=%r（要含「武將」）⇒ 选将弹窗没开。"
                    % (cover, hint))
    return r


DIALOG_KEYPAD_BAND = (500, 740, 480, 800)
"""数字键盘的搜索带 —— 语义 **(y0, y1, x0, x1)**（真机现量）。

⭐ 这是本对话框**最可靠的结构特征**：11 个**绿色**按键块。
"""


def dialog_present(hwnd, bgr=None) -> dict:
    """结构判据：**「輸入數字」对话框在不在**（只读）。

    ⛔⛔ 2026-09-20 踩实：原来这里用 `find_button_in(bgr,"green", *SCREEN_BTN_BAND)`
        （y 800..900）当判据 —— 而**对话框的「執行」在 y≈771**，
        **根本不在那个带里** ⇒ 明明对话框开着，判据说"没开"，
        `san9_deploy(soldiers=8000)` 因此直接失败。

    ⭐ 可靠判据 = **数字键盘的 11 个绿色按键块**（实测：对话框开着 **11** 个，
       没开时 **1** 个 —— 间隔极大）。
    ⛔ 别用按钮颜色判（这个对话框的按钮带**金色边框**，red/green 检测都会误命中边框）。

    | 状态 | 绿块数 |
    |---|---|
    | 对话框开着 | **11** |
    | 出征屏（无对话框）| **1** |

    ⇒ 门槛取 **8**。
    """
    import numpy as np
    if bgr is None:
        bgr = ocr._grab_bgr(hwnd)
    if bgr is None:
        return {"ok": False, "why": "抓不到画面"}
    y0, y1, x0, x1 = DIALOG_KEYPAD_BAND
    sub = bgr[y0:y1, x0:x1]
    b = sub[:, :, 0].astype(int)
    g = sub[:, :, 1].astype(int)
    r = sub[:, :, 2].astype(int)
    mask = ((g > 80) & (g - r > 25) & (g - b > 10)).astype(np.uint8)
    n_lab, _lab, stats, _ce = cv2.connectedComponentsWithStats(np.ascontiguousarray(mask), 8)
    n_keys = sum(1 for s in stats[1:] if int(s[4]) >= 200)
    out = {"ok": n_keys >= 8, "n_keys": n_keys, "band": list(DIALOG_KEYPAD_BAND)}
    if not out["ok"]:
        out["why"] = "数字键盘绿块只有 %d 个（要 ≥8）⇒ 对话框没开。" % n_keys
    return out


# ── 步骤 ─────────────────────────────────────────────────────────────

def step_open_screen(hwnd, row: int, expect: str | None = None) -> dict:
    """选右侧列表第 `row` 行 → 开命令菜单 → 键盘走到「軍事 → 出征」→ Enter。

    ⭐ 复用 `movecmd.step_open_screen`（已参数化）。
    ⛔ 但它内建的门禁是"彩色标签 1~2 个"—— 出征屏**没有彩色标签** ⇒ 用 `gate=False`，
       改由本模块的 `screen_present`（字段框位置）当后置门。
    """
    from san9 import movecmd
    out = movecmd.step_open_screen(hwnd, row, expect,
                                   sub_index=SUB_INDEX, name="出征", gate=False,
                                   menu_index=MAIN_INDEX)
    out["step"] = "open_screen"
    if not out.get("ok"):
        return out
    sc = screen_present(hwnd)
    out["screen"] = sc
    if not sc.get("ok"):
        out["ok"] = False
        out["why"] = "进了界面但结构判据说**不是出征屏**：%s" % sc.get("why")
    return out


def step_open_picker(hwnd) -> dict:
    """点「執行武將」字段 → 弹出「選擇武將」。"""
    out: dict = {"step": "open_picker"}
    sc = screen_present(hwnd)
    if not sc.get("ok"):
        out["why"] = "前置门：不像出征屏（%s）⇒ **一个像素都没点**。" % sc.get("why")
        return out
    xy = (FIELD_LABEL_X, FIELD_Y["執行武將"])
    winio.click_client(hwnd, xy[0], xy[1], settle=0.35)
    time.sleep(1.5)
    pk = picker_present(hwnd)
    out["picker"] = pk
    out["clicked"] = list(xy)
    if not pk.get("ok"):
        out["why"] = "点了「執行武將」但**弹窗没开**（%s）⇒ 停手。" % pk.get("why")
        return out
    out["ok"] = True
    return out


def step_select_officers(hwnd, rows) -> dict:
    """在弹窗里**勾选**指定行（0 起）。⛔ x 用 `PICK_TOGGLE_X`（不是 cmdscreen 那个）。"""
    out: dict = {"step": "select_officers", "rows": list(rows)}
    if not picker_present(hwnd).get("ok"):
        out["why"] = "弹窗不在 ⇒ **一个像素都没点**。"
        return out
    bad = [r for r in rows if not (0 <= r < PICK_N_SLOTS)]
    if bad:
        out["why"] = "行号越界 %s（弹窗最多 %d 行）⇒ **一个像素都没点**。" % (bad, PICK_N_SLOTS)
        return out
    for r in rows:
        winio.click_client(hwnd, PICK_TOGGLE_X, PICK_ROW0_Y + r * PICK_PITCH, settle=0.35)
        time.sleep(0.6)
    out["ok"] = True
    return out


def step_decide(hwnd) -> dict:
    """点弹窗「決定」→ 回到出征屏，游戏会自动填好 大將/陣形/船/士兵。"""
    out: dict = {"step": "decide"}
    if not picker_present(hwnd).get("ok"):
        out["why"] = "弹窗不在 ⇒ **一个像素都没点**。"
        return out
    winio.click_client(hwnd, PICK_DECIDE_XY[0], PICK_DECIDE_XY[1], settle=0.35)
    time.sleep(1.8)
    out["left_picker"] = not bool(picker_present(hwnd).get("ok"))
    if not out["left_picker"]:
        out["why"] = ("点了「決定」但**弹窗还在** ⇒ 多半是**一个人都没勾上**"
                      "（没勾人时「決定」是死的，点它没反应 —— 实测验证过）。")
        return out
    sc = screen_present(hwnd)
    out["screen"] = sc
    if not sc.get("ok"):
        out["why"] = "弹窗关了，但**看不出回到出征屏**（%s）⇒ 停手。" % sc.get("why")
        return out
    out["fields"] = read_fields(hwnd)
    g = deploy_execute(hwnd)
    out["execute_button"] = list(g) if g else None
    out["ok"] = True
    return out


def deploy_execute(hwnd) -> tuple[int, int] | None:
    """出征屏底部**绿「執行」**的坐标（= 游戏自己给的"可以出征了"硬信号）。"""
    bgr = ocr._grab_bgr(hwnd)
    if bgr is None:
        return None
    return cmdscreen.find_button_in(bgr, "green", *SCREEN_BTN_BAND)


def step_set_soldiers(hwnd, count: int | None = None, use: str = "value",
                      save_shot=None) -> dict:
    """点「士兵」→ 「輸入數字」对话框 → 设数量 → 对话框「執行」。

    `count=None` 或 `use="max"` → 点「最大」（= 让每个武将带满）。
    `count=int` → 先「清去」再逐位点数字键（例：8000 → 8,0,0,0）。

    ⛔ 不轮询、不猜坐标 —— 每个键都是现量出来的。
    """
    out: dict = {"step": "set_soldiers", "count": count, "use": use}
    sc = screen_present(hwnd)
    if not sc.get("ok"):
        out["why"] = "前置门：不像出征屏（%s）⇒ **一个像素都没点**。" % sc.get("why")
        return out
    winio.click_client(hwnd, FIELD_LABEL_X, FIELD_Y["士兵"], settle=0.35)
    time.sleep(1.5)
    # ⛔ 判据 = 数字键盘绿块（**不是**按钮颜色 —— 对话框「執行」在 y≈771，
    #    不在 SCREEN_BTN_BAND(y800..900) 里，用那个判必失败，2026-09-20 踩实）
    dl = dialog_present(hwnd)
    out["dialog"] = dl
    if not dl.get("ok"):
        out["why"] = "点了「士兵」但**没出现「輸入數字」对话框**（%s）⇒ 停手。" % dl.get("why")
        return out
    if use == "max" or count is None:
        winio.click_client(hwnd, NUM_MAX_XY[0], NUM_MAX_XY[1], settle=0.35)
        time.sleep(0.7)
        out["pressed"] = "max"
    else:
        winio.click_client(hwnd, NUM_CLEAR_XY[0], NUM_CLEAR_XY[1], settle=0.35)
        time.sleep(0.6)
        pressed = ["clear"]
        for ch in str(int(count)):
            if ch not in NUM_KEYS:
                out["why"] = "数字里有我没有键位的字符 %r ⇒ 已按下的：%s" % (ch, pressed)
                return out
            winio.click_client(hwnd, NUM_KEYS[ch][0], NUM_KEYS[ch][1], settle=0.3)
            time.sleep(0.4)
            pressed.append(ch)
        out["pressed"] = pressed
    out["typed"] = read_soldier_dialog(hwnd)      # ⭐ 点「執行」**之前**先读数，留证
    if save_shot:
        save_shot("soldier_dialog_typed")
    winio.click_client(hwnd, NUM_EXECUTE_XY[0], NUM_EXECUTE_XY[1], settle=0.35)
    time.sleep(1.6)
    left = not bool(dialog_present(hwnd).get("ok"))
    out["left_dialog"] = left
    if not left:
        out["why"] = "对话框「執行」之后**对话框还在** ⇒ 这一下没生效 ⇒ 停手（不重试）。"
        return out
    out["fields_after"] = read_fields(hwnd)
    out["ok"] = bool(screen_present(hwnd).get("ok"))
    if not out["ok"]:
        out["why"] = "离开对话框后看不出回到出征屏 ⇒ 停手。"
    return out


def read_soldier_dialog(hwnd, bgr=None) -> dict | None:
    """读「輸入數字」对话框顶部那一行 `士兵： <现在> → <变更后>`（只读）。

    **只用于留证**（判据不依赖它）。识别不到就返回 `None`，不猜。
    """
    if bgr is None:
        bgr = ocr._grab_bgr(hwnd)
    if bgr is None:
        return None
    try:
        boxes = [b for b in ocr.read(bgr, upscale=3)
                 if 195 <= int(b["y"]) <= 245 and 480 <= int(b["x"]) <= 900]
    except Exception:
        return None
    got = sorted(boxes, key=lambda b: int(b["x"]))
    text = " ".join((b.get("text") or "").strip() for b in got).strip()
    nums = [t for t in ((b.get("text") or "").strip() for b in got) if t.isdigit()]
    if not text:
        return None
    return {"raw": text, "numbers": nums,
            "now": int(nums[0]) if nums else None,
            "next": int(nums[1]) if len(nums) > 1 else None}


def step_abort_dialog(hwnd) -> dict:
    """「輸入數字」对话框 → 点「中止」关掉它（**一个**能验证的动作）。

    ⛔ 只有确认对话框真的开着才点（`dialog_present`）；关掉后要确认它真的没了。
    """
    out: dict = {"step": "abort_dialog"}
    dl = dialog_present(hwnd)
    if not dl.get("ok"):
        out["why"] = "对话框不在 ⇒ **一个像素都没点**。"
        out["ok"] = True                     # 目的已达成（就是要它不在）
        out["already"] = True
        return out
    winio.click_client(hwnd, NUM_ABORT_XY[0], NUM_ABORT_XY[1], settle=0.35)
    time.sleep(1.4)
    now = dialog_present(hwnd)
    out["dialog_after"] = now
    if now.get("ok"):
        out["why"] = "点了「中止」但**对话框还在** ⇒ 停手（不重试）。"
        return out
    out["ok"] = True
    out["note"] = "对话框已关（现在应是出征屏）。"
    return out


def step_execute(hwnd, save_shot=None, wait_s: float = 2.6) -> dict:
    """点出征屏的绿「執行」→ 进入**目标选择**阶段。

    ⭐ 实测：点完**自动弹出**「選擇對象」目标一览（不需要另外去点「一覽」）。
    """
    out: dict = {"step": "execute"}
    g = deploy_execute(hwnd)
    out["green_execute"] = list(g) if g else None
    if g is None:
        out["why"] = "「執行」不是绿的 ⇒ 还没配好（**没选执行武将？**）⇒ 不点。"
        return out
    winio.click_client(hwnd, g[0], g[1], settle=0.35)
    time.sleep(wait_s)
    tp = targets_present(hwnd)
    if not tp.get("ok") and wait_s < 4.0:
        time.sleep(2.0)                       # 只等这一次，不轮询
        tp = targets_present(hwnd)
    out["targets"] = tp
    if save_shot:
        out["shot"] = save_shot("after_execute")
    if not tp.get("ok"):
        out["why"] = "点了「執行」但**没弹出「選擇對象」目标表**（%s）⇒ 停手。" % tp.get("why")
        return out
    out["ok"] = True
    out["note"] = "已进**选择目标**：屏幕上开着「選擇對象」表（15 行）。"
    return out


# ── 目标选择：「選擇對象」表 ─────────────────────────────────────────
TARGET_ROW0_Y = 304
TARGET_ROW_PITCH = 30
TARGET_N_SLOTS = 15
"""「選擇對象」目标表的行几何（真机现量）。

⚠️ **又是一张原点不同的表**：`y = 304, 334, …, 724`（pitch **30.00**，15 行）。
   本表原点 **304** = 設施表/地域表，但 ⛔ ≠ 「選擇武將」表的 **323**。
"""

TARGET_COLS = (
    ("name",   "對象", 382, 505),
    ("force",  "勢力", 505, 600),
    ("troops", "士兵", 602, 680),
    ("hurt",   "傷兵", 682, 762),
    ("morale", "士氣", 763, 808),
    ("status", "狀態", 810, 900),
)
"""目标表各列的 **x 窗口**（真机现量：各列中心 對象416 · 勢力546 · 士兵631 · 傷兵696 · 士氣786 · 狀態836）。"""

TARGET_COL_SCALES = (3, 4, 5)

TARGET_ABORT_XY = (791, 813)
"""目标表左下角的红「中止」。"""

TARGET_MAP_XY = (564, 783)
"""目标表左下角的「地圖」按钮（切去地图上看位置；本工具用不到，留档）。"""

TARGET_HL_THR = 60.0
"""高亮行判据阈值。实测**高亮行 82.2 · 其余 24~41**（间隔极大）。"""


def targets_present(hwnd, bgr=None) -> dict:
    """结构判据：**「選擇對象」目标表在不在**（只读）。

    ⭐ 两级：① 15 行里**恰好一行**明显更亮（= 高亮行）+ ② 底栏写着「請選擇目標」。
       两条同时成立才算 —— 单看任一条都会和「選擇武將」表（也有高亮行）混。
    """
    if bgr is None:
        bgr = ocr._grab_bgr(hwnd)
    if bgr is None:
        return {"ok": False, "why": "抓不到画面"}
    g = (bgr[:, :, 0].astype(float) * 0.114 + bgr[:, :, 1].astype(float) * 0.587
         + bgr[:, :, 2].astype(float) * 0.299)
    means = []
    for r in range(TARGET_N_SLOTS):
        y = TARGET_ROW0_Y + r * TARGET_ROW_PITCH
        means.append(round(float(g[y - 8:y + 8, 400:1200].mean()), 1))
    bright = [r for r, v in enumerate(means) if v > TARGET_HL_THR]
    hint = targetlist.bottom_hint(bgr) or ""
    hint_ok = "目標" in hint
    r = {"ok": (len(bright) == 1 and hint_ok), "highlight_rows": bright,
         "means": means, "hint": hint}
    if not r["ok"]:
        r["why"] = ("高亮行有 %d 个（要恰好 1 个）、底栏=%r（要含「目標」）⇒ 不像选择对象表。"
                    % (len(bright), hint))
    return r


def highlight_row(hwnd, bgr=None) -> int | None:
    """当前高亮的行号（只读）。⛔ 判不出来就返回 `None`，不猜。"""
    if bgr is None:
        bgr = ocr._grab_bgr(hwnd)
    if bgr is None:
        return None
    g = (bgr[:, :, 0].astype(float) * 0.114 + bgr[:, :, 1].astype(float) * 0.587
         + bgr[:, :, 2].astype(float) * 0.299)
    vals = []
    for r in range(TARGET_N_SLOTS):
        y = TARGET_ROW0_Y + r * TARGET_ROW_PITCH
        vals.append(float(g[y - 8:y + 8, 400:1200].mean()))
    top = max(vals)
    if top < TARGET_HL_THR:
        return None
    return int(vals.index(top))


def read_targets(hwnd, rows=None, bgr=None) -> list[dict]:
    """读「選擇對象」目标表若干行（**分列 OCR + 多尺度投票**，只读）。

    返回每行 `{row, y, name, force, troops, hurt, morale, status, name_raw}`。

    ⛔ **读不出就是 `None`，不猜**。实测**单字城名（`薊`/`鄴`）与 `平原`/`下邳` 常读不出**
       —— 但**不影响操作**（选目标按 `row` 行号；点完在「方針」窗还会用大字号回读 `目標` 核对）。
    """
    if bgr is None:
        bgr = ocr._grab_bgr(hwnd)
    if bgr is None:
        return []
    if rows is None:
        rows = list(range(TARGET_N_SLOTS))

    def cell(y: int, x0: int, x1: int):
        votes: dict = {}
        for up in TARGET_COL_SCALES:
            try:
                for b in ocr.read(bgr[y - 15:y + 15, x0:x1], upscale=up):
                    t = (b.get("text") or "").strip()
                    if t:
                        votes[t] = votes.get(t, 0) + 1
            except Exception:
                continue
        if not votes:
            return None
        return sorted(votes.items(), key=lambda kv: (-kv[1], len(kv[0])))[0][0]

    out = []
    for r in rows:
        y = TARGET_ROW0_Y + r * TARGET_ROW_PITCH
        item: dict = {"row": r, "y": y}
        for key, _label, x0, x1 in TARGET_COLS:
            t = cell(y, x0, x1)
            item[key] = t
        for key in ("troops", "hurt", "morale"):
            v = item.get(key)
            item[key] = int(v) if (v and v.isdigit()) else None
        item["name_raw"] = item.get("name")
        out.append(item)
    return out


def step_pick_target(hwnd, row: int, expect: str | None = None,
                     save_shot=None) -> dict:
    """点「選擇對象」表第 `row` 行 → **直接弹出「決定方針」窗**（实测：不是先走地图）。

    ⭐ 点完立刻**用大字号回读「目標」**核对（`read_houshin`）—— 因为目标表上的城名
       小字常读不出，而方针窗里读得很干净。这就是本段的**二级核实**。
    ⛔ 只点**一个**点（表行位置是现量的），**不换坐标重试**。
    """
    out: dict = {"step": "pick_target", "row": row, "expect": expect}
    tp = targets_present(hwnd)
    if not tp.get("ok"):
        out["why"] = "前置门：不像「選擇對象」表（%s）⇒ **一个像素都没点**。" % tp.get("why")
        return out
    bad = [r for r in (row,) if not (0 <= r < TARGET_N_SLOTS)]
    if bad:
        out["why"] = "行号越界 %s（表里最多 %d 行）⇒ **一个像素都没点**。" % (bad, TARGET_N_SLOTS)
        return out
    y = TARGET_ROW0_Y + row * TARGET_ROW_PITCH
    winio.click_client(hwnd, TARGET_ROW_CLICK_X, y, settle=0.35)
    time.sleep(1.8)
    out["clicked"] = [TARGET_ROW_CLICK_X, y]
    hp = houshin_present(hwnd)
    out["houshin"] = hp
    if not hp.get("ok"):
        out["why"] = "点了目标行但**没弹出「決定方針」窗**（%s）⇒ 停手。" % hp.get("why")
        return out
    hs = read_houshin(hwnd)
    out["read"] = hs
    if save_shot:
        out["shot"] = save_shot("houshin_row%d" % row)
    got = (hs or {}).get("目標")
    out["target_name_read"] = got
    if expect and got and got != expect:
        out["why"] = ("⚠️ **目标核对不上**：期望「%s」，游戏报「%s」⇒ "
                      "按约定**停手**（不确认、也不另点一行）。" % (expect, got))
        return out
    if expect and not got:
        out["hint_warn"] = ("⚠️ 传了 `expect`=%r 但方针窗的「目標」**没读出来** ⇒ "
                            "**没核对上**（不是核对通过）。" % expect)
    out["ok"] = True
    return out


TARGET_ROW_CLICK_X = 450
"""点目标表某行用的 x。取 450（在「對象」列上）—— 整行都可点，选左边是为了离右边按钮远些。"""


# ── 方針窗 ───────────────────────────────────────────────────────────
HOUSHIN_EXECUTE_XY = (688, 731)
"""「決定方針」窗的绿「執行」= **真正的出征提交点**。"""

HOUSHIN_ABORT_XY = (798, 732)
"""「決定方針」窗的红「中止」→ **退回「選擇對象」表**（实测）。"""

HOUSHIN_RADIO_Y = {"敵接近時": 460, "自主撤退": 510, "追擊": 560, "事後命令": 610}
"""方针里 4 组二选一的**行 y**。"""

HOUSHIN_RADIO_X = {"1": 668, "2": 822}
"""每组两个按钮的 x（真机现量：左 621..715 中心 **668** · 右 770..875 中心 **822**）。

⚠️ **哪个是"选中"看颜色**（选中的是**橙色**，未选中的是暗色）—— 见 `read_houshin`。
"""

HOUSHIN_RADIO_LABELS = {
    "敵接近時": ("攻擊", "無視"),
    "自主撤退": ("許可", "不許可"),
    "追擊": ("許可", "不許可"),
    "事後命令": ("攻擊", "撤退"),
}
"""4 组二选一的**标签**（左/右）。"""

HOUSHIN_FIELD_Y = {
    "命令設施": 300, "到達預定": 300,
    "命令": 360, "目標": 410, "勢力": 410,
    "出征時期": 660,
}
"""方针窗里**文本字段**的行 y（供 `read_houshin` 定位）。"""

HOUSHIN_SCAN = (200, 700, 400, 1010)
"""方针窗读数的扫描区 **(y0,y1,x0,x1)**。"""

HOUSHIN_VALUE_X = {"左": (500, 700), "右": (790, 900)}
"""方针窗里**字段值**的 x 窗口（真机现量）。

⭐ 必须**按 x 归位**，⛔ 不能靠 OCR 的输出顺序 —— 2026-09-20 踩实：
   `目標` 那一行同时有「目標(标签, x421..463) / 洛陽(值, x591..632) /
   势力(标签, x731..770) / 何進(值, x820..862)」，按顺序取会把 **何進** 当成目标名，
   于是 `expect_target="洛陽"` 被误判成"核对不上"。
"""


def houshin_present(hwnd, bgr=None) -> dict:
    """结构判据：**「決定方針」窗在不在**（只读）。

    ⭐ 判据 = 它的绿「執行」按钮落在 (688,731) 附近（实测唯一、颜色很纯）。
    """
    if bgr is None:
        bgr = ocr._grab_bgr(hwnd)
    if bgr is None:
        return {"ok": False, "why": "抓不到画面"}
    g = cmdscreen.find_button_in(bgr, "green", 660, 800, 600, 800)
    if not g:
        return {"ok": False, "why": "方针窗位置没有绿按钮 ⇒ 窗没开。"}
    d = ((g[0] - HOUSHIN_EXECUTE_XY[0]) ** 2 + (g[1] - HOUSHIN_EXECUTE_XY[1]) ** 2) ** 0.5
    if d > 40:
        return {"ok": False, "found": list(g), "dist": round(d, 1),
                "why": "绿按钮在 (%d,%d)，离方针窗的「執行」太远 ⇒ 不是方针窗。" % g}
    return {"ok": True, "execute": list(g)}


def read_houshin(hwnd, bgr=None) -> dict | None:
    """读「決定方針」窗的内容（只读）。

    返回 `{命令設施, 到達預定, 目標, 勢力, 命令, 出征時期, 敵接近時, 自主撤退, 追擊, 事後命令}`
    —— 其中 4 组二选一给的是 **1 或 2**（1=左格，2=右格），靠**哪一格是橙色**判。

    ⛔ 读不出就是 `None`；二选一判不出来就是 `-1`（**不猜**）。
    """
    if bgr is None:
        bgr = ocr._grab_bgr(hwnd)
    if bgr is None:
        return None
    y0, y1, x0, x1 = HOUSHIN_SCAN
    raw: list = []
    for up in (2, 3, 4, 5):
        try:
            for b in ocr.read(bgr[y0:y1, x0:x1], upscale=up):
                t = (b.get("text") or "").strip()
                bx = b.get("box")
                if not t or not bx:
                    continue
                # `box` 是 [x0, y0, x1, y1]（**相对裁剪区**）⇒ 换算回全屏
                raw.append({"t": t, "x0": int(bx[0]) + x0, "x1": int(bx[2]) + x0,
                            "cx": (int(bx[0]) + int(bx[2])) // 2 + x0,
                            "cy": (int(bx[1]) + int(bx[3])) // 2 + y0})
        except Exception:
            continue

    def pick(y_target: int, wx0: int, wx1: int, exclude=()) -> str | None:
        """在 (行 y, x 窗口) 里按票数挑一个文本（**按 x 归位，不靠顺序**）。"""
        votes: dict = {}
        for b in raw:
            if abs(b["cy"] - y_target) > 20:
                continue
            if not (wx0 <= b["cx"] <= wx1):
                continue
            if b["t"] in exclude:
                continue
            votes[b["t"]] = votes.get(b["t"], 0) + 1
        if not votes:
            return None
        return sorted(votes.items(), key=lambda kv: (-kv[1], len(kv[0])))[0][0]

    out = {
        "命令設施": pick(HOUSHIN_FIELD_Y["命令設施"], *HOUSHIN_VALUE_X["左"]),
        "到達預定": pick(HOUSHIN_FIELD_Y["到達預定"], *HOUSHIN_VALUE_X["右"]),
        "命令": pick(HOUSHIN_FIELD_Y["命令"], *HOUSHIN_VALUE_X["左"]),
        "目標": pick(HOUSHIN_FIELD_Y["目標"], *HOUSHIN_VALUE_X["左"]),
        "勢力": pick(HOUSHIN_FIELD_Y["目標"], *HOUSHIN_VALUE_X["右"]),
        "出征時期": pick(HOUSHIN_FIELD_Y["出征時期"], *HOUSHIN_VALUE_X["左"]),
    }
    # 4 组二选一：看哪一格是橙色
    for field, ry in HOUSHIN_RADIO_Y.items():
        bgr_reg = bgr[ry - 12:ry + 12, :, :]
        picked = -1
        score = {}
        for k, rx in HOUSHIN_RADIO_X.items():
            reg = bgr_reg[:, rx - 45:rx + 45].reshape(-1, 3).mean(axis=0)
            b_, g_, r_ = float(reg[0]), float(reg[1]), float(reg[2])
            score[k] = round(r_ - b_, 1)          # 橙色 ⇒ R 明显大于 B
        if score["1"] > 25 and score["1"] > score["2"]:
            picked = 1
        elif score["2"] > 25 and score["2"] > score["1"]:
            picked = 2
        out[field] = picked
        out[field + "_score"] = score
    return out


def step_set_houshin(hwnd, field: str, value) -> dict:
    """改方针里的一组二选一（`field` ∈ `HOUSHIN_RADIO_Y` 的键）。

    `value` 可传 `"1"/"2"`（左/右格）或标签文字（如 `"不許可"`）。
    ⛔ 只在**确认方针窗开着**时点；点完**重读那组**确认真的换了。
    """
    out: dict = {"step": "set_houshin", "field": field, "value": value}
    if field not in HOUSHIN_RADIO_Y:
        out["why"] = "字段名不对：%r（可用：%s）" % (field, list(HOUSHIN_RADIO_Y))
        return out
    hp = houshin_present(hwnd)
    if not hp.get("ok"):
        out["why"] = "前置门：方针窗不在（%s）⇒ **一个像素都没点**。" % hp.get("why")
        return out
    key = str(value)
    if key not in HOUSHIN_RADIO_X:
        labels = HOUSHIN_RADIO_LABELS[field]
        if value in labels:
            key = str(labels.index(value) + 1)
        else:
            out["why"] = "`value` 认不出：%r（这组是 %s，或传 \"1\"/\"2\"）" % (value, labels)
            return out
    before = read_houshin(hwnd) or {}
    if before.get(field) == int(key):
        out["ok"] = True
        out["already"] = True
        out["note"] = "这一组本来就是 %s，**没点**。" % key
        return out
    winio.click_client(hwnd, HOUSHIN_RADIO_X[key], HOUSHIN_RADIO_Y[field], settle=0.35)
    time.sleep(0.9)
    after = read_houshin(hwnd) or {}
    out["after"] = after.get(field)
    if after.get(field) != int(key):
        out["why"] = ("点了「%s」，但那组读数变成 %s（想要 %s）⇒ 停手（不重复点）。"
                      % (field, after.get(field), key))
        return out
    out["ok"] = True
    return out


def step_confirm_houshin(hwnd, save_shot=None) -> dict:
    """**点方针窗的绿「執行」—— 这一步真正把出征命令发出去（不可逆）。**

    收尾核对：绿按钮消失（窗关了）+ 底栏回到 `請選擇命令起點`（`go` 锚点）。
    ⚠️ 提交后游戏会**弹一个武将台词框**（如「一定會拿回首功」），它会**自己消失**；
       这段时间 `go` 锚点读不到 ⇒ 允许**再等一次**（不轮询）。
    """
    out: dict = {"step": "confirm_houshin"}
    hp = houshin_present(hwnd)
    if not hp.get("ok"):
        out["why"] = "前置门：方针窗不在（%s）⇒ **一个像素都没点**（防重复提交）。" % hp.get("why")
        return out
    winio.click_client(hwnd, HOUSHIN_EXECUTE_XY[0], HOUSHIN_EXECUTE_XY[1], settle=0.35)
    time.sleep(3.0)
    out["houshin_after"] = houshin_present(hwnd).get("ok")
    if save_shot:
        out["shot"] = save_shot("after_confirm")
    go1 = bool(cmdscreen._go_clear(hwnd))
    out["go_after_3s"] = go1
    if not go1:
        time.sleep(6.0)                        # 等武将台词框自己消失；**只再等一次**
        out["go_after_9s"] = bool(cmdscreen._go_clear(hwnd))
    if out["houshin_after"]:
        out["why"] = "点了「執行」但**方针窗还开着** ⇒ 命令没发出去 ⇒ 停手（不重复点）。"
        return out
    out["ok"] = True
    out["committed"] = True
    out["note"] = ("已提交出征命令（不可逆）。之后**再点一次「進行」**部队才会开始移动。")
    if not out.get("go_after_9s", out["go_after_3s"]):
        out["warn"] = "底栏还没回到 `請選擇命令起點` —— 可能是武将台词框还挂着（它会自己消失），不是失败。"
    return out


def step_abort_houshin(hwnd) -> dict:
    """方针窗 →「中止」→ **退回「選擇對象」表**（实测）。"""
    out: dict = {"step": "abort_houshin"}
    if not houshin_present(hwnd).get("ok"):
        out["ok"] = True
        out["already"] = True
        out["note"] = "方针窗不在，没点。"
        return out
    winio.click_client(hwnd, HOUSHIN_ABORT_XY[0], HOUSHIN_ABORT_XY[1], settle=0.35)
    time.sleep(1.6)
    tp = targets_present(hwnd)
    out["targets"] = tp
    if not tp.get("ok"):
        out["why"] = "点「中止」后**没回到「選擇對象」表**（%s）⇒ 停手。" % tp.get("why")
        return out
    out["ok"] = True
    return out


def stage_of(hwnd, bgr=None) -> str:
    """判定**出征链路走到哪一层**（只读）。

    ⚠️ 顺序有讲究：
       ① **先判 `go` 锚点** —— 底栏写着 `請選擇命令起點` 就是干净战略面，
          这是项目里最硬的锚点，别的判据都不该越过它
          （踩过：战略面上 `大將/資金/士氣` 那几行碰巧也亮 ⇒ `screen_present` 误报
           `deploy_screen`）；
       ② `targets` 必须在 `officer_picker` **之前** 判
          （两者都是一块大暗面板；靠底栏提示区分）。
    """
    if bgr is None:
        bgr = ocr._grab_bgr(hwnd)
    if cmdscreen._go_clear(hwnd):
        return "strategic"
    if dialog_present(hwnd, bgr).get("ok"):
        return "number_dialog"
    if houshin_present(hwnd, bgr).get("ok"):
        return "houshin"
    if targets_present(hwnd, bgr).get("ok"):
        return "targets"
    if picker_present(hwnd, bgr).get("ok"):
        return "officer_picker"
    if screen_present(hwnd, bgr).get("ok"):
        return "deploy_screen"
    return "unknown"


def step_back_out(hwnd, max_steps: int = 5) -> dict:
    """**分级退屏**：把出征链路从任何一层退回干净战略面。

    ⛔ 为什么需要它：**`atoms.recover` 退不出方针窗**（实测 2026-09-20：
       它只会 Esc，而方针窗不吃 Esc），结果 `_fail_and_clean` 报"界面没清理干净"，
       把游戏停在出征屏上要人接手。

    ⭐ 每一层用**该层已验证过的**那一个动作：

    | 层 | 动作 | 已验证的去向 |
    |---|---|---|
    | `houshin` | 点「中止」(798,732) | → 目标表 |
    | `targets` | Esc | → 出征屏 |
    | `deploy_screen` | Esc | → 干净战略面 |
    | `officer_picker` | Esc | → 出征屏 |
    | `number_dialog` | 点「中止」(805,772) | → 出征屏 |

    ⛔ 每次只动一下、动完**重判层**；**屏幕没变就停手**（绝不重复试）。
    ⛔ 层判不出来（`unknown`）也停手，如实报告。
    """
    out: dict = {"step": "back_out", "actions": []}
    for _ in range(max(1, max_steps)):
        if cmdscreen._go_clear(hwnd):
            break
        st = stage_of(hwnd)
        if st == "strategic":
            break
        if st == "unknown":
            out["actions"].append({"at": st, "did": "nothing"})
            break
        before = _grab_int(hwnd)
        if st == "houshin":
            winio.click_client(hwnd, HOUSHIN_ABORT_XY[0], HOUSHIN_ABORT_XY[1], settle=0.35)
        elif st == "targets":
            winio.press("escape")
        elif st == "deploy_screen":
            winio.press("escape")
        elif st == "officer_picker":
            winio.press("escape")
        elif st == "number_dialog":
            winio.click_client(hwnd, NUM_ABORT_XY[0], NUM_ABORT_XY[1], settle=0.35)
        else:                                        # pragma: no cover
            break
        time.sleep(1.5)
        after_st = stage_of(hwnd)
        changed = float(np.abs(_grab_int(hwnd) - before).mean())
        out["actions"].append({"at": st, "now": after_st, "changed": round(changed, 2)})
        if changed < 0.6 and after_st == st:
            out["actions"][-1]["note"] = "这一下屏幕没变 ⇒ 停手，不再重复"
            break
    out["ok"] = bool(cmdscreen._go_clear(hwnd))
    out["stage_end"] = stage_of(hwnd)
    if not out["ok"]:
        out["why"] = ("没能回到干净战略面（停在 %s）—— 请人看一眼屏幕"
                      "（可手动按 Esc / 点「中止」）。" % out["stage_end"])
    return out


def step_abort(hwnd) -> dict:
    """把界面收回**干净战略面**（只读 / dry_run 的收尾）。

    ⭐ 先走**本模块的分级退屏** `step_back_out`（懂方针窗/目标表/数字对话框），
       它没成功再退给 `atoms.recover`（Esc 优先、零坐标）。
    """
    from san9 import atoms
    out: dict = {"step": "abort"}
    bo = step_back_out(hwnd)
    out["back_out"] = {k: v for k, v in bo.items() if k in ("ok", "actions", "stage_end", "why")}
    if bo.get("ok"):
        out["ok"] = True
        out["how"] = "stage_back_out"
        return out
    try:
        r = atoms.recover(hwnd) or {}
    except Exception as e:                                   # pragma: no cover
        out["why"] = "分级退屏没成功，recover 又抛异常：%s" % e
        return out
    out["recover"] = {k: v for k, v in r.items()
                      if k in ("recovered", "how", "actions", "why", "shot")}
    # ⚠️ `atoms.recover` 的键是 **`recovered`** 不是 `ok`。
    out["ok"] = bool(r.get("recovered"))
    if not out["ok"]:
        out["why"] = ("分级退屏 + recover 都没把界面收回干净战略面"
                      "（停在 %s）⇒ 请人看一眼屏幕。" % bo.get("stage_end"))
    return out
