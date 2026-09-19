# -*- coding: utf-8 -*-
"""「人材 → 移動」—— **逐步**驱动。每个 `step_*` 恰好做一次改屏动作。

⛔⛔ 为什么没有 `run_move()` 这种一把梭
=====================================
用户 2026-09-19 的硬要求（同类问题已被制止三次）：
「**禁止写一次点几十下的长脚本**：中间一步错了后面全是连锁错点。一步一验。」

⇒ 本模块只提供 `step_*`，调用方（或 `scripts/move_step.py`）**一次调一步、看一眼再下一步**。
   唯一允许"一次按多下"的是**方向键**（不改变游戏状态，只挪高亮）——
   这与 `cmdscreen.open_command` / `cityintel.enter_menu_item` 的既有做法一致。

流程（⭐ 步骤是**帧证据**排出来的，⛔ 不是照着说明书推的）
=========================================================
| 步 | 动作 | 判据（底栏 / 按钮） | 证据 |
|---|---|---|---|
| 1 | 选右侧列表第 row 行 → 点屏幕正中 → 核实标题 | 左上情报面板标题 | `atoms.open_command_menu`（已验证）|
| 2 | 键盘：↓×2 选「人材」→ → → ↓×1 选「移動」→ Enter | 底栏 `移動我軍團的武將到對象設施` | `S27_move.jpg` |
| 3 | 点「**目標設施**」标签（**上面**那个蓝标签）| 底栏 `請選擇目標設施` + 右下 [一覽] | `S28_after_click_target.jpg` |
| 4 | 点「**一覽**」（青绿按钮）| 表格出现（`targetlist.check_target_list`）| `S30_yilan_list.jpg` |
| 5 | 方向键选行 → Enter | 回步骤 3 的屏，`目標設施` 填上 | `S31/S32` |
| 6 | 点「**執行武將**」标签（**下面**那个蓝标签）| 底栏 `請選擇執行武將` | `S33_move_picker.jpg` |
| 7 | 点武将行（单选）| 弹窗关、回命令界面 | `S35/S37` |
| 8 | 点绿色「執行」| ⛔ **未验** —— `S48_move_executed` 之后是什么没拍到 | — |

⭐ **与「軍事 → 輸送」的关键差别**（别把两者混为一谈）
-----------------------------------------------------
    輸送：選武將 → 士兵數量 → 執行 → 目標列表
    移動：目標設施 → 執行武將 → 執行

顺序**相反**。所以 `targetlist.open_via_transport()` **不能复用**；
能复用的只有「目標列表」那一段（`parse_target_list` / `select_target` / `close_transport`），
而且该段的版面常量 **在两屏上完全相同**（2026-09-19 现量，见
`scripts/probe_move_flow.py` 的输出：两帧都是 ROW0_Y=304 / 行距 30.0 / 15 行）。
"""
from __future__ import annotations

import time

from . import atoms, cmdscreen, ocr, targetlist, winio

MAIN_INDEX = 2      # 主菜单第 2 项 = 人材（設施0 軍事1 人材2 計略3 外交4 任免5）
SUB_INDEX = 1       # 人材子菜单第 1 项 = 移動（召來0 移動1 探索2 登庸3）

HINT_MOVE_SCREEN = "移動我軍團的武將到對象設施"
"""↓×2 → → ↓×1 之后、**按 Enter 之前**的底栏说明字（`S27_move.jpg` 现量）。

⛔⛔ **不许用"子串精确匹配"判它** —— 2026-09-19 真机第一课，同一句话两次读出：
    `移勤我率围的武群到好象设施。` · `移動我军图的武将到谢象設施。`
    **繁简混排（動+军）＋ 每两三个字错一个**。精确锚点必然假阴性。
    ⇒ 见 `hint_hit()`：用**字符包含率**，⛔ 不是子串。"""

HINT_TARGET_MODE = "請選擇目標設施"
"""点了「目標設施」之后的底栏（`S28` 现量 `請選擇目標設施。`）。
⚠️ 与輸送那一屏的 `請選擇目標。` **不同** ⇒ 锚点不能混用。"""

HINT_PICK_OFFICER = "請選擇執行武將"
"""点「執行武將」之后的底栏 —— 与輸送同一句。⚠️ 也改用 `hint_hit` 判。"""

HINT_FUZZY_THR = 0.5
"""字符包含率阈值。实测量到的两档（离线和真机都算过）：
  · 命中：`移動我军图的武将到谢象設施` vs 期望 14 字 → **9/14 = 0.64**
  · 误命中：`輸送士兵至自勢力的設施`  vs 期望 14 字 → 3/14 = 0.21
⇒ 0.5 落在两者正中间，两边都有余量。⛔ 别调到 0.3 以下（会把輸送的说明吸进来）。"""


