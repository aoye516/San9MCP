# -*- coding: utf-8 -*-
"""原子能力层：**一步一个动作**，每步存图 + 返回屏幕证据，交给人/agent 看。

为什么要这一层（用户 2026-09-17 夜的要求）：
- "一旬一个固定策略脚本"不是玩法 —— 每旬用同一套策略，等于不会玩。
- 长程脚本一旦中间某步没生效，后面全是**连锁乱点**，而脚本自己看不见。

## 三条判据纪律（都是踩过的坑换来的）

1. **认设施要用名字，不能用行号。** 右侧列表的顺序会变 —— 实测同一个 `row=3`
   一次指到濮陽、下一次指到東阿港。所以对外一律用**名字**，内部动作前才现查行号。
2. **"我现在在指挥谁"看左上角设施情报面板的标题。** 实测极稳（3 区域 × 3 尺度
   全部读到「東阿港」）。⛔ 不要用右下「地域：X（Y）」—— 它跟**鼠标悬停**，
   不是选中项（选的是濮陽，它显示「頓丘（平原）」）。这个误判曾经导致连锁乱点。
3. **判"菜单开了"要用内容，不是面积。** 点情報后右侧面板刷新也能让那片区域
   变化 >8000 像素 → 假阳性。

## 用法（一次只调一个）

    python -m san9.cli atom --op state
    python -m san9.cli atom --op select  --facility 3
    python -m san9.cli atom --op menu    --facility 3
    python -m san9.cli atom --op issue   --facility 3 --cmd-index 0
    python -m san9.cli atom --op turn
    python -m san9.cli atom --op journal

`--facility N` 指的是**上一次 `state` 报出来的设施序号**（存在
`explore/atoms/last_facilities.json`），动作时才按名字现查行号 —— 这样序号在
两次调用之间保持稳定。中文不能走命令行，所以命令用 `--cmd-index`。
"""
import json
import os
import re
import time
from collections import Counter

import numpy as np
import cv2

from . import cmdscreen, cmdmenu, journal, lexicon, ocr, paths, winio

SHOT_DIR = os.path.join(paths.DATA, "explore", "atoms")
CACHE = os.path.join(SHOT_DIR, "last_facilities.json")
_seq = [0]

HINT_REG = cmdscreen.HINT_REG
"""底栏提示栏区域。**唯一实现在 `cmdscreen.HINT_REG` / `cmdscreen.read_hint`**，
这里只是转发，免得两份实现早晚不一致。"""


def hint(hwnd) -> str:
    """底部提示栏 —— 游戏**主动告诉我们**它在等什么 / 为什么不给做，最便宜的状态通道。

    ⚠️ 读不到就返回空串，**空串不要当判据**。

    ⭐ 2026-09-18 修好了一个大坑：老区域 `(0, 878, 1000, 960)` **什么都读不出来**
    （7 区域 × 5 尺度全扫描，老区域 5 个尺度全空）。换成 `cmdscreen.HINT_REG`
    之后稳定可读 —— 而这条栏会把**病因直接写出来**：
    北海本旬没有可执行武将时，它写着「沒有可執行的武將。」，命令全是灰的。
    """
    try:
        return cmdscreen.read_hint(hwnd)
    except Exception:
        return ""

FACILITY_CMDS = ["巡察", "商業", "開墾", "修築", "徵兵", "訓練", "買進", "賣出"]
"""「設施」子菜单 8 项，按菜单顺序。CLI 用 --cmd-index 引用（中文不能走命令行）。"""


# ------------------------------------------------------------------ 基础

_SESSION = [None]


def session_for(hwnd):
    """拿一个**已经 attach 过**的 GameSession。

    踩过：`end_turn` 内部 assert "还没 attach 到游戏" —— 因为我原来是
    `GameSession(mode='auto')` 然后手动塞 `s.hwnd = hwnd`，跳过了 attach，
    于是 self.drv 没建起来。这里统一走 attach()。
    """
    s = _SESSION[0]
    if s is None or getattr(s, "hwnd", None) != hwnd:
        from .session import GameSession
        s = GameSession(mode="auto")
        if not s.attach():
            raise RuntimeError("attach 失败（游戏没在运行？）")
        _SESSION[0] = s
    return s


def _grab(hwnd):
    w, h, buf = winio.grab(hwnd)
    return np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)[:, :, :3]


def _grab_int(hwnd):
    return _grab(hwnd).astype(int)


def _next_shot_path(tag: str) -> str:
    """给 `tag` 挑一个**不会被覆盖**的文件名。

    ⛔ 为什么要这个（2026-09-19 实测踩到）：
    `_seq` 是**进程级**计数器，每次新起进程都从 1 开始 —— 于是
    「新进程里第一次调 `san9_panel(row=1)`」永远写 `001_panel1_南皮.jpg`，
    **把上一次的证据原地覆盖掉**。而 `explore/atoms/` 既是留证目录，
    又是 `scripts/test_panel_parse.py` 的**离线回归 fixture 目录**：
    一次在线调用就能把回归基准悄悄换掉（实测 `001_panel1_南皮.jpg` 的 mtime
    从"历史"变成了"刚刚"，害我一度把"今晚的读数"当成"Sep-17 的历史样本"）。

    修法：**同名文件已存在就往下顺延**，让留证变成 append-only。
    代价只是文件名不再每次从 001 开始（这正是我们要的）。
    """
    os.makedirs(SHOT_DIR, exist_ok=True)
    while True:
        _seq[0] += 1
        p = os.path.join(SHOT_DIR, f"{_seq[0]:03d}_{tag}.jpg")
        if not os.path.exists(p) or _seq[0] > 9999:
            return p


def _shot(hwnd, tag: str) -> str:
    p = _next_shot_path(tag)
    cv2.imencode(".jpg", np.ascontiguousarray(_grab(hwnd)),
                 [int(cv2.IMWRITE_JPEG_QUALITY), 82])[1].tofile(p)
    return p


def commanded_facility(hwnd):
    """我现在在指挥哪个设施 —— 转发到 cmdscreen 的实现（同一份逻辑，别抄两遍）。"""
    return cmdscreen.commanded_facility(hwnd)


# ------------------------------------------------------------------ 失败必清理

RECOVER_MAX_ACTS = 2      # 最多两次**已定位**的动作；到顶就停手，绝不加码


def _recover_one_act(hwnd) -> dict:
    """**做且只做一次"已经程序化定位过"的动作**，返回做了什么（或为什么什么都没做）。

    ⛔⛔ 这里**严禁**出现"换个坐标再试"的循环。

    ⭐ 2026-09-19 重排：**Esc 提到第一位**（用户明确要求「键盘优先」，原话
    「这个不是靠 esc 就行了吗，现在都是 esc」）。旧版把 Esc 排在最后，理由是
    "真按下去的效果未知"——**那条理由已经过期**：

      · `winio.VK_ALIAS` 修好后 `press("escape")` 确实按得下去
        （血案见 `scripts/test_press_keyname.py`）
      · `targetlist.close_transport()` 已**真机验证**这条路：
        Esc×1 退一覽（变化 29.11）→ Esc×2 退輸送界面（变化 41.96，
        底栏回 `請選擇命令起點`）
      · ⭐ **用户 2026-09-19 确认：「ESC 能关掉都市命令菜单」**（原话）。
        这正是本文件旧注释里悬了很久的那句「**Esc 到底能不能关命令菜单，
        尚未重测**」—— 到此闭环。
        同一次还确认了另外三层（原话「任免选人和计略目标都可以 esc」、
        「2 可以 esc 退」）：
          · 任免：选人界面
          · 計略：选目标界面
          · 情報 → 「全部隊」全屏一览
        ⚠️ 出处是**用户手动验证**（用户自己在游戏里逐个试过），不是本项目的实测帧。
        都算"已验证"，但将来要复核时前两条有帧可查、这四条没有。
      · ⭐⭐ **通例（用户 2026-09-19 归纳，可直接依赖）**：
        「Esc 是**返回键**，Enter 是**确认键**，都是非常靠谱的」——
        Esc 不是"某个界面碰巧能退"，而是**游戏全局的"退一层"键**。
        ⇒ 遇到没点名的分层界面也**默认它能退**，不必逐屏预验。
        ⇒ 因此第 ① 步（Esc）**已覆盖所有已知分层界面**；② ③ 只是
          "Esc 竟然没生效"时的后备，实际几乎轮不到（docs/04 §11.1）。
        ⚠️ "默认能退"仍是**预期**：按完要用底栏 `go` 锚点**确认它真的退了**，
          ⛔ 不许"按了就当退了"。
      · ⛔ **常驻元素不适用**：右侧「移動列表」的 部隊/武將/設施 是**并列 tab**
        （用户确认：点击 = 切换列表，原地换内容、**不分层**）⇒
        它**没有"退屏"概念**，Esc 对它无意义。别把"切了 tab"当成"开了界面"，
        否则 recover 会去退一个不存在的层级（docs/04 §11.1 注 2）。
      · Esc 是**零坐标按键，不可能点错**；而 ② ③ 都是"按颜色/纹理猜位置"

    ⚠️ 判据用**锚点**（`cmdscreen._go_clear` → `go` 锚点 score≥0.8，
    即底栏回到 `請選擇命令起點`），⛔ **不是"按了几次"**，也不只看"变化量"。
    变化量只当"这一下有没有起作用"的辅助信号（防在同一状态上重复按）。

    顺序固定（四个候选，每个只做一次）：
      ① 按 Esc → 立刻用锚点判据验证
      ② 没干净 → 定位得到红色的「中止」按钮 → 点它
          （命令界面 / 選擇武將弹窗 的出口，实测都落 `CMD_BTN_BAND` 这条带里）
      ③ 否则**程序化定位**一个地图空白点 → 点它
          （原本是"关都市命令菜单"的路径；既然 Esc 就能关（见上），
            这条实际很少轮得到，但留着不影响 —— 它仍然是**已定位**的动作）
      ④ 全都不行 → **什么都不做**，如实返回原因
    """
    # ---- ① Esc 优先：零坐标、不可能点错 ----
    before = _grab_int(hwnd)
    try:
        winio.press("escape")
        time.sleep(1.2)
    except Exception as e:
        # ⛔ 按键出问题不许把 recover 带崩 —— 它本身是"失败后的兜底"，
        #    兜底自己炸掉会连累整局。
        return {"did": "nothing", "why": "按 Esc 抛异常：%s" % e}
    changed = float(np.abs(_grab_int(hwnd) - before).mean())
    clean = cmdscreen._go_clear(hwnd)
    if clean or changed > 0.6:
        return {"did": "press_escape", "changed": round(changed, 2),
                "go_clear": clean,
                "located_by": "无坐标按键（不可能点错）"}

    # ---- ② 红色「中止」按钮（程序化定位） ----
    pos = cmdscreen.find_button(hwnd, "red", *cmdscreen.CMD_BTN_BAND,
                                x0=700, x1=1010)
    if pos:
        winio.click_client(hwnd, pos[0], pos[1], settle=0.5)
        time.sleep(1.2)
        return {"did": "click_abort", "at": list(pos),
                "located_by": "find_button(red)"}

    # ---- ③ 地图空白点（程序化定位） ----
    pt = cmdscreen.locate_blank_map_point(hwnd)
    if pt.get("found"):
        winio.click_client(hwnd, pt["x"], pt["y"], settle=0.35)
        time.sleep(0.9)
        return {"did": "click_blank_map", "at": [pt["x"], pt["y"]],
                "located_by": "locate_blank_map_point", "std": pt.get("std")}

    # ---- ④ 停手 ----
    return {"did": "nothing",
            "locator_said": pt.get("why"),
            "why": "按 Esc 屏幕毫无变化；也定位不到红色「中止」按钮或足够平坦的地图空白点。"
                   + ("定位器说：%s" % pt.get("why") if pt.get("why") else "")}


def recover(hwnd, tries: int = RECOVER_MAX_ACTS) -> dict:
    """**失败必清理** —— 把界面恢复成干净战略面。

    为什么必须做（血泪）：`issue` 失败时**命令界面留在屏幕上没关**，
    紧接着 `end_turn` 在"命令界面开着"的状态下跑 → 演出判据狂等 150 秒 → 整局崩。
    **一步失败会污染全局**，所以每个原子失败后都要调用它。

    ⛔⛔⛔ 2026-09-19 重写（用户当面制止，第三次犯同类错误）：
    旧实现是「Esc → 7 个硬编码落点 × 2 轮 → 4 个坐标带里找红按钮 × 3 轮」——
    **最坏一次调用会盲点约 45 次鼠标**，全落在按颜色猜的坐标上。
    **那段逻辑已整段删除。**

    现在的规矩（写死，别再犯）：
      0. **Esc 优先** —— 零坐标按键，不可能点错。先按一次，用锚点判据验证；
         有效就一步收工（2026-09-19 起，见 `_recover_one_act` 的说明）。
         带坐标的两条路降为后备。
      1. **每一次点击都必须先程序化定位**：按钮用 `find_button`（连通块 + 形状判据），
         地图空白点用 `locate_blank_map_point`（补丁方差判据）。
      2. **定位不到就不动手** —— 停手、留图、如实报告"我看到什么"。
      3. **允许对同一类出口重复**（实测退出要点两次「中止」），
         但**禁止在定位失败时换一个坐标再试**。
      4. 上限 `tries` 次（默认 2），到顶返回失败，**绝不加码**。
      5. 判据是**锚点**（`cmdscreen._go_clear`：`go` 锚点 score≥0.8，即底栏回到
         `請選擇命令起點`），⛔ **不是"按了几次"**。
    """
    out: dict = {"recovered": False, "actions": []}
    for _ in range(max(1, tries)):
        if cmdscreen._go_clear(hwnd):
            break
        before = _grab_int(hwnd)
        act = _recover_one_act(hwnd)
        act["go_after"] = _go_score(hwnd)
        out["actions"].append(act)
        if act["did"] == "nothing":
            break                    # ⛔ 定位不到 → 立刻停手，绝不换坐标
        # ⛔ **屏幕一点没变 ⇒ 同一状态上再点一次是白点**（还会定位到同一个位置）。
        # 这是「不许重复试」的**可验证版本**：不看约定，看这一下到底有没有起作用。
        # 实测踩过：locate_blank_map_point 连点两次同一个 (996,688)，两次 go 都是 0.14。
        act["screen_changed"] = round(float(np.abs(_grab_int(hwnd) - before).mean()), 2)
        if act["screen_changed"] < 0.6:
            act["note"] = "这一下屏幕完全没变 → 停手，不再重复"
            break
    out["recovered"] = bool(cmdscreen._go_clear(hwnd))
    if out["recovered"]:
        out["how"] = out["actions"][-1]["did"] if out["actions"] else "already_clear"
        return out
    out["why"] = ("清理不干净，而且**我不会再自己想办法** —— 屏幕已经拍下来了，"
                  "请看清了再决定下一步（或人工按 Esc / 点「中止」）。")
    out["actions_done"] = [a.get("did") for a in out["actions"]]
    out["shot"] = _shot(hwnd, "recover_failed")
    return out


# ------------------------------------------------------------------ 原子 1：读状态

OFFICER_KEYS = ("在任", "俘虜", "在野")


# ============================== 左上「设施情报」面板 ==============================
#
# ⛔⛔ 2026-09-19 重写（D2：截图 17 项只读回 11、键名脏）。
# **面板布局是固定的** —— 所以用**行表**认字段，不用"猜格局"。
#
# 行位置哪来的（**程序化扫描，不是目测**）：
#   对两张已保存的全分辨率菜单截图（`001_menu1_南皮.jpg` / `001_menu3_北海.jpg`）
#   扫「标签列 x∈[88,160] 的亮像素行」→ 两张图的 18 个文字行**逐个像素一致**：
#       192(标题) 243 277 312 360 395 443 478 512 548 595 630 665 712 748 794 830 865
#   即：**17 个数据行 + 1 个标题行**，行距不匀（组间有隔断，35 / 47±1 交替）
#   ⇒ 任何"算行距 / 按 y 分桶"的写法都会错，必须用固定表。
#
# 旧的 PANEL_REG=(15,135,350,700) 有两个毛病：
#   ① 下沿只到 700 → **商人/市價/在任/俘虜/在野 五行整个读不到**（实测在 y≈712~865）
#   ② 右沿到 350 → x≈336 处会读进地图上的字（北海那张就漏进一个 `'00'`）
#   这两条曾在一个一次性脚本（`scripts/read_inren.py`，2026-09-19 已删）的注释里
#   被记过 —— 它在脚本里猴补了常量，**却一直没回到源文件**。教训：
#   **实测出来的常量修正必须当场写回定义处，写在调用方注释里等于没记。**
PANEL_REG = (15, 165, 330, 900)
"""左上设施情报面板的区域（含标题行）。上沿 165 / 右沿 330 / 下沿 900 都有实测出处。"""

PANEL_TITLE_Y = 192
"""面板标题行（城市名 + 等级）的 y 中心。"""

PANEL_ROWS: list[tuple[str, int]] = [
    ("民心", 243), ("收益", 277), ("收穫", 312), ("耐久", 360), ("士氣", 395),
    ("人口", 443), ("兵役人口", 478), ("士兵", 512), ("傷兵", 548),
    ("增加兵役", 595), ("資金收入", 630), ("兵糧收入", 665),
    ("商人", 712), ("市價", 748),
    ("在任", 794), ("俘虜", 830), ("在野", 865),
]
"""面板 17 个数据行的**固定** y 中心（实测，两城逐像素一致）。"""