def hint_hit(text: str, expected: str, thr: float = HINT_FUZZY_THR) -> float:
    """**字符包含率** —— 期望串里的汉字有多少出现在读到的文字里。返回 0..1。

    ⚠️⚠️ **只用它当"人看的线索"，⛔ 不用它当门禁** —— 2026-09-19 真机 + 离线双侧实测，
       两头的失败都撞到了：

       | 情况 | 例子 | 比率 | 结论 |
       |---|---|---|---|
       | 假阳性 | `請選擇目標。`（輸送的说明）vs 期望 `請選擇目標設施` | **0.71** | 分不开 |
       | 假阴性 | `请遥择目设施遥择目標设施`（S30 帧 OCR）vs 同上 | **0.43** | 分不开 |

       根因：底栏 OCR **繁简混排**（`设施` vs `設施`）**且逐字读错**（`移動`→`移勤`、
       `對象`→`好象`、`選`→`遥`）。⇒ **任何基于文字的精确/模糊判据都不可靠。**

    ⇒ 门禁一律改用**结构性判据**（蓝标签个数 / 青绿按钮 / 表格门禁 / 弹窗行数），
       那些是程序化量出来的，与 OCR 无关。见 `looks_like_move_screen` / `in_target_mode`。
    """
    t = "".join(ch for ch in (text or "") if "\u4e00" <= ch <= "\u9fff")
    want = [ch for ch in expected if "\u4e00" <= ch <= "\u9fff"]
    if not t or not want:
        return 0.0
    return sum(1 for ch in want if ch in t) / len(want)


# ══════════════════════════════════════════════════════════════════════
# 结构性判据（⭐ 门禁一律用这些，⛔ 不用底栏文字）
# ══════════════════════════════════════════════════════════════════════

N_LABELS_MOVE = 2
"""「移動」命令界面在**该城本旬可下令武将 ≥ 2** 时的彩色标签个数（= 2）。

⚠️ 这只是**上界**，不是恒等式 —— 见 `LABEL_RANGE`。"""

LABEL_RANGE = (1, 2)
"""彩色标签个数的**合法区间**（含端点）。为什么是 1~2：

| 情形 | `目標設施` | `執行武將` | 彩色标签数 |
|---|---|---|---|
| 可下令武将 **≥ 2** | 彩色 | **彩色**（可点 ⇒ 要手动选）| **2** |
| 可下令武将 **= 1** | 彩色 | **灰的**（游戏已自动填好，不需要点）| **1** |

⛔⛔ 2026-09-19 真机（平原，只剩 1 人）：门禁当时写死"恰好 2 个"
⇒ **把正确状态判成失败**，还报成 `command_unavailable`（"这座城没有可行动的武将"）
—— 完全说反了：不是没武将，是**只有一个、游戏替他选好了**。
（用户当时就点出了这条通例，见 docs/04 §12.10。）

⇒ 光看个数不够 ⇒ 再加一条**共现判据**：命令界面上必须有红色「中止」按钮
（还停在菜单上时没有它）。两条一起才认。
"""

LABEL_W_RANGE = (60, 200)
"""蓝色标签的**宽度**范围（像素）。2026-09-19 真机 + 帧现量：

| 标签 | 宽 |
|---|---|
| `目標設施`（S27） | 110 |
| `執行武將`（S27） | 108 |
| 真机实测 | 110 · 116 · 111 |

⛔ 加这条是因为**光靠"数蓝块"会假阳性**：干净战略面的地图上有蓝色水域/装饰，
   实测在 `TAB_COLOR_BAND` 里也能凑出 2 块（(679,361) 与 (659,379)）。
   ⚠️ 但**它挡不住对话框标题横幅**（宽 116，与真标签同量级）⇒ 另有 `TARGET_LABEL_Y_MIN`。
"""


def looks_like_move_screen(hwnd) -> dict:
    """按**结构**判断现在是不是「移動」命令界面。返回证据，不做动作。

    ⭐ 三道判据**都要过**（2026-09-19 真机逐条踩出来的）：

      1. `cmdscreen._go_clear(hwnd)` **不成立**
         —— 已验证的"干净战略面"判据。底栏出现 `請選擇命令起點` 就说明根本没开命令界面，
         ⛔ 这时候数彩色块没意义（地图上有蓝色水域）。
         ⚠️ 这一条是踩出来的：提交完 移動、刚回战略面时，只数块的版本报了 `ok=true`
         （读到 (679,361)/(659,379)）—— **假阳性**。
      2. 彩色标签个数落在 `LABEL_RANGE`（1~2），且每个**宽度**在 `LABEL_W_RANGE`
      3. **存在红色「中止」按钮**（`MOVE_BTN_BAND`）—— 菜单开着时没有它，
         用它把"停在菜单上"与"进了命令界面"分开
    """
    try:
        if cmdscreen._go_clear(hwnd):
            return {"ok": False, "n_labels": 0, "go_clear": True,
                    "why": "底栏 go 锚点成立 ⇒ 这是**干净战略面**，不是命令界面"}
    except Exception:
        pass
    bgr = ocr._grab_bgr(hwnd)
    if bgr is None:
        return {"ok": False, "why": "抓不到画面", "go_clear": False}
    w0, w1 = LABEL_W_RANGE
    labels = [d for d in cmdscreen.find_blue_labels_in(bgr)
              if w0 <= (d["x1"] - d["x0"]) <= w1]
    # ⭐ 必须排掉**对话框标题横幅** —— 它满足"蓝/紫色系 + 高饱和"，也在 `TAB_COLOR_BAND` 里。
    #    实测（平原·探索）读到 3 个：(459,308) 宽116 · (455,321) 宽102 · (440,430) 宽111，
    #    前两个是标题横幅「探索」，只有第三个是 `目標地域`（见 §12.11）。
    #    ⛔ 宽度过滤挡不住它（116 vs 111 同量级），只能用 y。
    labels = [d for d in labels if d["y0"] >= cmdscreen.TARGET_LABEL_Y_MIN]
    red = cmdscreen.find_button_in(bgr, "red", *MOVE_BTN_BAND)
    n0, n1 = LABEL_RANGE
    r = {"n_labels": len(labels), "go_clear": False, "red_abort": red,
         "labels": [{"x": d["x"], "y": d["y"], "w": d["x1"] - d["x0"]} for d in labels],
         "expect": list(LABEL_RANGE)}
    r["ok"] = (n0 <= len(labels) <= n1) and (red is not None)
    if not r["ok"]:
        bits = []
        if not (n0 <= len(labels) <= n1):
            bits.append("彩色标签 %d 个（期望 %d~%d）" % (len(labels), n0, n1))
        if red is None:
            bits.append("找不到红色「中止」按钮（说明**没进命令界面**，多半还停在菜单上）")
        r["why"] = "；".join(bits) + " ⇒ 不像「移動」命令界面。"
    return r