PANEL_TOL = 17
"""行归并容差 = 最小心距(35) 的一半。超出这个距离的 OCR 条目算"野字"，丢弃。"""

PANEL_LABEL_X = 165
"""OCR 框**左沿**小于它 = 标签列；大于 = 数值列（实测标签左沿 ≤120、数值左沿 ≥174）。"""

PANEL_GRADES = ("一般", "重要", "要衝")
"""标题行右侧的等级（旧名 `PANEL_SKIP`，语义其实是"等级表"）。"""
PANEL_SKIP = PANEL_GRADES          # 向后兼容别名

PANEL_SCALES = (4, 5, 6, 7, 8)
"""多尺度：同一个字段多种放大都读一遍，**多数一致**才算数。

7/8 是 2026-09-19 加的。实测**没能**救回「傷兵/商人/俘虜/在野」那几个短值
（`0` / `—` 这种一两个字符的格子，引擎在任何倍数下都不报框）——
但它们在别的行上没有副作用，留着多两票。**别指望靠调倍数解决"读不出来"**，
那不是倍数问题，是通道问题（见下）。
"""

PANEL_VALUE_X = (200, 330)
"""数值列的 x 范围 —— 只用来**量墨迹**（判断"这格是真空还是读不出来"）。"""

PANEL_VALUE_PAT = {
    # 带斜杠的行 —— 形状必须形如 数字/数字
    "民心": r"\d+/\d+",
    "收益": r"\d+/\d+",
    "收穫": r"\d+/\d+",
    "耐久": r"\d+/\d+",
    "士氣": r"\d+/\d+",
    "在任": r"\d+/\d+",
    # `市價` 值是「兵糧 N/資金 M」，前缀**常被读花/读丢**（实测 `兵6/資金1`、`兵檀12/資金`）
    # ⇒ 只要求"斜杠两侧各至少有一个数字"，前缀是什么不管（前缀丢了也不影响数字可用）
    "市價": r"[^/]*\d[^/]*/[^/]*\d[^/]*",
    # ⚠️ `商人` 是**唯一一个值不是数字的行** —— 说明书 P.643 原文：
    #   「有商人在的都市將表示『駐在』。隨時都有商人在的都市，則將表示『常駐』。」
    # 所以 `駐在` / `常駐` 是**合法值**，不是垃圾。见 PANEL_TEXT_ROWS。
    "商人": r"(駐在|常駐|常驻|駐|在)",
}
r"""**值形状**白名单：形状不对就读数作废（当作"读不出来"）。

⛔ 为什么需要（2026-09-19 实测）：`在任` 的真值是 `4/ 4`（两数之间一个斜杠），
实测被 OCR 读成 **`47`** —— 斜杠丢了、两个数字粘成一个。
"47" 是个**看起来很正常的数字**，调用方会当成"47 个武将可下令"一路用下去
（正是 `_panel_ink` 注释里说的那种**静默错误**）。

⛔ 处理方式是**丢弃，不是修**：`47` 到底该还原成 `4/7` 还是本来就写错了，无法判定，
所以只能作废（原读数记进 `value_rejected` 留痕），**绝不猜值**。
跟 `ocr.TOPBAR_FIELDS` 的"只接受纯数字读数"是同一条纪律。

## 为什么从 1 行扩到 7 行（2026-09-19 07:xx，离线证据）

面板里**带斜杠的行不止 `在任`** —— 26 帧真实菜单截图离线重放（5 座城）显示，
这些行的**主导形状全部带斜杠**：
    民心 `390/1000` · 收益 `310/700` · 收穫 `412/600` · 耐久 `410/800` ·
    士氣 `53/100` · 市價 `兵糧7/資金1` · 在任 `5/5`
（斜杠是**固定布局**的一部分，不是数据形状 ⇒ 用"必须有斜杠"当守卫是安全的；
反过来，数字位数会随数据变（实测 收益 有 `###/###` 也有 `###/####`），
所以**只许写宽松的 `\d+/\d+`，不许写死位数**。）

证据与统计脚本：`scripts/dump_panel_values.py`（纯离线）。
"""

PANEL_TEXT_VALUES = {
    # 行 → 该行**所有**合法值（说明书 P.643）
    "商人": ("駐在", "常駐"),
}
"""描述性文字行的合法值全集 —— 用来算"读到的字像不像其中之一"。

⚠️ 这是 `PANEL_VALUE_PAT` 的**语义版**：正则管"收不收进 `values`"，
这张表管"没收进去的原文有多可信"。两者必须同时维护。
"""


def _text_row_similarity(lab: str, text: str) -> dict:
    """读到的文字与本行合法值的最优相似度 → `{best, ratio, verdict}`。

    ⛔ **为什么需要**：把"白名单没中也保留原文"这条放宽之后，
    非面板帧串进来的城名人名（`東阿` / `谢城壁`）也会落进 `values_text`。
    如果不区分，agent 就没法判断「`駐存` 是错字，`東阿` 是串台」。

    判据是**字符相似度**（`difflib`），不是"我觉得像"：
    - `>= 0.5` → `likely_typo`：与某个合法值高度重合 ⇒ 大概率 OCR 错字，**可用**
    - `< 0.5`  → `unrelated`：跟本行任何合法值都不沾 ⇒ **多半不是这一行的值**，别用
    """
    import difflib

    legal = PANEL_TEXT_VALUES.get(lab) or ()
    if not legal or not text:
        return {"best": None, "ratio": None, "verdict": "no_reference"}
    scored = [(difflib.SequenceMatcher(None, text, w).ratio(), w) for w in legal]
    scored.sort(reverse=True)
    ratio, best = scored[0]
    return {"best": best, "ratio": round(ratio, 2),
            "verdict": "likely_typo" if ratio >= 0.5 else "unrelated"}


PANEL_TEXT_ROWS = frozenset({"商人"})
"""值**本来就是文字、不含数字**的行 —— 这些行不受 `PANEL_VALUE_MUST_DIGIT` 约束。

⛔ **我 2026-09-19 在这里搞错过，用户当场纠正（"商人状态就是文字的"）。**

说明书 P.643 原文：「有商人在的都市將表示『**駐在**』。隨時都有商人在的都市，
則將表示『**常駐**』。」⇒ `駐在` / `常駐` 是**合法值**。

我当时立 `PANEL_VALUE_MUST_DIGIT` 时写的理由是「面板 17 行的合法值全都是含数字的」
—— **这句话本身是错的**，`商人` 行就不是。于是守卫把真读数当垃圾拦了
（`value_rejected={'商人':'駐在'}`），我还把它当成"守卫生效"的成功案例记进了文档。

**教训：立"通用规则"之前必须逐行核一遍说明书，不能从样本里归纳。**
样本里 `商人` 恰好多数是垃圾（非面板帧的城名落进来），
少数的 `駐在` 是真值 —— 靠统计分不开，靠说明书一句话就分开了。

## ⭐ 这类行的失败处理与数字行**不同**（2026-09-19 用户提出）

用户原话：「文字ocr就算有小错，应该也可以直接输出吧？Agent自己有语义理解能力应该能搞定的」。**对。**

分野不在"是文字还是数字"，在于**读错之后会发生什么**：

| | 这个值拿去干什么 | 读错的代价 | 处理 |
|---|---|---|---|
| **描述性文字**（本表：`商人`；另有事件正文 / 底栏 `hint` / 身分 / 学得） | agent **读来理解** | `駐在`→`駐存`，agent 照样知道有商人。**代价≈0** | **原文照给** + `value_uncertain` |
| **数字**（其余 16 行） | 算账、比较、做阈值判断 | `273302`→`25`，决策直接错。**代价高** | **作废**，`value=None` |
| **选择器文字**（不在本表：武将名/城名/命令名/按钮） | **拿去点** | 匹配不到 → **点错行 → 改了游戏状态**。**代价最高** | 必须锁到候选表，锁不上**不点** |

所以白名单没命中时，这类行走 `values_text` + `uncertain`（**不是 `unread`**），
而不是像数字行那样整个丢掉。白名单仍然留着 —— 它的作用变成"命中就升级成
`values` 里的精确值"，而不再是"不中就杀"。
"""

PANEL_VALUE_MUST_DIGIT = True
"""**通用否决规则**：任何行的值只要**一个数字都没有**，就作废。
**例外见 `PANEL_TEXT_ROWS`**（`商人` 行的值本来就是文字）。

⛔ 为什么（2026-09-19 实测，比"斜杠丢失"更普遍的一类静默错误）：
`read_panel_from` 只按**固定行 y + x≥`PANEL_LABEL_X`** 抓字，**它不校验屏幕上到底有没有面板**。
于是当画面其实是「情報→全都市」一覽 / 「選擇武將」列表 / 出征界面时，
**列表里的城名、人名、UI 词会正好落进这些 y 行，被当成"面板读数"**：

| 行 | 被读成的值（全部是垃圾） |
|---|---|
| 民心 | `大将` |
| 收穫 | `障形` / `南皮` |
| 士氣 | `士兵` / `北海` |
| 人口 | `資金` / `陈留` / `高异` |
| 兵役人口 | `小沛` / `劉辟` |
| 士兵 | `士氣` / `樂陵` / `廖化` |
| 傷兵 | `安德` / `龚都晟政` |
| 資金收入 | `東莱` / `谢守兵` |
| 商人 | `駐在` / `常驻` / `東阿` / `谢城壁` |
| 市價 | `守備力` |

**这些值一个数字都没有**，而面板 17 行的合法值**全都是"含数字"的**
（数字 / `数字/数字` / `兵糧N/資金M`；`—` 这种非数字符会被 `_panel_norm_value` 先剥掉）。
26 帧 × 17 行 = 442 个格子里，**"不含数字"的样本里没有一个合法值** ⇒ 规则成立。

⚠️ 这是**形状否决**，不是"修补"：垃圾值作废后进 `unread`/`missing`，
调用方看到的是"读不出来"，**不是**"這城有 廖化 個士兵"。仍然**绝不猜值**。
"""

PANEL_INK_TH = 110
"""墨迹判据：灰度 > 110 算字。亮像素计数用它。"""

PANEL_OCR_ALIAS = {
    "伤兵": "傷兵", "士氟": "士氣", "士気": "士氣",
    "兵耀": "兵糧", "兵程": "兵糧", "兵幢": "兵糧",
    "资金": "資金", "收覆": "收穫", "驻在": "駐在",
}
"""OCR 实测读花过的字（两张截图 + 8 座城的历史实跑记录）→ 纠正表。**只列见过的。**"""

_PANEL_LABEL_SET = frozenset(lab for lab, _ in PANEL_ROWS)


def _panel_norm_label(s: str) -> str:
    """把 OCR 读到的标签归一化，好跟行表对位。**白名单驱动，不做猜测。**

    历史实测的脏样本：`0民心` `士氟` `兵耀收入` `资金收入。` `。耐久` `伤兵` `人`。
    """
    s = re.sub(r"^[^\u4e00-\u9fff]+", "", (s or "").strip())
    if s in _PANEL_LABEL_SET:
        return s
    for a, b in PANEL_OCR_ALIAS.items():
        s = s.replace(a, b)
    if s in _PANEL_LABEL_SET:
        return s
    s2 = re.sub(r"[^\u4e00-\u9fff0-9/]+$", "", s)      # 去掉尾随噪声（`。` `口` 等）
    if s2 in _PANEL_LABEL_SET:
        return s2
    # 前置"糊进来的汉字"（实测 `市價` 被读成 `建市價` / `阳市價`）——
    # 从左边一/两个字地试掉，**只有正好落进白名单才认**（否则原样返回）。
    for k in range(1, min(3, len(s2))):
        if s2[k:] in _PANEL_LABEL_SET:
            return s2[k:]
    return s


def _panel_norm_value(v: str | None) -> str | None:
    """数值归一化：去首尾噪声 + 纠已知读花字。不动数字。"""
    if v is None:
        return None
    v = (v or "").strip()
    for a, b in PANEL_OCR_ALIAS.items():
        v = v.replace(a, b)
    return re.sub(r"[^\u4e00-\u9fff0-9/]+$", "", v) or None


def _panel_ink(frame: np.ndarray, ry: int) -> int:
    """数值格里的亮像素数 —— 「这格到底有没有东西」的**非 OCR 通道**。

    ⛔ 为什么需要它：`傷兵/俘虜/在野/商人` 四个格子的值（`0` / `—`）
    OCR 在 4~10 倍放大下**一个都不报框**，但像素上是**有墨迹的**（实测 48~75 个亮像素）。
    所以「读不出来」必须跟「本来就是空的」分开报 —— 否则调用方会把
    「俘虜：0」误当成「俘虜：未知」，或者反过来。
    """
    x0, x1 = PANEL_VALUE_X
    cell = frame[max(0, ry - PANEL_TOL):ry + PANEL_TOL, x0:x1]
    if cell.size == 0:
        return 0
    return int((cell > PANEL_INK_TH).sum())


def _panel_join(ts: list[str]) -> str:
    """把一行里同一列的多段 OCR 拼起来并去掉空白（`310/ 700` → `310/700`）。"""
    return re.sub(r"\s+", "", "".join(ts))


def _panel_assign(items: list[dict]) -> tuple[dict, list[str]]:
    """一次 OCR 的结果 → `({行y: {"left": [...], "right": [...]}}, 野字列表)`。"""
    bucket: dict = {}
    stray: list[str] = []
    for it in items:
        t = (it.get("text") or "").strip()
        if not t:
            continue
        y = it["y"]
        row, best = None, PANEL_TOL + 1
        for _lab, ry in ((None, PANEL_TITLE_Y),) + tuple(PANEL_ROWS):
            d = abs(y - ry)
            if d < best:
                row, best = ry, d
        if row is None:
            stray.append(t)               # 离每一行都太远 → 当野字丢掉（但要留痕）
            continue
        side = "left" if it["box"][0] < PANEL_LABEL_X else "right"
        bucket.setdefault(row, {"left": [], "right": []})[side].append(t)
    return bucket, stray


def _panel_vote(vals: list[str]) -> tuple[str | None, int]:
    """多尺度投票：票数最多的胜出；返回 (值, 票数)。"""
    vals = [v for v in vals if v]
    if not vals:
        return None, 0
    top, n = Counter(vals).most_common(1)[0]
    return top, n


def read_panel(hwnd) -> dict:
    """读左上设施情报面板 → **结构化**结果（`read_info_panel` 的详细版）。

    返回 `{"ok", "title", "grade", "rows": [...], "values": {...},
            "missing": [...], "unstable": [...], "stray": [...]}`。

    每行含 `label`（**行表给的名字，权威**）/ `label_ocr`（OCR 真读到什么）/
    `value` / `agree`（三种放大有几票一致）。

    ⛔ 设计要点：**字段身份由行位置决定，不由 OCR 文本决定。**
    实测 OCR 会把 `兵糧收入` 读成 `兵耀收入` / `兵程收入`、把 `傷兵` 读成 `伤兵`、
    把 `人口` 读成 `人`，而 `收穫` / `士氣` / `俘虜` 的**标签整个读不出来** ——
    旧实现把这些行整行丢掉（就是"17 项只读回 11"的主因）。
    OCR 标签只当**对位校验的锚**（`label_ocr` + `anchor_ok`），不拿来当键名。
    """
    bgr = ocr._grab_bgr(hwnd)
    if bgr is None:
        return {"ok": False, "why": "抓屏失败"}
    return read_panel_from(bgr)