def hint_hit_ok(text: str, expected: str) -> bool:
    """⛔ **已废弃为门禁** —— 保留仅为兼容旧调用。新代码一律用结构性判据。"""
    return hint_hit(text, expected) >= HINT_FUZZY_THR

BTN_BAND = (840, 915, 1000, 1270)
"""右下按钮带。[一覽](青绿) 与 [中止](红) 都在这条带里（`S28` 现量 y≈855..894）。"""


def hint(hwnd) -> str:
    bgr = ocr._grab_bgr(hwnd)
    return targetlist.bottom_hint(bgr) if bgr is not None else ""


# ══════════════════════════════════════════════════════════════════════
# 步 1+2：开到「移動」命令界面
# ══════════════════════════════════════════════════════════════════════

def step_open_screen(hwnd, row: int, expect: str | None = None,
                     sub_index: int = SUB_INDEX, name: str = "移動",
                     gate: bool = True, menu_index: int | None = None) -> dict:
    """选列表第 `row` 行 → 开命令菜单 → 键盘走到「人材 → 第 sub_index 项」→ Enter。

    参数化是为了让**同一个子菜单的别的项**复用（`探索` = 同一屏第 2 项）。
    ⭐ **`menu_index` 也要传** —— 主菜单 6 项：設施0 **軍事1** 人材2 計略3 外交4 任免5。
       ⛔ 默认 `None` 会当成人材(2)，别的类别不传就会走错菜单。
    ⛔ 判据不许改成"按名字断言" —— 底栏文字 OCR 不可靠（见 §12.5），
       一律用**结构判据**（命令界面的蓝标签个数）。

    `gate=False` = **只报告不过门**（给"第一次侦察某个界面"用）。
    """
    out: dict = {"step": "open_screen", "row": row, "expect": expect,
                 "sub_index": sub_index, "name": name}
    m = atoms.open_command_menu(hwnd, row, expect)
    out["menu"] = {k: m.get(k) for k in ("ok", "commanded", "why", "clicked")}
    if not m.get("ok"):
        out["why"] = "命令菜单没开成（%s）⇒ 没往下走" % (m.get("why") or "?")
        return out
    out["commanded"] = m.get("commanded")

    # 键盘：人材(2) → 展开 → 第 sub_index 项 → Enter（方向键只挪高亮；Enter 前有底栏门禁）
    # ⭐ `submenu_gate=False` —— 本函数有自己的**硬后置门**（`looks_like_move_screen`），
    #   不需要 `open_command` 那道**启发式**前置门（「子菜单读 0 项就不按 Enter」）。
    #   ⛔ 2026-09-19 真机：那道门在「人材」子菜单上**假阴性** ——
    #   底栏明明写着「移動我軍團的武將到對象設施」，它却因为读 0 项拒绝按 Enter，
    #   整条链失败、还报成"走菜单失败"，把人往"定位不对"的方向带。
    # ⚠️ `menu_index=None` ⇒ 用人材(2)。**别的类别必须显式传** ——
    #    2026-09-20 踩到：出征(`軍事`=1) 直接复用了本函数，结果走进了 `人材→召來`，
    #    界面完全不对，还报成"不是出征屏"（症状离病因很远）。
    oc = cmdscreen.open_command(hwnd, None, sub_index=sub_index,
                                menu_index=MAIN_INDEX if menu_index is None else menu_index,
                                submenu_gate=False)
    out["nav"] = {k: oc.get(k) for k in ("ok", "hint_before_enter", "item",
                                          "hint_blocked", "sub_read",
                                          "submenu_gate_skipped")}
    if not oc.get("ok"):
        out["why"] = ("键盘走到「人材 → 第 %d 项（%s）」被门禁挡住：%s ｜ 底栏 %r。"
                      "（`hint_blocked` = 游戏自己写了「不能做」的原因；"
                      "`sub_read` = 子菜单项读了几项）"
                      % (sub_index, name,
                         oc.get("hint_blocked") or oc.get("sub_read")
                         or oc.get("reason_code") or "?",
                         oc.get("hint_before_enter")))
        return out

    h = hint(hwnd)
    out["hint"] = h
    labels = cmdscreen.find_blue_labels_in(ocr._grab_bgr(hwnd))
    w0, w1 = LABEL_W_RANGE
    labels = [d for d in labels if w0 <= (d["x1"] - d["x0"]) <= w1]
    out["labels"] = {"n": len(labels),
                     "at": [{"x": d["x"], "y": d["y"], "w": d["x1"] - d["x0"]}
                            for d in labels]}
    if not gate:
        out["ok"] = True
        out["note"] = "只报告不过门（gate=False）—— 这是第一次侦察这个界面"
        return out
    # ⭐ 门禁 = `looks_like_move_screen` 的**三信号**（go 锚点否决 + 标签数 1~2 + 红中止按钮）。
    #    ⛔ 不用"恰好 N 个标签" —— 那会在「本旬只剩 1 人、游戏自动选好」时假阴性
    #    （2026-09-19 真机：报成 `command_unavailable`，把"只有一个武将"说成"没有武将"）。
    ls = looks_like_move_screen(hwnd)
    out["labels"] = ls
    if not ls.get("ok"):
        out["why"] = ("进了命令界面，但结构判据不过：%s ⇒ **不确认成功**"
                      "（底栏读到 %r，只当线索）。" % (ls.get("why"), h))
        return out
    out["ok"] = True
    return out