def read_panel_from(frame: np.ndarray) -> dict:
    """`read_panel` 的**图像入口** —— 传整屏 BGR 图，从里面抠出面板区来读。

    拆出来是为了能**离线回归测试**（拿已保存的截图当输入），
    跟 `ocr.read_digit_field(bgr, region)` 一个路子：
    **读数通道要能脱机重放，才谈得上"改完不回归"。**
    """
    x0, y0, x1, y1 = PANEL_REG
    sub = frame[y0:y1, x0:x1]
    per = []
    strays: list[str] = []
    for up in PANEL_SCALES:
        items = ocr.read(sub, upscale=up)
        for it in items:                  # 还原成整屏坐标（_panel_assign 用得到）
            for k in (0, 1, 2, 3):
                it["box"][k] += (x0, y0, x0, y0)[k]
            it["x"] += x0
            it["y"] += y0
        bucket, stray = _panel_assign(items)
        per.append(bucket)
        strays += stray

    rows, missing, unread, unstable, uncertain = [], [], [], [], []
    for lab, ry in PANEL_ROWS:
        vals, labs = [], []
        for b in per:
            d = b.get(ry) or {}
            if d.get("right"):
                vals.append(_panel_join(d["right"]))
            labs += d.get("left") or []        # 标签：**所有尺度**的候选都收进来投票
        value, agree = _panel_vote(vals)
        value = _panel_norm_value(value)
        # 形状不对 ⇒ 读数作废（见 PANEL_VALUE_PAT：实测 `4/ 4` 被读成 `47`）
        rejected = None
        raw_text = None
        if value is not None:
            _pat = PANEL_VALUE_PAT.get(lab)
            _is_text_row = lab in PANEL_TEXT_ROWS
            if _pat and not re.fullmatch(_pat, value):
                if _is_text_row:
                    # ⭐ **描述性文字行：白名单没中也保留原文**（2026-09-19 用户提出）。
                    # 理由：这类值 agent 只是**读来理解**，不拿去点击。
                    # OCR 把 `駐在` 读成 `駐存` 时，agent 靠语义照样知道"有商人"
                    # ⇒ 丢掉原文的代价（什么都不知道）比给个错字大得多。
                    # 仍然标 `value_uncertain` + `value_looks_like`，让 agent 自己判断。
                    raw_text, value = value, None
                else:
                    rejected, value = value, None
            elif _is_text_row and not _pat:
                # 文字行没配白名单 ⇒ 等于完全放开。这里不该发生（test ⑦b 会拦），
                # 但真发生了也走"原文 + 不确定"而不是当精确值
                raw_text, value = value, None
            elif (PANEL_VALUE_MUST_DIGIT and not _is_text_row
                    and not re.search(r"\d", value)):
                # 通用否决：一个数字都没有 ⇒ 一定是抓到了别处的字（城名/人名/UI 词）。
                # 见 PANEL_VALUE_MUST_DIGIT：数字行的合法值全都是"含数字"的。
                rejected, value = value, None
        # 标签先归一化再投票 —— 同一行被读成 `市價` / `建市價` 时票会合到一起
        label_ocr, _ = _panel_vote([t for t in map(_panel_norm_label, labs) if t])
        row = {"label": lab, "label_ocr": label_ocr or None,
               "value": value, "agree": agree, "value_rejected": rejected,
               "anchor_ok": (label_ocr == lab) if label_ocr else None}
        if raw_text is not None:
            # 原文照给，但明确告诉调用方"这几个字没对上已知词，可能有错字"
            row["value_text"] = raw_text
            row["value_uncertain"] = True
            # ⚠️ **区分两种"没对上"**，否则这个例外会变成漏洞：
            #   ① `駐存` —— 与合法值 `駐在` 只差一字 ⇒ 大概率是 OCR 错字，**可用**
            #   ② `東阿` / `谢城壁` —— 非面板帧的城名人名串进来 ⇒ **不是这一行的值**
            # 判据：与本行合法值集合的**最优相似度**。agent 靠它决定信不信。
            row["value_looks_like"] = _text_row_similarity(lab, raw_text)
        if value is None and raw_text is None:
            # 「读不出来」和「本来就是空的」必须分开 —— 见 _panel_ink 的注释
            ink = _panel_ink(frame, ry)
            row["ink"] = ink
            (unread if ink > 0 else missing).append(lab)
        elif raw_text is not None:
            # 有原文但没对上词表 ⇒ **不是 unread**（确实读到字了），归进第四类
            uncertain.append(lab)
        elif agree < 2:
            unstable.append(lab)
        rows.append(row)

    tb = per[0].get(PANEL_TITLE_Y) or {}
    title = _panel_join(tb.get("left") or []) or None
    grade = None
    for b in per:                          # 等级在标题行右列，取能读到的那几个之一
        g = _panel_join((b.get(PANEL_TITLE_Y) or {}).get("right") or [])
        if g in PANEL_GRADES:
            grade = g
            break
    if title:                              # 城名过一遍词典（OCR 常把生僻字读花）
        fixed, conf, how = lexicon.correct(
            title, vocab=lexicon.OBSERVED_PLACES + lexicon.CANDIDATE_PLACES)
        if conf >= 0.7:                    # 只有"确实是词典里的地方名"才采纳
            title = fixed

    return {"ok": bool(rows), "title": title, "grade": grade, "rows": rows,
            "values": {r["label"]: r["value"] for r in rows if r["value"] is not None},
            "missing": missing, "unread": unread, "unstable": unstable,
            "stray": strays,
            # 形状不对而被丢掉的读数（**留痕**，让"整行读错"从此可见）
            "value_rejected": {r["label"]: r["value_rejected"]
                               for r in rows if r.get("value_rejected")},
            # ⭐ 描述性文字行：读到了字但没对上词表 ⇒ **原文照给 + 标不确定**。
            # 这不是 `unread`（确实读到了），也不进 `values`（怕调用方当精确值用）。
            "values_text": {r["label"]: r["value_text"]
                            for r in rows if r.get("value_text")},
            "uncertain": uncertain,
            }

def read_info_panel(hwnd, detailed: bool = False) -> dict:
    """读左上设施情报面板 → `{标签: 数值}`，例如 `{"民心": "552/1000", "在任": "4/4"}`。

    `detailed=True` 时返回 `read_panel()` 的完整结构（含 `title` / `missing` / 各行的
    原始 OCR 文本），排查用。

    ⚠️ **这个面板只在"设施的都市命令菜单开着"时才存在** —— 实测干净战略面上
    该区域是空的（OCR 0 项）。所以它是 `atoms.menu` 的一部分，不是随时可读的。

    这个面板是决策与"闭环验证"的核心：命令在**过旬时**才结算，
    验证方式就是过旬前后复读同一批数字。
    """
    p = read_panel(hwnd)
    return p if detailed else p.get("values", {})


def read_panel_only(hwnd, row: int, expect: str | None = None) -> dict:
    """**只读面板**：开命令菜单 → 读左上情报面板 → 关菜单。**不下达任何命令。**

    为什么不干脆把面板做成"随时可读"的：实测**干净战略面上该区域是空的**
    （OCR 0 项、区域全空）—— 面板是命令菜单的一部分，不点开都市就没有。
    所以这里必须开一次菜单；走的是 `open_command_menu` 那条**已验证**的路径
    （选列表行 → 点屏幕正中 → 用左上标题核实），失败时它自己会留图 + recover。

    比 `menu()` 便宜：不读主菜单、**不展开子菜单**（少一次 `→` 按键）。
    """
    out: dict = {"ok": False, "row": row, "expect": expect}
    opened = open_command_menu(hwnd, row, expect=expect)
    out["commanded"] = opened.get("commanded")
    out["clicked"] = opened.get("clicked")
    out["located_by"] = opened.get("located_by")
    if opened.get("warn"):
        out["warn"] = opened["warn"]
    if not opened.get("ok"):
        # ⛔ 复用 open_command_menu 在失败当场拍的图 + 它做的清理，别再拍第二张
        out["why"] = opened.get("why") or "命令菜单没打开"
        out["shot"] = opened.get("shot")
        out["recover"] = opened.get("recover")
        return out

    try:
        p = read_panel(hwnd)
    except Exception as e:
        p = {"ok": False, "why": "读面板抛异常：%s: %s" % (type(e).__name__, e)}
    if p.get("title") and out["commanded"] and p["title"] != out["commanded"]:
        p["warn"] = "面板标题「%s」与左上标题「%s」不一致 —— 以游戏为准" % (
            p["title"], out["commanded"])
    out.update(p)
    out["hint"] = hint(hwnd)
    out["shot"] = _shot(hwnd, "panel%d_%s" % (row, out.get("commanded")))

    closed = False
    try:
        closed = bool(cmdscreen.close_menu(hwnd))
    except Exception as e:
        out["close_error"] = str(e)
    out["closed"] = closed
    out["ok"] = bool(closed) and bool(p.get("rows"))
    if not out["ok"]:
        out["why"] = ("面板读到了但菜单没关干净" if p.get("rows")
                      else "菜单开了「%s」但面板一项都没读出来" % out.get("commanded"))
        if not closed:
            out["recover"] = recover(hwnd)
        else:
            out["recover"] = {"recovered": True, "how": "close_menu 已确认通畅"}
    return out


def officer_availability(panel: dict) -> dict:
    """把面板读数翻译成「这个设施现在**还有几个人能下令**」—— **只给人数，不给名字**。

    ## 语义（2026-09-19 定案，不是猜的）

        `在任 X/Y`  ⇒  X = **本旬可下令人數**   Y = **該設施在任武將總數**

    怎么定的：两条**互不相同**的通道、且**零提交**——
    ① 面板数值列 OCR 读 `在任` = `4/5`；
    ② `san9_facility_command(row=1)` 第 1 轮（不传 officers）让游戏自己开出选将弹窗，
       候选名单**恰好 4 个**。读不同区域、用不同算法，数字相同 ⇒ 定案。

    ## ⛔ `Y − X` 不许解释成「本旬已行动」

    实测反例：南皮读到 `4/5`，而该战略面**从未下过任何命令**（資金 30631 一分未动，
    且那个月正是資金收入月）。⇒ 少掉的那个人是**人不在城里**
    （远征 / 移動 / 探索中，或说明书 P.59 的修行期间）。
    **成因不可判定** ⇒ 所以这里只报人数，把 `cannot_order_note` 原样带出去，
    让调用方没有机会把它当成"已行动人数"。

    ## ⛔ 「读不出来」≠「0 人」

    `在任` 读不出来 ⇒ `can_order_now` 是 `None`（**不是 0**），并带 `reason`。
    形状不合法（实测 `4/ 4` 被 OCR 读成 `47`，见 `PANEL_VALUE_PAT`）也算读不出来。
    """
    v = panel.get("values") or {}
    raw = v.get("在任")
    rejected = (panel.get("value_rejected") or {}).get("在任")
    out: dict = {
        "field": "在任",
        "raw": raw,
        "can_order_now": None,
        "total": None,
        "cannot_order_now": None,
        "meaning": "在任 X/Y = X 本旬可下令人數 / Y 該設施在任武將總數"
                   "（2026-09-19 定案：两条独立通道 · 零提交验证）",
        "cannot_order_note": "⛔ 不许把 Y−X 当成「本旬已行动人数」—— 实测反例：南皮读到 4/5 "
                             "而该战略面从未下过任何命令（資金一分未动）。少掉的人可能是"
                             "「人不在城里」（远征 / 移動 / 探索 / 修行），**成因不可判定**。",
    }
    if raw is None:
        out["unread"] = True
        if rejected:
            out["reason"] = ("「在任」读到 %r，但**形状不合法**（要求 数字/数字）"
                             "⇒ 已作废、不猜值。原读数留痕在 value_rejected。" % rejected)
        else:
            out["reason"] = ("「在任」这一格没有任何可用读数（也读不到 OCR 框）"
                             "—— 是读不出来还是本来就没这行，分不出来，**不猜**。")
        return out

    m = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*", raw)
    if not m:
        out["unread"] = True
        out["reason"] = "「在任」=%r 解析不出「數字/數字」形状 ⇒ 作废（不猜值）" % raw
        return out

    x, y = int(m.group(1)), int(m.group(2))
    out["can_order_now"] = x
    out["total"] = y
    out["cannot_order_now"] = y - x
    if y == 0:
        out["note"] = ("在任總數 0 ⇒ 本旬该设施无人可下令。实测北海 0/0 时：8 条命令全灰、"
                       "底栏写「沒有可執行的武將。」")
    elif x == 1:
        out["note"] = ("⚠️ 本旬只剩 1 名可下令武将 —— 此时游戏会自动选中他并**直接执行**命令，"
                       "「零提交侦察」会被误伤成真的下了命令，别拿选将弹窗做只读侦察。")
    elif x == 0:
        out["note"] = "本旬 0 人可下令，但在任總數 >0 ⇒ 在任的人当前都不可下令（成因不可判定）。"
    return out


def _panel_cell_status(panel: dict, label: str) -> dict | None:
    """面板某一格的值 + 「读不出来 / 本来就是空」的分类。

    ⛔ 为什么必须分类：`俘虜 / 在野 / 傷兵` 的值是 `0`，OCR 在 4~10 倍放大下**一个都不报框**，
    但像素上**有墨迹**。所以 `status` 只有三种，且**永不猜值**：
    `"read"`（读到值）/ `"unread"`（有墨迹读不出）/ `"missing"`（连墨迹都没有）。

    这个函数在 `atoms.py` 里，是因为"什么算读不出来"属于读数语义，
    不该由调用方（`san9mcp/`）各自重新解释一遍。
    """
    for r in panel.get("rows") or []:
        if r.get("label") != label:
            continue
        if r.get("value") is not None:
            return {"value": r["value"], "status": "read"}
        if r.get("value_rejected"):
            return {"value": None, "status": "unread", "ink": r.get("ink"),
                    "value_rejected": r["value_rejected"]}
        ink = r.get("ink")
        return {"value": None, "status": "unread" if (ink or 0) > 0 else "missing",
                "ink": ink}
    return None


def state(hwnd) -> dict:
    """只读。顶栏 / 设施列表 / 底部提示 / 当前指挥对象 / 面板数字 / 图。不做任何操作。"""
    out: dict = {"ok": True}
    try:
        tb = ocr.read_topbar(hwnd)
        out["topbar"] = {k: v for k, v in tb.items() if k != "raw"}
    except Exception as e:
        out["topbar"] = {}
        out["note"] = f"顶栏读失败：{e}"
    try:
        rows = cmdscreen.city_rows(hwnd)
    except Exception as e:
        out["ok"] = False
        out["error"] = f"设施列表读失败：{e}"
        out["shot"] = _shot(hwnd, "state_err")
        return out
    # ⛔ **不要把"推断出来的名字"当名字端出去。**
    # `cmdscreen._attach_names` 会给 OCR 读不出的行编一个「南皮+1 行(推断)」
    # 并标 `inferred=True` —— 内部调试这样合理（诚实标注），
    # 但对 agent 是个**陷阱**：它会以为真有个设施叫这个名。
    # 名字读不出就老实给 `null`（**行号是准的，这个能信**），
    # 编出来的那个只放进 `name_inferred`，不冒充。
    fac = []
    for i, r in enumerate(rows):
        nm = (r.get("name") or "").strip()
        inferred = bool(r.get("inferred")) or ("推断" in nm)
        fac.append({"i": i, "row": i,
                    "name": None if (inferred or not nm) else nm,
                    "name_inferred": nm if inferred else None,
                    "highlight": bool(r.get("highlight"))})
    out["facilities"] = fac
    os.makedirs(SHOT_DIR, exist_ok=True)
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(fac, f, ensure_ascii=False, indent=1)
    out["commanded"] = commanded_facility(hwnd)
    out["hint"] = hint(hwnd)
    out["shot"] = _shot(hwnd, "state")
    return out


def resolve(hwnd, index: int) -> tuple[str | None, int]:
    """把 `--facility N` 解析成 (缓存里的名字, **当前行号**)。

    ⚠️ 实测：右侧列表的行**位置会随选择变化而滚动**，而名字 OCR 又读不全
    （14 行里稳定读出的只有 6~7 行，高亮那一行反而读不出来）。
    所以这里**不再要求名字必须找到** —— 名字只当参考，
    "到底指挥的是谁"交给 `menu` 里的左上标题去报（那个 6/6 稳）。
    找不到缓存就退回 index 本身当行号。
    """
    try:
        with open(CACHE, encoding="utf-8") as f:
            cached = json.load(f)
    except Exception:
        return (None, index)
    name = None
    for c in cached:
        if c.get("i") == index:
            name = c.get("name")
            break
    if name and "推断" in name:
        name = None                      # 推断出来的名字不算名字
    try:
        rows = cmdscreen.city_rows(hwnd)
        if 0 <= index < len(rows):
            cur = rows[index].get("name")
            if cur and "推断" not in cur:
                name = cur               # 用当前这一行的名字（比缓存新）
    except Exception:
        pass
    return (name, index)


CITY_CLICK = (640, 480)
"""**选中设施之后，它的城管图形落在哪** —— 就是**屏幕正中**。

（用户指出的：选中城市之后往中间点最稳，直接就是城市中心。
实测行0平原 → 标题「平原」、行5小沛 → 标题「小沛」，同一个 (640,480) 两点全中。
我一开始用的 (540,460) 偏左下 100px，也中但不如正中干净 —— 正中不是魔数。）

为什么不能靠"在地图上找城市"：`cmdscreen.find_city_on_map` 是坏的 ——
实测返回 **1 个 750x613、面积 18 万像素的巨块**（城墙+道路+港口糊成一片），
取质心 (578,518) 当点击点，那是"糊出来的平均位置"，会点到旁边的设施上
（用户看到的"为什么去点東阿港"就是它干的）。

正确做法：
1. 在右侧列表点某一行 → 地图滚过去，该设施落在**屏幕正中**
2. 点 (640,480) → 左上「设施情报」面板出现，**标题就写着这是谁**
3. 用标题核实（`commanded_facility`）；⛔ 不要用右下「地域：X（Y）」，它跟鼠标悬停
"""

# ⛔⛔ 2026-09-19 **删除** `CITY_CLICK_ALT = [(640,480),(632,486),(648,474),(620,470),(660,490)]`
# —— 那是"同一点不稳就依次试 5 个落点"的写法，属于**禁止**的盲点。
# 正确做法见 `open_command_menu`：**只点推导出来的屏幕正中**，核实不上就报错停手。


def select(hwnd, index: int) -> dict:
    """选中缓存里第 index 个设施（按名字现查行号）。判 ok = **左上标题变成它**。"""
    out: dict = {"ok": False, "facility_index": index}
    name, row = resolve(hwnd, index)
    out["name_guess"] = name
    out["row"] = row
    before = _grab_int(hwnd)
    try:
        cmdscreen.select_city_row(hwnd, row)
    except Exception as e:
        out["why"] = f"select_city_row 抛异常：{type(e).__name__}: {e}"
        out["shot"] = _shot(hwnd, f"select{index}_err")
        return out
    after = _grab_int(hwnd)
    out["changed_px"] = int((np.abs(after - before).mean(axis=2) > 25).sum())
    out["commanded"] = commanded_facility(hwnd)
    out["hint"] = hint(hwnd)
    # 面板标题只有在指挥菜单打开时才有；这里主要靠"画面确实滚过去了"
    out["ok"] = out["changed_px"] > 8000
    if not out["ok"]:
        out["why"] = "点了行但画面几乎没变 —— 可能没点上"
    out["shot"] = _shot(hwnd, f"select{index}_{name or row}")
    return out