# ══════════════════════════════════════════════════════════════════════
# 步 3：点「目標設施」标签 → 進入「選擇設施」地图模式
# ══════════════════════════════════════════════════════════════════════

def step_target_mode(hwnd) -> dict:
    """点「**目標設施**」标签（**上面**那个蓝标签），进入选择目标模式。

    ⛔ **前置门**：先确认底栏是「移動」的说明字。
      因为 `find_target_label` 取的是"最上面那个蓝标签"—— 在**别的**命令界面上
      （那里只有「執行武將」一个蓝标签）它会取到「執行武將」，
      **点下去会弹出选将窗**（凭空多开一层），而不是进选择目标模式。
    """
    out: dict = {"step": "target_mode"}
    h0 = hint(hwnd)
    out["hint_before"] = h0
    ls = looks_like_move_screen(hwnd)
    out["labels_before"] = ls
    if not ls.get("ok"):
        out["why"] = ("前置门：结构判据说这不是「移動」界面（%s）⇒ **一个像素都没点**。"
                      "⛔ 在别的命令界面上点「最上面那个蓝标签」会点到「執行武將」"
                      "（凭空多开一层选将窗）。" % ls.get("why"))
        return out
    xy, how = cmdscreen.find_target_label(hwnd)
    out["label"] = {"xy": xy, "how": how}
    if xy is None:
        out["why"] = ("找不到「目標設施」蓝标签（%s）⇒ **一个像素都没点**。"
                      "可能不在「移動」命令界面上。" % how)
        return out
    winio.click_client(hwnd, xy[0], xy[1], settle=0.35)
    time.sleep(1.5)
    h = hint(hwnd)
    out["hint"] = h
    out["hint_ratio_targetkey"] = round(hint_hit(h, HINT_TARGET_MODE), 2)
    out["clicked"] = list(xy)
    # ⭐ 后置门：进"选择目标"模式。**表格 / 地图两种形态都算**
    #    （2026-09-19 真机更正：点「目標設施」这次**直接**出表，不是地图 —— 我原来写错了）
    tm = in_target_mode(hwnd)
    out["target_mode"] = tm
    if not tm.get("ok"):
        out["why"] = ("点了「目標設施」之后**既没出选择设施表、右下也没有青绿按钮** ⇒ "
                      "没进选择目标模式（底栏读到 %r，只当线索）。" % h)
        return out
    out["ok"] = True
    return out


# ══════════════════════════════════════════════════════════════════════
# 步 4：点「一覽」→ 目标列表
# ══════════════════════════════════════════════════════════════════════

TEAL_BANDS = (
    (760, 920, 640, 800),       # 表格形态 [地圖]：x≈655..730 y≈772..806（真机现量）
    (830, 920, 1000, 1200),     # 地图形态 [一覽]：x≈1047..1144 y≈855..894（`S28` 现量）
)
"""青绿按钮的搜索带 —— **两个形态各一条**。

⛔ 不能只写一条：表格形态的 [地圖] 在 y≈788、x≈690，地图形态的 [一覽] 在
   y≈875、x≈1095 —— **x 差 400px**，任何单条带都会在另一个形态上 miss。
"""


def find_teal_button(hwnd, band_index: int | None = None) -> tuple[tuple[int, int] | None, str]:
    """找青绿按钮（`一覽` / `地圖`）。

    ⛔ 判据是**颜色**（`_color_mask(cyan)`），不是按钮上的字。
    ⚠️ **`band_index` 要按形态指定，⛔ 不要"两条带依次碰运气"** ——
       2026-09-19 真机：地图形态下 `TEAL_BANDS[0]`（表格形态那条带）会先命中
       **地图上的青绿水域**，于是点到了地图上、什么也没发生
       （连轮询 6.2 秒也没用，因为**根本没点到按钮**）。
       ⇒ 地图形态只查 `TEAL_BANDS[1]`（实测 `S28` 的 [一覽] 在 (1095,875)）。
    """
    bgr = ocr._grab_bgr(hwnd)
    if bgr is None:
        return None, "no_frame"
    idxs = [band_index] if band_index is not None else list(range(len(TEAL_BANDS)))
    for i in idxs:
        xy = cmdscreen.find_button_in(bgr, "cyan", *TEAL_BANDS[i])
        if xy is not None:
            return xy, "color_cyan@band%d" % i
    return None, "no_teal_button"


def teal_candidates(hwnd) -> list:
    """（取证用）两条带各找到什么 —— 失败时把证据带回去。"""
    bgr = ocr._grab_bgr(hwnd)
    if bgr is None:
        return []
    return [{"band": list(TEAL_BANDS[i]),
             "found": cmdscreen.find_button_in(bgr, "cyan", *TEAL_BANDS[i])}
            for i in range(len(TEAL_BANDS))]


BAND_MAP = 1
"""地图形态用的带下标（`一覽`，实测 (1095,875)）。"""


def in_target_mode(hwnd) -> dict:
    """现在是不是「選擇目標」模式？**两种形态都算**（2026-09-19 真机更正）。

    ⭐ 这一条是**被真机打脸之后**写的，原稿只认"地图形态"，结果假阴性：

    | 形态 | 结构签名 | 证据 |
    |---|---|---|
    | **表格形态** | 15 行选择设施表（`targetlist.check_target_list`）＋右下 **[地圖]** 青绿按钮 | ✅ 真机 2026-09-19（点「目標設施」**直接**出表）|
    | **地图形态** | 地图 ＋ 右下 **[一覽]** 青绿按钮 | ✅ `S28_after_click_target.jpg` |

    ⇒ **同一屏会记住上次用的形态** —— S 系列那次进的是地图，这次进的是表格。
      ⛔ 所以**不许把「一定是地图」写进流程**（我原来就写错了）。
    """
    bgr = ocr._grab_bgr(hwnd)
    if bgr is None:
        return {"ok": False, "why": "抓不到画面"}
    gate = targetlist.check_target_list(bgr)
    btn, how = find_teal_button(hwnd)
    r = {"table_gate": bool(gate.get("ok")), "teal_button": btn, "how": how,
         "hint": targetlist.bottom_hint(bgr)}
    r["ok"] = bool(gate.get("ok")) or (btn is not None)
    if not r["ok"]:
        r["why"] = ("既没有 15 行选择设施表、右下也没有青绿按钮 ⇒ 不在选择目标模式"
                    "（表格门禁说：%s）" % gate.get("why"))
    r["form"] = ("table" if gate.get("ok") else ("map" if btn else None))
    return r


def step_open_list(hwnd) -> dict:
    """确保目标是**表格形态**（如果现在是地图形态，点青绿 [一覽] 切过去）。

    ⭐ 2026-09-19 真机更正：点「目標設施」**可能直接就是表格**（这次就是），
      也可能先进地图（`S28`）。⇒ 这一步要**先看形态**：
        已是表格 → **零动作**返回 ok（⛔ 不许为了"走流程"白点一下青绿按钮）
        是地图   → 点 [一覽]
    """
    out: dict = {"step": "open_list"}
    tm = in_target_mode(hwnd)
    out["target_mode_before"] = tm
    if not tm.get("ok"):
        out["why"] = "不在选择目标模式（%s）⇒ **一个像素都没点**。" % tm.get("why")
        return out
    if tm.get("form") == "table":
        out["ok"] = True
        out["note"] = "已经是表格形态 ⇒ 零动作（没点任何东西）"
        return out

    # ⭐ **只查地图形态那条带** —— 表格形态那条带在地图形态下会命中青绿水域（假阳性）
    xy, how = find_teal_button(hwnd, band_index=BAND_MAP)
    out["button"] = {"xy": xy, "how": how}
    out["candidates"] = teal_candidates(hwnd)
    if xy is None:
        out["why"] = ("地图形态但在它那条带里找不到青绿 [一覽] 按钮 ⇒ **不点**"
                      "（⛔ 不回退去试表格形态那条带 —— 那条带上可能是地图的青绿水域）。"
                      "各带找到的：%s" % out["candidates"])
        return out
    winio.click_client(hwnd, xy[0], xy[1], settle=0.35)
    time.sleep(1.4)
    # ⛔⛔ **门禁必须轮询，不能一帧定生死** —— 这是 `targetlist.open_via_transport` 里
    #     记过的血案：点完「執行」后一覽屏其实已经开出来了，但只等 1.8s 就读，
    #     那一帧底栏还没画出来 ⇒ 把"成功"报成"没开到"，还白退了一次屏。
    #     ⚠️ 2026-09-19 本模块真机又犯一次：地图形态点 [一覽] 后一帧就判，
    #        报出"表格门禁没过"（底栏只命中 1 条、标题和表头都是空）—— **又是同一个坑**。
    gate, polls = None, []
    for i in range(7):                       # 1.4 + 6×0.8 ≈ 6.2s
        gate = targetlist.check_target_list(ocr._grab_bgr(hwnd))
        polls.append({"t": round(1.4 + 0.8 * i, 1), "ok": gate.get("ok"),
                      "hint": gate.get("hint")})
        if gate.get("ok"):
            break
        time.sleep(0.8)
    out["gate"] = {k: gate.get(k) for k in ("ok", "hint", "votes", "why")}
    out["gate_polls"] = polls
    out["clicked"] = list(xy)
    if not gate.get("ok"):
        out["why"] = ("点了 [一覽] 后**轮询 %.1f 秒**仍没进目标列表：%s"
                      % (polls[-1]["t"], gate.get("why")))
        return out
    out["ok"] = True
    return out