# ------------------------------------------------------------------ 原子 3：读菜单

def open_command_menu(hwnd, row: int, expect: str | None = None,
                      allow_title_unread: bool = True) -> dict:
    """**选中第 row 行 → 点屏幕正中 → 核实**。返回看到的是谁。

    这是本文件里唯一正确的"打开某设施命令菜单"的路径。
    不要用 `cmdscreen.find_city_on_map`（它返回巨型糊块的质心，见 CITY_CLICK 注释）。

    ⛔⛔ 2026-09-19 重写（用户当面制止）：原来这里是
    `for xy in CITY_CLICK_ALT:` —— 点一个不灵就 `close_menu` 再换下一个坐标再点，
    最多 5 个落点 × (1 次点击 + close_menu 的盲点)，**最坏约 75 次盲点**。整段删除。

    现在**只点一个点**，而且它**不是猜的、是推导出来的**：
    点右侧列表某行后，地图会滚过去让该设施**落在屏幕正中**（实测配方），
    所以 `CITY_CENTER` 就是那个设施本身。

    ⭐⭐ **2026-09-20 改：标题读不出不再等于"不能操作"**（用户指正：
    「不能读不出就不操作呀，每个城市都得操作，读不出能操作的城市也得操作」）。
    核实分两级：

      1. **一级（首选）**：左上情报面板的标题读得出 → 用它核对名字（`verified_by="title"`）。
      2. **二级（兜底）**：标题读不出 → 改用**结构判据**：`cmdmenu.read_menu()` 报命令菜单
         **真的开了**（`verified_by="menu_opened"`）。**点空地图/海面是开不出命令菜单的**，
         所以这条能证明"点中的确实是个设施"；设施的**身份**则按
         **右侧列表第 `row` 行**的位置认（这正是"按行号操作"的语义）。
         ⛔ 仍然**只点一个点、不换坐标重试**；⛔ 也**不会**把名字当成必需项。

    ⛔ 标题读不出这件事会在返回里**明确标出来**（`title_unread=True` + `warn`），
       让调用方知道"名字没核对过，身份是按行号认的"。⛔ 不许悄悄当成核对过。
    """
    out: dict = {"ok": False, "row": row, "expect": expect}
    try:
        cmdscreen.select_city_row(hwnd, row)
    except Exception as e:
        out["why"] = f"select_city_row 抛异常：{e}"
        return out
    time.sleep(1.8)

    xy = cmdscreen.CITY_CENTER      # (640,480)：选行后设施落在这里 —— 推导值，不是猜的
    winio.click_client(hwnd, xy[0], xy[1], settle=0.5)
    time.sleep(1.4)
    out["clicked"] = list(xy)
    out["located_by"] = "CITY_CENTER（选中列表行后，地图滚动让该设施落在屏幕正中）"
    out["commanded"] = commanded_facility(hwnd)
    if out["commanded"]:
        out["ok"] = True
        out["verified_by"] = "title"
        if expect and out["commanded"] != expect:
            out["warn"] = f"期望「{expect}」，游戏报「{out['commanded']}」—— 以游戏为准"
        return out

    # ⭐ 二级核实：标题读不出 → 看**命令菜单有没有真的开**
    if allow_title_unread:
        try:
            m = cmdmenu.read_menu(hwnd)
        except Exception as e:                                   # pragma: no cover
            m = {"found": False, "why": "read_menu 抛异常：%s" % e}
        out["menu_found"] = bool(m.get("found"))
        if m.get("found"):
            out["ok"] = True
            out["verified_by"] = "menu_opened"
            out["title_unread"] = True
            out["hint"] = hint(hwnd)
            out["warn"] = ("左上标题读不出 ⇒ **没做名字核对**；已用结构判据确认**命令菜单真的开了**"
                           "（点空地图不会开菜单）。本次操作的设施身份 = **右侧列表第 %d 行**。"
                           % row)
            return out

    out["why"] = ("点了屏幕正中（选中列表行后设施应落在这里），但左上情报面板的标题读不出来"
                  " → **无法确认点对了，停手**。**不换坐标重试** —— "
                  "要么是这个设施的名字 OCR 读不出，要么列表行选错了，"
                  "两种都该由调用方看了证据再决定。")
    out["hint"] = hint(hwnd)
    out["shot"] = _shot(hwnd, f"openmenu{row}_fail")   # ⛔ **先留证**（拍在清理之前）
    # ⛔ **再清理** —— 失败时命令菜单很可能开着。
    # 实测踩过：`san9_menu` 读失败后**没关菜单**，屏幕留脏（go=0.175）要人收拾。
    out["recover"] = recover(hwnd)
    return out


def read_open_menu(hwnd, expand: bool = True) -> dict:
    """菜单已经开着时，读主菜单 + 「設施」子菜单的可用/置灰。

    ⚠️ **子菜单不是自动展开的** —— 主菜单打开时只有 6 项（設施/軍事/人材/計略/
    外交/任免），「設施」的 8 个子项要按一下「→」（或把鼠标移到「設施」上）才出来。
    实测：不按 → 子菜单读回来是空列表（我一开始就漏了这一步，白读成"2 行空名"）。
    """
    if expand:
        try:
            winio.press("right")
            time.sleep(0.9)
        except Exception:
            pass
    try:
        r = cmdmenu.read_menu(hwnd)
    except Exception as e:
        return {"ok": False, "why": f"read_menu 抛异常：{e}"}
    raw = [{"name": (i.get("name") or "").strip(),
            "enabled": bool(i.get("enabled")), "y": i.get("y")}
           for i in (r.get("sub") or [])]

    # 子菜单的名字几乎读不出来（这套小字 + 底纹）。但**项序是固定的**，
    # 所以用表补名，并用"能读到的那一项"做对位校验 ——
    # 实测读到「徵兵」正好在第 5 项（index 4），与 FACILITY_CMDS[4] 一致 → 对位成立。
    items = []
    anchors = []
    for i, it in enumerate(raw[:len(FACILITY_CMDS)]):
        want = FACILITY_CMDS[i]
        nm = it["name"] or want
        if it["name"]:
            anchors.append({"i": i, "ocr": it["name"], "table": want,
                            "match": it["name"] == want})
        items.append({"name": nm, "from_table": not it["name"],
                      "enabled": it["enabled"], "y": it["y"]})
    extra = len(raw) - len(FACILITY_CMDS)
    return {
        "ok": bool(r.get("found")) and bool(items),
        "found": r.get("found"),
        "main": [{"name": i.get("name"), "enabled": i.get("enabled"),
                  "y": i.get("y")} for i in (r.get("main") or [])],
        "items": items,
        "anchor_check": anchors,
        "extra_rows": extra if extra > 0 else 0,
        "enabled": [i["name"] for i in items if i["enabled"]],
        "disabled": [i["name"] for i in items if not i["enabled"]],
    }


def menu(hwnd, index: int) -> dict:
    """打开指定设施的**命令菜单**并读出可用/置灰。读完一定关掉。

    路径：选列表行 → 点城管图形(CITY_CLICK) → 左上标题核实 → 读菜单 → 关。
    """
    out: dict = {"ok": False, "facility_index": index}
    name, row = resolve(hwnd, index)
    out["name_guess"] = name
    out["row"] = row

    opened = open_command_menu(hwnd, row, expect=name)
    out["commanded"] = opened.get("commanded")
    out["clicked"] = opened.get("clicked")
    if opened.get("warn"):
        out["warn"] = opened["warn"]
    if not opened.get("ok"):
        out["why"] = opened.get("why") or "菜单没打开"
        # ⛔ **复用 `open_command_menu` 在失败当场拍的图和它做的清理。**
        # 原来这里又拍一张 —— 可那时菜单已被 recover 关掉了，
        # 拍的是"恢复之后"的画面 = **等于没留证**（同一个错以前在 issue 里犯过）。
        out["shot"] = opened.get("shot")
        out["recover"] = opened.get("recover")
        return out

    m = read_open_menu(hwnd)
    out.update({k: m.get(k) for k in ("found", "main", "items", "enabled",
                                      "disabled", "anchor_check", "extra_rows")})
    out["panel"] = read_info_panel(hwnd)
    out["hint"] = hint(hwnd)
    out["shot"] = _shot(hwnd, f"menu{index}_{out['commanded']}")
    try:
        cmdscreen.close_menu(hwnd)
        out["closed"] = True
    except Exception as e:
        out["closed"] = False
        out["why"] = f"关菜单失败：{e}"
    out["ok"] = bool(out["commanded"]) and bool(m.get("found"))
    if not out["ok"]:
        out["why"] = f"打开了「{out['commanded']}」但菜单没读出来"
    return out