# ══════════════════════════════════════════════════════════════════════
# 步 5：选目标行 + Enter
# ══════════════════════════════════════════════════════════════════════

def select_list_row(hwnd, row: int) -> dict:
    """只把高亮移到第 `row` 行，**不按 Enter**。"""
    return targetlist.select_target(hwnd, row)


def step_confirm_target(hwnd) -> dict:
    """按 Enter 确认当前高亮的目标（⚠️ 这一步之后 `目標設施` 就填上了）。"""
    out: dict = {"step": "confirm_target"}
    bgr = ocr._grab_bgr(hwnd)
    gate = targetlist.check_target_list(bgr)
    out["gate_before"] = gate.get("ok")
    if not gate.get("ok"):
        out["why"] = "按 Enter 前复核：表格门禁没过（%s）⇒ **不按**。" % gate.get("why")
        return out
    hl = targetlist.highlight_row(bgr)
    out["highlight_row"] = hl.get("row")
    if hl.get("row") is None:
        out["why"] = ("按 Enter 前复核：判不出高亮在第几行（%s）⇒ **不按**，"
                      "按了等于赌。" % hl.get("why"))
        return out
    p = targetlist.parse_target_list(bgr, cities=None)
    cur = next((r for r in (p.get("rows") or []) if r.get("is_current")), None)
    out["highlight_raw"] = (cur or {}).get("raw")
    winio.press("enter")
    time.sleep(1.8)
    h = hint(hwnd)
    out["hint"] = h
    # 后置门：确认后应该**离开选择目标模式**，回到「移動」命令界面（蓝标签又变 2 个）
    tm2 = in_target_mode(hwnd)
    out["target_mode_after"] = tm2
    if tm2.get("ok"):
        out["why"] = ("按了 Enter，但**还在选择目标模式**（形态=%s）⇒ "
                      "没确认上（表格仍在 / 青绿按钮仍在）。" % tm2.get("form"))
        return out
    ls = looks_like_move_screen(hwnd)
    out["labels_after"] = ls
    if not ls.get("ok"):
        out["why"] = ("按了 Enter 之后既不在选择目标模式、也不像「移動」界面（%s）"
                      "⇒ 停手，由调用方决定。" % ls.get("why"))
        return out
    out["ok"] = True
    return out


# ══════════════════════════════════════════════════════════════════════
# 步 6：点「執行武將」→ 选将
# ══════════════════════════════════════════════════════════════════════

def picker_officers_raw(hwnd) -> list[dict]:
    """「選擇武將」弹窗里的候选武将名单（给"选谁"用）。

    ⛔⛔ **它判不了"弹窗开没开"** —— `cmdscreen.picker_officers` 在亮度剖面
       一行都检不出时，会**兜底编造 8 行**（`PICK_ROWS_Y0=413` 起步、行距 30）。
       2026-09-19 真机：回到「移動」命令界面（弹窗**已关**）时它仍返回 **8 行**
       ⇒ 拿它当"弹窗还开着"的判据会**把成功报成失败**。
       **判在不在请用 `picker_open()`**（命令界面蓝标签被盖住 = 弹窗在）。
    """
    try:
        return cmdscreen.picker_officers(hwnd) or []
    except Exception:
        return []


def step_open_picker(hwnd) -> dict:
    """点「**執行武將**」标签（**下面**那个蓝标签）→ 打开「選擇武將」弹窗。

    ⭐⭐ **两种正常结局**（用户 2026-09-19 指出，这是「人材/軍事」这类命令的**通例**）：

    | 该城本旬可下令人数 | 标签 | 结局 |
    |---|---|---|
    | ≥ 2 | **蓝色** | 点开弹窗 → 勾选 → 「決定」|
    | **= 1** | **灰色**（`how == "no_colored_tab"`）| ⛔ **不用点**：游戏已自动把他填进「執行武將」 ⇒ 直接去按「執行」|

    ⛔ 原稿把第二种当成**失败**报出来（"选将窗打不开"）—— 那是**误报**：
    真机（平原·探索）看到「執行武將」行已经填好裴元紹 + 政治 27，标签是因为
    "不需要你点"才置灰的。⇒ 置灰时返回 `auto_selected=True` 并且 **ok=True**，
    由调用方跳过选将、直接走「執行」判定（那一步是游戏自己给的硬信号）。

    门禁用**弹窗行数/蓝标签被盖住**（结构），⛔ 不用底栏文字 —— 见 §12.5 教训①。
    """
    out: dict = {"step": "open_picker"}
    if picker_open(hwnd):
        out["ok"] = True
        out["note"] = "弹窗**已经开着** ⇒ 零动作。"
        return out
    xy, how = find_officer_label(hwnd)
    out["label"] = {"xy": xy, "how": how}
    if xy is None:
        if how == "no_officer_label":
            # ⭐ 不是失败 ——「本旬只剩 1 名可下令武将」时游戏已自动选中
            out["auto_selected"] = True
            out["ok"] = True
            out["note"] = ("屏上只有 `目標設施` 一个彩色标签 ⇒ 「執行武將」是**灰的** "
                           "⇒ 按通例，本旬只剩 1 名可下令武将、**游戏已自动填好他** ⇒ "
                           "跳过选将，直接去按「執行」（变绿与否是游戏自己给的硬判据）。"
                           "⚠️ 本步**没有验证到底填了谁** —— 卡片上的名字不在返回值里。")
            return out
        out["why"] = ("在这屏上找不到「執行武將」标签（how=%s）⇒ **一个像素都没点**。"
                      % how)
        return out
    winio.click_client(hwnd, xy[0], xy[1], settle=0.35)
    time.sleep(2.0)
    if not picker_open(hwnd):
        out["hint"] = hint(hwnd)
        out["why"] = "点了「執行武將」但弹窗没开（命令界面还看得见）。"
        return out
    rows = picker_officers_raw(hwnd)
    out["officers"] = [p.get("name") for p in rows]
    out["n"] = len(rows)
    out["note"] = ("⚠️ 名单里的**名字**只读出一部分是正常的；选人一律按 `row`。"
                   "`picker_officers` 在检不出任何行时会**编造** 8 行（413 起步）"
                   "⇒ 这里的 `n` 不可信，**别拿它当「有几个人」**。")
    out["ok"] = True
    return out


def look_like_n(hwnd) -> int:
    """（小工具）当前画面按宽度过滤后的蓝标签个数 —— 只用于报错里带上证据。"""
    bgr = ocr._grab_bgr(hwnd)
    if bgr is None:
        return -1
    w0, w1 = LABEL_W_RANGE
    return len([d for d in cmdscreen.find_blue_labels_in(bgr)
                if w0 <= (d["x1"] - d["x0"]) <= w1])


def step_pick_officer(hwnd, row: int) -> dict:
    """在「選擇武將」弹窗里点第 `row` 行（**单选：点完自动回命令界面**）。

    门禁 = **弹窗仍然在**（多选，点一行不会关），⛔ 不用底栏文字。
    ⭐ 「移動」是**多选**：点一行只是勾上，弹窗**故意不关**。
       ⇒ 这一步的"成功"判据就是"勾完之后弹窗还在"；
       **真正的最终验证在 `step_execute`**（「執行」变绿 = 游戏自己承认选择有效）。
    """
    out: dict = {"step": "pick_officer", "row": row}
    if not picker_open(hwnd):
        out["why"] = "选将弹窗不在（命令界面蓝标签还在）⇒ **一个像素都没点**。"
        return out
    picks = picker_officers_raw(hwnd)
    out["officers"] = [p.get("name") for p in picks]
    out["n_read"] = len(picks)
    if row >= len(picks):
        out["why"] = ("row=%d 越界：名单读到 %d 行（%s）⇒ **一个像素都没点**。"
                      "⚠️ 读到的行数可能多于真实人数（见 `picker_officers_raw` 的兜底）。"
                      % (row, len(picks), out["officers"]))
        return out
    tgt = picks[row]
    winio.click_client(hwnd, cmdscreen.PICK_ROW_X, tgt["y"], settle=0.35)
    time.sleep(1.5)
    out["hint"] = hint(hwnd)
    out["clicked_y"] = tgt["y"]
    out["row_name_raw"] = tgt.get("name")
    if not picker_open(hwnd):
        out["why"] = ("点了第 %d 行之后**弹窗关了** —— 出乎预期"
                      "（「移動」是多选，正常应该还开着；单选的是「輸送」）⇒ "
                      "停下来由调用方判断。" % row)
        return out
    out["ok"] = True
    out["note"] = ("已勾选第 %d 行（y=%d，OCR 名字 %r）。"
                   "⚠️ **本次没验证「勾上了没有」** —— 多选弹窗的勾选状态没有可靠的结构判据；"
                   "最终验证靠下一步「執行」是否变绿（那是游戏自己给的信号）。"
                   % (row, tgt["y"], tgt.get("name")))
    return out


# ══════════════════════════════════════════════════════════════════════
# 步 7/8：执行
# ══════════════════════════════════════════════════════════════════════

PICKER_BTN_BAND = (690, 800, 780, 1010)
"""选将弹窗的 [決定](绿) / [中止](红) 按钮带。2026-09-19 真机现量：
绿 `決定` **(840, 743)** · 红 `中止` **(951, 743)** —— 见 `shots/move_picker.png`。"""

MOVE_BTN_BAND = (640, 800, 600, 900)
"""命令界面自己的 [執行](绿) / [中止](红) 按钮带 —— **两种命令界面都覆盖**（真机现量）：

| 界面 | 绿「執行」 | 红「中止」 |
|---|---|---|
| 移動 | (708, 702) | (818, 703) |
| 探索 | （未就绪时是灰的）| (818, 663) |

⇒ y 取 640..800（跨 663 与 702），x 取 600..900（跨 708 与 818）。
⛔ 与选将弹窗的 `PICKER_BTN_BAND`(840,743) **不是同一条**：x 上界 900 已把它排除，
   而且调用前会先用 `picker_open` 挡住"弹窗还开着"的情况。
"""