# ------------------------------------------------------------------ 原子 3.5：命令结果播报框

RESULT_BOX_REG = (430, 380, 1030, 700)
"""命令执行后那个**结果播报框**的区域。

⚠️ **用户 2026-09-18 纠正过的一个事实**：这个框**不是**要关掉的脏弹窗，
它是**命令的即时结果播报** —— 工具必须把它读出来当操作结果。

实测时间线（平原·巡察，2026-09-18）：

    t≈28s  命令下达完，框出现；go 锚点 0.889 → **0.161**（框会挡住战略面）
    t≈32s  数字页出现：「平原的民心變為375(+15)，增加兵役變為11498(+460)」
    t≈35s  框空（翻页间隙）
    t≈37s  框**自动消失**，go 回到 0.889 —— **不用点，自己走**

推论（很重要）：**設施 9 条是「立即」见效**，所以这个框就是**数字闭环本身** ——
一条命令调用就能自我验证，**不用等过旬**。
"""


def _go_score(hwnd) -> float | None:
    """「進行」锚点匹配分。通畅 0.8~1.0 / 被东西挡住 0.1~0.35。"""
    try:
        from .anchors import AnchorBook
        w, h, buf = winio.grab(hwnd, use_window_dc=False)
        hit = AnchorBook().find(buf, w, h, "strategy", "go")
        return float(hit.get("score", 0.0)) if hit else None
    except Exception:
        return None


def capture_result_box(hwnd, timeout: float = 22.0, poll: float = 0.7,
                       appear_timeout: float = 8.0) -> dict:
    """等结果框出现 → **多帧并集**收集它的文本 → 等它自己消失。

    ⚠️ 必须多帧并集：实测框是**分页**的 —— 先出武将这句台词，再翻到数字页，
    中间还有一瞬是空的。只看一帧会漏掉数字（那才是我们要的）。

    ⚠️ **框没出现就别死等**（`appear_timeout`）：实测下达后约 4s 才出现，
    但如果本来就没有框（例如失败路径），死等 22s 纯属浪费。

    返回 `{found, lines, text, seconds, cleared}`。
    `cleared=True` 表示等到它自己走了，战略面已恢复干净。
    """
    lines: list[str] = []
    t0 = time.time()
    seen = False
    while time.time() - t0 < timeout:
        el = time.time() - t0
        g = _go_score(hwnd)
        blocked = (g is not None and g < 0.5)
        if blocked:
            seen = True
            try:
                items = ocr.read_screen(hwnd, RESULT_BOX_REG, upscale=4)
            except Exception:
                items = []
            items.sort(key=lambda d: (d["y"], d["x"]))
            for it in items:
                t = (it.get("text") or "").strip()
                if t and t not in lines:
                    lines.append(t)
        elif seen:
            break                      # 框出现过又没了 → 收工
        elif el > appear_timeout:
            break                      # 压根没出现，别死等
        time.sleep(poll)

    waited = round(time.time() - t0, 1)
    g_end = _go_score(hwnd)
    return {
        "found": bool(seen and lines),
        "lines": lines,
        "text": " ".join(lines),
        "numbers": [x for x in lines if ("+" in x or "(" in x)],
        "seconds": waited,
        "cleared": bool(g_end is not None and g_end >= 0.5),
        "go_after": g_end,
    }


# ------------------------------------------------------------------ 原子 4：下一条命令

def issue(hwnd, index: int = 0, cmd: str = "", row: int | None = None,
          menu: int = 0, menu_name: str | None = None,
          capture_result: bool = True) -> dict:
    """下**一条**命令。

    `menu` = 主菜单第几项（0=設施 1=軍事 2=人材 3=計略 4=外交 5=任免）。

    ⭐ `row` 给了就**按列表行号直通**（推荐用法）—— 因为列表里的名字 OCR 读不出来，
    而**行的位置是准的**。而且 `run_facility_command` 内部有 `verify_city` 这一步：
    它会读**命令界面的标题栏**「命令設施 X」核实到底在指挥谁，点错了当场能看出来
    （返回值里的 `commanded`）。

    `index` 是旧的设施序号用法（走 `resolve` 查名字），保留兼容。
    """
    out: dict = {"ok": False, "facility_index": index, "cmd": cmd, "menu": menu}
    if row is None:
        name, row = resolve(hwnd, index)
        out["name_guess"] = name
    out["row"] = row
    if row is None:
        out["why"] = "解析不出行号（缓存过期？先跑一次 state）"
        out["shot"] = _shot(hwnd, f"issue{index}_norow")
        return out
    try:
        res = cmdscreen.run_facility_command(
            hwnd, row, cmd, select_all=True, menu_index=menu)
    except Exception as e:
        out["why"] = f"run_facility_command 抛异常：{type(e).__name__}: {e}"
        out["shot"] = _shot(hwnd, f"issue{index}_err")
        return out
    one = res or {}
    out["result"] = one
    out["ok"] = bool(one.get("ok"))
    out["failed_at"] = one.get("failed_at")
    # 把"游戏自己报的设施名"提出来 —— 这是**点对了没有**的硬证据
    for st in (one.get("steps") or []):
        if st.get("step") == "verify_city":
            out["commanded"] = st.get("commanded")
    if not out["ok"]:
        out["why"] = (f"命令没下达：{one.get('failed_at')} "
                      f"{one.get('reason') or one.get('error') or one.get('note') or ''}")
        # ⛔ **先留证，再清理**（顺序不能反）。
        # 踩过：原来 `_shot` 放在 `recover` 之后 —— 失败时的截图拍的是
        # **恢复之后的画面**，等于没留证，排查时只能靠猜。这正是我们自己定的
        # "卡住先留证再动手"那条纪律，代码写反了。
        out["shot"] = _shot(hwnd, f"issue{row}_{cmd}_fail")
        # ⛔ **失败必清理**：命令界面很可能还开着，留着会把后面全带崩
        # （踩过：issue 失败 → 界面没关 → end_turn 在命令界面上跑 → 演出判据狂等 150s → 崩）
        out["recover"] = recover(hwnd)
    elif capture_result:
        # ⭐ 把**游戏自己报的结果**读回来。
        # 这是"命令真的生效了"的**最强证据**（比 commanded 更强 —— 那个只证明点对了设施），
        # 而且**不用等过旬**：設施 9 条是「立即」见效，结果当场就播报。
        # 顺带一个好处：等它自己消失，界面就干净了
        # （框在的时候 go 只有 0.16，会把战略面挡住，下一个工具会读成 blocked）。
        #
        # ⚠️ 下层（`cmdscreen._pick_and_finish`）**已经读过一次**了 —— 复用它，
        # 别在这儿再等一遍（那会白等 8 秒，还可能把好的结果覆盖掉）。
        rb = one.get("result_box")
        if not (rb or {}).get("found"):
            rb = capture_result_box(hwnd)
        out["result_box"] = rb
    out["hint"] = hint(hwnd)
    if out["ok"]:
        out["shot"] = _shot(hwnd, f"issue{row}_{cmd}")
    return out


# ------------------------------------------------------------------ 原子 5：过一旬

def end_turn(hwnd) -> dict:
    """过**一**旬。"""
    out: dict = {"ok": False}
    try:
        r = session_for(hwnd).end_turn(1, auto_answer=True)
    except Exception as e:
        out["why"] = f"end_turn 抛异常：{type(e).__name__}: {e}"
        out["shot"] = _shot(hwnd, "turn_err")
        return out
    out["result"] = r
    out["advanced"] = r.get("advanced")
    out["stopped_at"] = r.get("stopped_at")
    out["ok"] = bool(r.get("advanced"))
    if not out["ok"]:
        out["why"] = r.get("reason") or f"没推进（stopped_at={r.get('stopped_at')}）"
    try:
        out["topbar"] = {k: v for k, v in ocr.read_topbar(hwnd).items()
                         if k != "raw"}
    except Exception:
        out["topbar"] = {}
    out["hint"] = hint(hwnd)
    out["shot"] = _shot(hwnd, "turn")
    return out


# ------------------------------------------------------------------ 原子 6：读進行記錄

def read_journal(hwnd) -> dict:
    """开 情報→進行記錄，读回本旬事件（含开屏核实），关掉。"""
    try:
        r = journal.read(hwnd)
    except Exception as e:
        return {"ok": False, "why": f"journal.read 抛异常：{e}",
                "shot": _shot(hwnd, "journal_err")}
    r["shot"] = _shot(hwnd, "journal_after")
    return r


OPS = {"state": state, "select": select, "menu": menu,
       "issue": issue, "turn": end_turn, "journal": read_journal}