def find_officer_label(hwnd) -> tuple[tuple[int, int] | None, str]:
    """在**移动/探索命令界面**上找「執行武將」标签。找不到 = **游戏已自动选好**。

    ⛔⛔ 不能用 `cmdscreen.find_officer_tab` —— 它取"**最下面**那个彩色标签"，
       那是基于"这类界面上有两个彩色标签"的前提。而**本旬只剩 1 名武将**时
       `執行武將` 会**变灰**（游戏已自动填好），此时屏上**只剩 `目標設施` 一个彩色标签**
       ⇒ `find_officer_tab` 会把 **`目標設施`** 当成「執行武將」返回。
       2026-09-19 真机后果：点下去**又进了"选择目标"模式**（不是选将窗），
       而 `picker_open` 判"不是移动屏"就算弹窗开着 ⇒ 一路错到"找不到「決定」按钮"。

    ✅ 本函数的判据（只在本类界面上用，⛔ 不问 `設施` 命令那套）：

    | y>=`TARGET_LABEL_Y_MIN` 的彩色标签数 | 结论 |
    |---|---|
    | **≥ 2** | 下面那个就是「執行武將」⇒ 返回它的坐标 |
    | **= 1** | 只有 `目標設施` ⇒ 「執行武將」是灰的 ⇒ **返回 None（= 自动选好）** |
    | **= 0** | 不在命令界面上 ⇒ 返回 None |
    """
    bgr = ocr._grab_bgr(hwnd)
    if bgr is None:
        return None, "no_frame"
    w0, w1 = LABEL_W_RANGE
    labels = [d for d in cmdscreen.find_blue_labels_in(bgr)
              if w0 <= (d["x1"] - d["x0"]) <= w1 and d["y0"] >= cmdscreen.TARGET_LABEL_Y_MIN]
    if len(labels) < 2:
        return None, ("no_officer_label" if labels else "no_colored_tab")
    labels.sort(key=lambda d: d["y0"])
    lo = labels[-1]
    return (lo["x"], lo["y"]), "color_lowest"


def picker_open(hwnd) -> bool:
    """「選擇武將」弹窗在不在？

    ⛔ 判据**不能**只是"不是移动屏" —— 那会把"选择目标模式"也算成弹窗
       （2026-09-19 真机踩到，见 `find_officer_label`）。
    ✅ 两道一起：**既不是移动屏、也不在选择目标模式** ⇒ 是某种全屏覆盖层（弹窗）。

    ⚠️ 也⛔ 不用 `picker_officers` 判 —— 它一行检不出时会**兜底编造 8 行**，
       在"弹窗没开"时也会返回 8 行（§12.5 教训②）。
    """
    if looks_like_move_screen(hwnd).get("ok"):
        return False
    if in_target_mode(hwnd).get("ok"):
        return False
    return True


def step_confirm_officers(hwnd) -> dict:
    """在选将弹窗里点绿色「**決定**」—— 确认已勾选的武将，回「移動」命令界面。

    ⭐ **「移動」的选将是多选**（弹窗底部有 [總括選擇] / [總括解除] + [決定]），
       与 **「輸送」的单选**（点完自动回、无「決定」）**不一样**。
       2026-09-19 真机确认：点一个武将行之后弹窗**仍然开着**。
       （这也解释了帧序列里为什么会有 `S36_pickall` 这个名字。）
    """
    out: dict = {"step": "confirm_officers"}
    if not picker_open(hwnd):
        out["ok"] = True
        out["note"] = "弹窗已经不在 ⇒ 零动作"
        return out
    green = cmdscreen.find_button_in(ocr._grab_bgr(hwnd), "green", *PICKER_BTN_BAND)
    out["green"] = green
    if green is None:
        out["why"] = "选将弹窗里找不到绿色「決定」按钮 ⇒ **不点**。"
        return out
    winio.click_client(hwnd, green[0], green[1], settle=0.35)
    time.sleep(1.8)
    out["hint"] = hint(hwnd)
    ls = looks_like_move_screen(hwnd)
    out["labels_after"] = ls
    if not ls.get("ok"):
        out["why"] = ("点了「決定」之后**弹窗还在**（蓝标签 %d 个，期望 %d）⇒ 没确认上。"
                      % (ls.get("n_labels"), ls.get("expect")))
        return out
    out["ok"] = True
    return out


def step_execute(hwnd) -> dict:
    """点「移動」命令界面上绿色「執行」提交。

    ⛔ **未真机验证** —— 执行之后是什么屏还没拍到（`S48_move_executed` 之后缺帧）。
    """
    out: dict = {"step": "execute", "unverified": True}
    if picker_open(hwnd):
        out["why"] = ("选将弹窗**还开着**（命令界面蓝标签为 0）⇒ 先走「決定」，"
                      "⛔ 不点执行。")
        return out
    bgr = ocr._grab_bgr(hwnd)
    green = cmdscreen.find_button_in(bgr, "green", *MOVE_BTN_BAND)
    out["green"] = green
    if green is None:
        out["why"] = ("「移動」界面找不到绿色「執行」⇒ **武将没选上 / 目标没选上**"
                      "（这正是本判据的分辨力，选完才变绿）。⛔ 不点。")
        return out
    winio.click_client(hwnd, green[0], green[1], settle=0.35)
    time.sleep(1.8)
    out["hint"] = hint(hwnd)
    out["ok"] = True
    return out


def close(hwnd, max_esc: int = 3) -> dict:
    """退屏。复用语义相同的 `targetlist.close_transport`（判据 = 底栏 `go` 锚点）。"""
    r = targetlist.close_transport(hwnd, max_esc=max_esc)
    r["note"] = "移動 链的退屏复用 `close_transport`（Esc + go 锚点判据，与輸送同源）"
    return r
