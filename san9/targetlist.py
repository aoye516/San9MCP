# -*- coding: utf-8 -*-
"""「一覽」目标设施列表 —— 选一座**自势力**城当命令目标。

这是什么屏
==========
执行 移動 / 探索 / 登庸 / 輸送 这类需要"指一个目标设施"的命令时，游戏进入
「選擇設施」模式（底栏 `請選擇目標。`）。此时点右下的**一覽**按钮，弹出一张表：

入口链（⭐ 用户 2026-09-19 口述，以輸送为例。⛔ 不是我猜的，也别再改回猜）
--------------------------------------------------------------------------
| 步 | 动作 | 备注 |
|---|---|---|
| 1 | 都市命令菜单 → 主菜单**第 2 项 `軍事`** | `citymenu.MAIN_ITEMS[1]` |
| 2 | 进下级菜单 → **第 3 项 `輸送`** | `SUBMENU_ITEMS["軍事"][2]`。**Enter 和右键都能进下级** |
| 3 | 进「命令执行界面」 | 与巡察/徵兵那套是**同一种**界面 |
| 4 | 点「**執行武將**」蓝标签 | ⚠️ 武将列表**不自动弹**，必须点这个标签（`cmdscreen.open_officer_picker`） |
| 5 | 在「選擇武將」弹窗里点武将行 | ⭐⭐ **輸送是单选** ⇒ 点一下**自动回执行界面**，⛔ **不用点「決定」** |
| 6 | 点绿色「**執行**」按钮 | ⇒ 进入「選擇設施」模式，底栏变 `請選擇目標。` |
| 7 | 点「**一覽**」按钮 | ⇒ 本模块负责的那张表。**设施也是单选** |

⚠️ **第 5 步与设施类命令不同。** `cmdscreen` 头部记的设施类流程是
「点武将行（**可多选**）→ 点「決定」(绿) → 回执行界面」。輸送这类**单选**命令
**没有「決定」这一步** —— 点完就回去了。⇒ 写驱动时别无脑复用设施类的那段。

⚠️ 第 6 步的绿「執行」按钮 **y 随命令漂**（`cmdscreen` 实测：巡察 708~744、徵兵 740~770）
⇒ 必须靠**找绿色块**定位，⛔ 不许写死 y。

    表头 y≈274：  工作物 | (軍團) | 大将 | 士兵 | 伤兵 | 耐久 | 在任
    数据行 y=304 起，行距 30，**屏上 15 行**
    城名列 x≈300（左对齐，2 字城 300 / 3 字港 311）

⚠️ **只列自势力设施** —— 底栏原话「輸送士兵至**自势力**的設施」，且真机 15 行的
   「大将」列全是張角/張寶/張燕/程遠志（我方）。用户 2026-09-19 手跑独立确认。
   ⇒ 本模块**解锁不了** 燒夷/奪取/出征 这类打他势力城的命令，那是另一条通道（未知）。

为什么不用 `cmdscreen.resolve()` 按名字找行
------------------------------------------
`resolve()` 按名找行不稳（C 档坏件）。这里走**列表 + 键盘**：
亮度定当前行 → 算差值 → 按方向键 → 复核。零坐标点击。

⭐ 选行策略：**先一把按到位，再验一次**（用户 2026-09-19 明确要求）
----------------------------------------------------------------
用户原话：「没按一次复核一次，那这个工具也太慢了吧，是不是应该先尝试按到那个位置
再检查对不对呀？」

这**不违反**"禁止盲点"禁令，因为：
  · 按方向键**不改变游戏状态**，只挪高亮（与 `cityintel.enter_menu_item` 同理）
  · **高亮在第几行是可以算出来的**（亮度峰值，间隔 71，见下）
    ⇒ 纠偏是"当前行 - 目标行"的**减法**，不是"再试试看"
  · 真正不可逆的动作只有最后那一次 **Enter**，而 Enter 前必定复核一次

所以时序是：`读当前行 → 一次按完 |Δ| 下 → 读一次核对 → 不对就再按新的 Δ`
（最多纠偏 2 轮，仍不对就**停手不按 Enter**）。
⛔ 不是"按一下读一下"，也⛔ 不是"按完就 Enter"。

⭐ 判「高亮在第几行」= 亮度峰值（本屏定案）
-----------------------------------------
`scripts/probe_yilan_grid_fix.py` 在两帧实拍上现量：高亮行 mean≈102，其余行 24~31
⇒ **间隔 71**，两帧（高亮行0 / 高亮行11）都唯一命中。
比「都市武将」那屏用的底栏 hint 更硬 —— 这屏底栏说明字**不跟随高亮变**
（15 行都是同一句 `請選擇目標。`），所以**只能**靠亮度。

⛔⛔ 两条踩过的坑（都是"量到的东西 ≠ 以为量到的东西"）
-----------------------------------------------------
① **`ROW0_Y` 不是 294，是 304。** 上一轮 `probe_yilan_layout.py` 用两帧差影法量到 294，
   我当成文字行 y 用 ⇒ 归行时城名被容差全挡掉，误判成"这屏城名 OCR 读不出来"。
   真相：**差影量到的是高亮条上边缘，不是文字中心**，差 ~10px。
   现值由 15 个城名 y 做最小二乘拟合而来（残差 max 1.02px），⛔ 非目测非差影。
② **右下角 `地域：XX（YY）` 不跟随高亮变。** 我一度看到两帧读出 `中年（计昌` / `中牟`
   以为它跟着高亮走，实测**该区域两帧像素差 mean=0.00，一个像素都没变** —— 纯 OCR 抖动。
   与 `cityintel.CITY_TAG_IS_NOT_A_GATE` 同一条结论。⇒ ⛔ 不能当"当前选中哪座城"的判据。

⚠️ **单字城名 OCR 必读不出**（现量，两帧一致）
   `scripts/probe_yilan_row13.py`：行13（y=694）城名墨段 `x=282..299` 只有 **17px**，
   而南皮/陳留（2 字）是 36px ⇒ 17px ≈ **一个字**。该行城名列 OCR **全尺度交白卷**，
   而同一行「大将」列读出 `张燕` ⇒ 不是这行没内容，是**单字缺上下文 OCR 认不出**。
   ⇒ 这行走 `unread`，**这是正确行为不是 bug**。等 D8 汉字字形库才能读。
   ⛔ **不许从"我方有哪些城"去猜它是谁** —— 那是推断不是读数（我确实想猜「鄴」，忍住了）。

⛔ 不给数值列：`士兵 / 伤兵 / 耐久 / 在任` 那几列是数字，而**本屏没有字形模板库**。
   项目定案"数字 = 字形查表，读不出作废"⇒ 不建库就不给（同 `cityintel`）。
⚠️ 不翻页：屏上 15 行。用户确认"目前正好放下，后续放不下可能还得方向键翻"
   ⇒ 真实设施多于 15 个时会有滚动，**本模块尚未验证滚动**，见 `SCROLL_UNVERIFIED`。
"""
from __future__ import annotations

import time

import cv2
import numpy as np

from . import namelock, ocr

# ══ 版面常量 ══
# 全部由 scripts/probe_yilan_fullscan.py（全幅 OCR 现量）+
# scripts/probe_yilan_grid_fix.py（15 个城名 y 最小二乘拟合）得出。⛔ 非目测。
ROW0_Y = 304
ROW_PITCH = 30
N_SLOTS = 15
"""屏上可见行数（现量：y=304..724 共 15 行）。

⚠️ 这是**屏上槽位数**，不是"我方设施总数"。真实数目更多时要滚动，未验证。
"""

NAME_X0, NAME_X1 = 292, 360
"""城名列取字范围。现量城名左边界 300（2 字）/ 311（3 字）⇒ **左对齐**。"""

HL_X0, HL_X1 = 275, 843
"""高亮条宽度（`probe_yilan_layout.py` 两帧差影现量）。

⚠️ 这个范围**只用来测亮度**，⛔ 不是文字列范围（我上一轮就是混了这两件事，见模块头坑①）。
"""

HEAD_Y = 274
HEAD_WORDS = ("工作物", "大将", "大將", "士兵", "伤兵", "傷兵", "耐久", "在任")

CAPTION_REG = (400, 928, 1000, 960)
CAPTION_MARKS = ("請選擇目標", "请选择目标", "请遥择目標", "選擇目標", "择目標")
"""底栏说明字 —— 门禁①。现量原文 `請選擇目標。`（OCR 常读成 `请遥择目標`）。

⚠️ 这句**不跟随高亮变**（15 行都一样）⇒ 它只能判"在不在这屏"，
   ⛔ **判不了"高亮在第几行"**（那个只能靠亮度）。
"""

HINT_STRATEGY_IDLE = ("請選擇命令起點", "请选择命令起点", "请遥择命令起點",
                      "请送择命今起路", "命令起點", "命令起点", "令起")
"""⭐⭐ **`請選擇命令起點。` = 干净战略面的常态底栏提示**（不是异常、不是残留状态）。

⛔⛔ **2026-09-19 血案**：我把这句当成"上一轮遗留的半开命令"，在**干净战略面**上按 Esc，
   然后因为"画面没变"报了一条「Esc 按不动」的假结论，还停下来找维护者求助。
   维护者原话：「现在不就在战略面吗？你在干嘛」。**Esc 什么都没退是因为没东西要退。**

⇒ **教训：看到没见过的底栏提示，先查它是不是常态，别默认它代表异常。**
   OCR 变体实测有 `请遥择命令起點。` / `请送择命今起路，`（错到 3 个字）⇒ 匹配必须容错。
"""

TITLE_REG = (280, 130, 520, 160)
TITLE_MARKS = ("選擇設施", "选择设施", "挥设施", "捍设施")
"""左上标题 `選擇設施` —— 门禁②（现量 y≈142 x≈335）。"""

# 亮度定高亮行的判据。现量：高亮 mean≈102、其余 24~31 ⇒ 间隔 71。
# 阈值取得比实测间隔**保守得多**（要求 >=25），留足余量但仍能挡住"没有高亮"的情况。
HL_MIN_MARGIN = 25.0
HL_MIN_MEAN = 60.0

INK_THR = 60
INK_MIN = 3
ROW_OFF_TOL = 0.25
ROW_INK_MIN = 6
"""墨量定行参数。现量：15 行墨峰 19~26，`row=18`（列表下方「中止」按钮区）会被
`row < N_SLOTS` 挡掉 —— **这就是必须有行号上限的原因**。"""

SCROLL_UNVERIFIED = (
    "⚠️ **本模块只读屏上可见的 15 行，未实现也未验证滚动。** 用户确认当前存档"
    "「正好放下」，但设施变多后列表会需要方向键翻页。"
    "⇒ 如果 agent 要找的城不在返回的 `rows` 里，**不代表它不存在**，"
    "只代表它不在当前这一屏。⛔ 不要据此下「没有这座城」的结论。")

REAL_MACHINE_VERIFIED = (
    "✅ **2026-09-19 真机全链跑通（平原·輸送·周倉）**：門禁 ok（底栏+表头 2/3 票）· "
    "15 行全出 · 高亮 margin 70.81（与离线 71 一致）· "
    "`select_target` 连打 5 次任意跳转（0→11→3→14→0→9）**全部一次到位、零修正**，"
    "每次只 grab 2 帧 · 越界 row=99 正确拒绝且**一个键都没按** · "
    "Esc×2 干净退回战略面（29.11 → 41.96）。"
    "⇒ 「先按到位再复核」这条路子**在真机上成立**，不需要每按一次复核一次。")

CANDIDATE_IS_NOT_A_BUG = (
    "⚠️ **不传 `cities` 时 15 行全是 `candidate`，这是设计行为不是 bug。** "
    "namelock 没有候选表可锁 ⇒ 只能给原文。真机传表后 **10/15 行 locked**，"
    "剩下 3 行卡 candidate 是**形近/漏字**（`榮陵港`→樂陵港 0.667 · `東莱港`→東萊港 0.667 · "
    "`淄港`→臨淄港 0.8），2 行 unread 是单字城名。"
    "⇒ ⛔ **不许调低 `HIGH_RATIO` 把它们凑成 locked** —— 要补 `officers.json.aka` 别名，"
    "而别名只能来自实测帧 OCR 读数。")

OWN_FACTION_ONLY = (
    "⚠️ **这张表只列自势力设施。** 底栏原话「輸送士兵至**自势力**的設施」，"
    "真机 15 行「大将」列全是我方武将，用户 2026-09-19 手跑确认。"
    "⇒ 本通道**解锁不了** 燒夷/奪取/出征 这类需要指定**他势力**城的命令。")

TAG_IS_NOT_A_GATE = (
    "⛔ 右下角 `地域：XX（YY）` **不跟随高亮变** —— 实测两帧（高亮行0 / 高亮行11）"
    "该区域**像素差 mean=0.00**，一个像素都没变；之前看到的 `中年（计昌` vs `中牟` "
    "纯属 OCR 抖动。⇒ 它判不了「当前选中哪座城」。要知道选中哪行，只能用亮度。")


def _grab(hwnd):
    return ocr._grab_bgr(hwnd)


def _ocr_boxes(bgr, upscales=(2, 3)) -> list[dict]:
    out: list[dict] = []
    for up in upscales:
        try:
            out += ocr.read(bgr, upscale=up)
        except Exception:
            pass
    return out


def _region_text(bgr, reg, upscales=(3, 4)) -> str:
    txt = ""
    try:
        sub = bgr[reg[1]:reg[3], reg[0]:reg[2]]
        for up in upscales:
            txt += "".join(i["text"] or "" for i in ocr.read(sub, upscale=up))
    except Exception:
        pass
    return txt.strip()


def _text_near_y(bgr, y: int, tol: int = 14,
                 x_range: tuple[int, int] | None = None) -> str:
    """**全屏 OCR 之后按 (y,x) 命中**取某一行的文字。

    ⭐⭐ 这是本仓的定案手法，⛔ **不许裁小图单独 OCR** ——
       `city_officers` 早就实测过：裁 68×27 小图 5 行只读出 1 个，
       全屏 OCR 反而读出 4 个（OCR 靠周边上下文定位）。
    """
    out = []
    for b in _ocr_boxes(bgr, upscales=(2, 3)):
        t = (b.get("text") or "").strip()
        if not t or abs(b["y"] - y) > tol:
            continue
        if x_range and not (x_range[0] <= b["x"] <= x_range[1]):
            continue
        out.append((b["x"], t))
    return "".join(t for _x, t in sorted(out))


def bottom_hint(bgr) -> str:
    """底栏说明字原文。判"在不在这屏"，⛔ 判不了高亮在第几行（见 CAPTION_MARKS）。

    ⛔⛔ **2026-09-19 血案：本函数原来用 `_region_text(bgr, CAPTION_REG)`
       裁小图单独 OCR ⇒ 在一覽屏上读成空串**，害得 `check_target_list` 只拿到
       1/3 票、把"一覽屏明明开着"判成"没开到"，`san9_transport` 直接退屏 ——
       用户当场问「为什么不选设施，直接就退出了」。
       同一帧用**全屏 OCR 按 y≈940 命中**能稳稳读出 `請選擇目標。`。
    ⇒ **凡读游戏文字一律全屏 OCR + 坐标命中，裁小图是已知的错法。**
    """
    # ① 首选：全屏 OCR 按底栏 y 命中（稳）
    y = (CAPTION_REG[1] + CAPTION_REG[3]) // 2
    t = _text_near_y(bgr, y, tol=16, x_range=(CAPTION_REG[0] - 80, CAPTION_REG[2] + 80))
    if t:
        return t
    # ② 兜底：旧的裁图法（⚠️ 已知偶发读空，只当补充，读到空就如实返回空）
    return _region_text(bgr, CAPTION_REG)


def check_target_list(bgr) -> dict:
    """门禁：现在开着「一覽」目标列表吗？**三条件取二**，不过就什么都不按。

    ① 底栏说明字命中 `請選擇目標`
    ② 左上标题命中 `選擇設施`
    ③ 表头行 y≈274 能读到 >= 3 个表头词

    ⛔ **不用 `go`/`info`/`func` 锚点** —— 一览类屏保留战略面外框，锚点会假阳性
       （2026-09-19「全武將一覽」血案）。判据必须是**这屏自己的内容**。

    为什么"取二"而不是"全中"：底栏 OCR 在这屏偶发读空串（`cityintel` 同样踩过），
    三条里任何一条单独失手都不该让整个工具瘫掉；但只中一条也不敢往下按键。
    """
    hint = bottom_hint(bgr)
    hit_cap = [m for m in CAPTION_MARKS if m in hint]

    title = _region_text(bgr, TITLE_REG)
    hit_title = [m for m in TITLE_MARKS if m in title]

    boxes = _ocr_boxes(bgr)
    got = []
    for b in boxes:
        if abs(b["y"] - HEAD_Y) > 12:
            continue
        got += [w for w in HEAD_WORDS if w in (b["text"] or "")]
    got = sorted(set(got))

    votes = [bool(hit_cap), bool(hit_title), len(got) >= 3]
    ok = sum(votes) >= 2
    return {"ok": ok, "hint": hint, "hint_hit": hit_cap,
            "title": title, "title_hit": hit_title,
            "header_found": got, "votes": votes,
            "why": "" if ok else
                   ("不是「一覽」目标列表屏：底栏读到 %r（命中 %s）、标题读到 %r"
                    "（命中 %s）、表头 y≈%d 读到 %s ⇒ 三条判据只过 %d 条（需 >=2）"
                    "⇒ **停手，一个键都不按**"
                    % (hint, hit_cap, title, hit_title, HEAD_Y, got, sum(votes)))}


def highlight_row(bgr) -> dict:
    """**高亮在第几行**（0 起）。这是本模块的核心 —— 纠偏靠它算差值，不靠试。

    做法：对 15 个行槽位，量高亮条宽度内的平均亮度，取峰值。
    现量（两帧实拍）：高亮行 mean≈102、其余 24~31 ⇒ 间隔 71，唯一命中。

    ⛔ **不许目测高亮条**（禁令）。⛔ 不用底栏 hint —— 这屏 15 行说明字全一样。

    间隔不够（`HL_MIN_MARGIN`）或峰值太低（`HL_MIN_MEAN`）⇒ 返回 `row=None`，
    调用方**必须**因此停手：宁可不按，也不能按到不知道的地方去。
    """
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.int32)
    h = g.shape[0]
    means = []
    for n in range(N_SLOTS):
        y = ROW0_Y + ROW_PITCH * n
        if y + 8 >= h:
            break
        means.append((n, float(g[y - 14:y + 8, HL_X0:HL_X1].mean())))
    if not means:
        return {"row": None, "why": "画面高度 %d 放不下任何行槽位（抓错画面？）" % h}

    best = max(means, key=lambda t: t[1])
    rest = sorted((m for n, m in means if n != best[0]), reverse=True)
    margin = best[1] - (rest[0] if rest else 0.0)
    out = {"row": None, "peak_row": best[0], "peak_mean": round(best[1], 2),
           "runner_up": round(rest[0], 2) if rest else None,
           "margin": round(margin, 2),
           "all_means": [(n, round(m, 1)) for n, m in means]}
    if best[1] < HL_MIN_MEAN:
        out["why"] = ("最亮行 mean=%.2f < %.1f ⇒ **看不出哪行是高亮的**"
                      "（可能列表没开、或被弹窗盖住）⇒ 不按键"
                      % (best[1], HL_MIN_MEAN))
        return out
    if margin < HL_MIN_MARGIN:
        out["why"] = ("最亮行 %d (mean=%.2f) 与次亮 (%.2f) 只差 %.2f < %.1f ⇒ "
                      "**判不准高亮在哪** ⇒ 不按键。实测正常间隔约 71，"
                      "差这么小说明画面不对劲。"
                      % (best[0], best[1], rest[0], margin, HL_MIN_MARGIN))
        return out
    out["row"] = best[0]
    return out


def detect_data_rows(bgr) -> list[dict]:
    """**墨量定行**：城名列上"有内容"的行（0 起）。⛔ 不预设行数。

    高亮行是**亮底深字**，拿"比阈值亮"当墨会把整行算成墨
    ⇒ 用「与该行中位数的偏离」当墨（这一条是本屏特有的，`cityintel` 那屏没有亮底行）。

    `row < N_SLOTS` 上限是必需的：现量列表下方「中止」按钮区会产生 `row=18` 的墨块。
    """
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.int32)
    h = g.shape[0]
    band = g[:, NAME_X0:NAME_X1]
    med = np.median(band, axis=1, keepdims=True)
    ink = (np.abs(band - med) > INK_THR).sum(axis=1)

    out: list[dict] = []
    y = HEAD_Y + 12
    while y < h:
        if ink[y] >= INK_MIN:
            y0 = y
            while y < h and ink[y] >= INK_MIN:
                y += 1
            center = (y0 + y - 1) // 2
            off = (center - ROW0_Y) / ROW_PITCH
            row = int(round(off))
            err = abs(off - row)
            peak = int(ink[y0:y].max())
            out.append({"row": row, "y": ROW0_Y + ROW_PITCH * row,
                        "ink_y0": y0, "ink_y1": y - 1, "ink_center": center,
                        "ink_peak": peak, "off_err": round(err, 3),
                        "is_data": bool(0 <= row < N_SLOTS
                                        and peak >= ROW_INK_MIN
                                        and err < ROW_OFF_TOL)})
        else:
            y += 1
    return out


def _row_text(boxes, y: int) -> tuple[str | None, list[str]]:
    """取某一行城名列的文字。**全屏 OCR 按 (y, x) 命中**，⛔ 不裁小图。

    理由（`cityintel` 已验，同一条）：裁 68×27 小图后 5 行只读出 1 个，
    而全屏 OCR 读出 4 个 —— OCR 引擎要靠周边上下文定位文字框。
    """
    cand = []
    for b in boxes:
        if abs(b["y"] - y) > 12:
            continue
        if not (NAME_X0 - 12 <= b["x"] <= NAME_X1):
            continue
        t = (b["text"] or "").strip()
        if t:
            cand.append(t)
    if not cand:
        return None, []
    # 多尺度可能给出不同读数（如 `巢陵港` / `樂陵港`）⇒ 取出现最多的，全部原样附上
    best = max(set(cand), key=lambda t: (cand.count(t), len(t)))
    return best, sorted(set(cand))


def parse_target_list(bgr, cities=None) -> dict:
    """解析「一覽」列表：每行是哪座城 + 当前高亮在第几行。

    三类字段三种待遇（项目定案）：
      · 城名 = **选择器文字** ⇒ 过 `namelock` 三级判定，
        `candidate` **只给参考，禁止据此点城**（要点就用 row，别用名字）
      · 数值列（士兵/伤兵/耐久/在任）⇒ ⛔ **不给**（本屏无字形库）
      · 有墨读不出 ⇒ `unread`，**不删不猜**

    `cities` 是候选城名表（给 namelock 用）。不传就退化成"只给原文 + candidate"。
    """
    gate = check_target_list(bgr)
    out: dict = {"ok": False, "gate": gate,
                 "scroll_note": SCROLL_UNVERIFIED,
                 "faction_note": OWN_FACTION_ONLY,
                 "tag_note": TAG_IS_NOT_A_GATE}
    if not gate["ok"]:
        out["stage"] = "gate"
        out["why"] = gate["why"]
        return out

    hl = highlight_row(bgr)
    out["highlight"] = hl
    out["current_row"] = hl["row"]

    boxes = _ocr_boxes(bgr)
    blocks = [d for d in detect_data_rows(bgr) if d["is_data"]]
    raws = [_row_text(boxes, d["y"]) for d in blocks]

    # ⭐ 走 `lock_many` 而不是逐行 `lock`：同一屏不可能有两行是同一座城
    # ⇒ 唯一性分配能挡住"两行都锁到同一个名字"这种必错的读数（namelock 已实现）。
    locks = None
    if cities:
        locks = namelock.lock_many([r for r, _ in raws], cities, unique=True)

    rows, unread = [], []
    for i, d in enumerate(blocks):
        raw, variants = raws[i]
        item = {"row": d["row"], "y": d["y"], "ink_peak": d["ink_peak"],
                "raw": raw, "ocr_variants": variants,
                "is_current": bool(hl["row"] is not None and d["row"] == hl["row"])}
        if raw is None:
            # ⛔ 有墨读不出 ≠ 这行没内容。记 unread，不猜、不从列表里删。
            item["status"] = "unread"
            item["city"] = None
            item["note"] = ("这行有墨（峰值 %d）但 OCR 读不出城名 ⇒ **不是空行**，"
                            "是我读不出来。⛔ 不要当它不存在。" % d["ink_peak"])
            unread.append(d["row"])
        elif locks is not None:
            lk = locks[i]
            item["status"] = lk["status"]
            # ⚠️ 字段名是 `locked`（不是 `name`），且**只有 status=="locked" 时可用**
            item["city"] = lk.get("locked") if lk["status"] == "locked" else None
            item["lock"] = lk
        else:
            item["status"] = "candidate"
            item["city"] = None
            item["note"] = "没给候选城名表 ⇒ 只能给原文，⛔ 不许据此点城"
        rows.append(item)

    rows.sort(key=lambda r: r["row"])
    out["rows"] = rows
    out["n_rows"] = len(rows)
    out["unread"] = unread
    out["ok"] = True
    out["stage"] = "parse"
    if hl["row"] is None:
        # 读得到表但判不出高亮 ⇒ 表能给，但**不能用来选行**。如实说，不静默。
        out["select_blocked"] = True
        out["select_blocked_why"] = ("表读到了，但判不出高亮在第几行（%s）⇒ "
                                    "**不能用这次读数去按方向键**，因为算不出差值。"
                                    % hl.get("why"))
    return out


def select_target(hwnd, row: int, /, max_fix: int = 2) -> dict:
    """把高亮移到第 `row` 行（0 起）。**先一把按到位，再验一次，不对按差值纠偏。**

    用户 2026-09-19 明确要求这个时序（原话：「是不是应该先尝试按到那个位置再检查
    对不对呀？」）—— 按一次复核一次太慢。

    为什么这不算"盲点"（禁令不冲突，理由写在这里以免以后又被自己改回去）：
      ① 按 ↑↓ **不改变游戏状态**，只挪高亮；唯一不可逆的 Enter 不在本函数里
      ② **当前行号是算出来的**（`highlight_row`，亮度间隔 71）⇒ Δ = 目标 - 当前 是**减法**
      ③ 按完**必定复核**；复核读不出来就停手，⛔ 不猜不碰运气

    ⛔ 不做的事：不按坐标点行（列表行没有可靠的点击判据，键盘就够）；
                 `max_fix` 用尽仍不对 ⇒ 如实报 `ok:false`，**不继续按**。
    """
    from . import winio

    out: dict = {"ok": False, "want_row": row, "presses": [], "why": ""}
    if not isinstance(row, int) or row < 0 or row >= N_SLOTS:
        out["why"] = ("row=%r 越界：本屏只有 %d 个可见槽位（0..%d）。%s"
                      % (row, N_SLOTS, N_SLOTS - 1, SCROLL_UNVERIFIED))
        return out

    bgr = _grab(hwnd)
    if bgr is None:
        out["why"] = "抓不到画面 ⇒ 不知道高亮在哪，**一个键都不按**"
        return out
    gate = check_target_list(bgr)
    out["gate"] = gate
    if not gate["ok"]:
        out["stage"] = "gate"
        out["why"] = gate["why"]
        return out

    hl = highlight_row(bgr)
    out["highlight_before"] = hl
    if hl["row"] is None:
        out["stage"] = "highlight_before"
        out["why"] = "按键前判不出高亮在第几行 ⇒ 算不出要按几下 ⇒ 停手。%s" % hl["why"]
        return out

    cur = hl["row"]
    for attempt in range(max_fix + 1):
        delta = row - cur
        if delta == 0:
            out["ok"] = True
            out["final_row"] = cur
            out["stage"] = "done"
            out["note"] = ("高亮已在第 %d 行。⚠️ 本函数**不按 Enter** —— "
                           "确认目标是调用方的事（要先核对城名）。" % row)
            return out

        key = "down" if delta > 0 else "up"
        n = abs(delta)
        # ⭐ 一把按完，中间不复核（这就是用户要的"先按到位"）
        for _ in range(n):
            winio.press(key)
            time.sleep(0.09)
        time.sleep(0.5)
        out["presses"].append({"attempt": attempt, "key": key, "n": n,
                              "from_row": cur, "want_row": row})

        bgr = _grab(hwnd)
        if bgr is None:
            out["stage"] = "verify"
            out["why"] = ("按了 %d 次 %s 之后抓不到画面 ⇒ 不知道停在哪了。"
                          "⛔ 不再按键。" % (n, key))
            return out
        hl = highlight_row(bgr)
        out["highlight_after"] = hl
        if hl["row"] is None:
            out["stage"] = "verify"
            out["why"] = ("按了 %d 次 %s 之后判不出高亮在第几行（%s）⇒ "
                          "**算不出还差几下** ⇒ 停手如实报。" % (n, key, hl["why"]))
            return out
        cur = hl["row"]
        if cur == row:
            out["ok"] = True
            out["final_row"] = cur
            out["stage"] = "done"
            out["note"] = ("高亮到第 %d 行（按了 %d 次 %s，复核 %d 次）。"
                           "⚠️ 本函数**不按 Enter**。"
                           % (row, n, key, attempt + 1))
            return out
        # 没到位 ⇒ 下一轮的 Δ 是**重新算出来的**，不是"多按一下试试"
        out["presses"][-1]["landed_row"] = cur

    out["stage"] = "fix_exhausted"
    out["final_row"] = cur
    out["why"] = ("纠偏 %d 轮后高亮停在第 %d 行，目标是第 %d 行 ⇒ 没到位，如实报。"
                  "按键记录 %s。可能原因：列表需要滚动（%s）、或方向键在边界被吃掉。"
                  "⛔ 不继续按、⛔ 不按 Enter。"
                  % (max_fix, cur, row, out["presses"], SCROLL_UNVERIFIED))
    return out


# ══════════════════════════════════════════════════════════════════════
# 开屏 / 退屏 —— 全部步骤 2026-09-19 真机一步一验跑过（平原·輸送·周倉）
# ══════════════════════════════════════════════════════════════════════

# ⭐⭐ 游戏用底栏告诉你「这条命令为什么不能用」。真机实测（2026-09-19）：
# 把平原的兵输送走之后再选 輸送，底栏直接写 `沒有士兵。`
# ⇒ **「命令不可用」与「我没定位对」是两件事，必须分开报**，
#    前者是合法答案（换城 / 先招兵），后者才是 bug。
NO_SOLDIER_MARKS = ("沒有士兵", "没有士兵", "有士兵。", "沒有兵", "没有兵")

OFFICER_TAB_XY = (410, 400)
"""「執行武將」标签。**两路互证**才敢点：
`cmdscreen.find_officer_tab` 颜色定位返回 (410,400)，同时全屏 OCR 在 y=402 x=421
读出 `轨行武将`（=執行武將）⇒ 颜色块和文字框对上了。

⚠️ 说明书语义（本 SKILL 第 11 节）：本旬只剩 1 名可行动武将时**这个标签是灰的、
不用点**，游戏已自动选中。⇒ 点之前用 `find_officer_tab` 复核，返回 None 就跳过这步。
"""

HINT_PICK_OFFICER = ("請選擇執行武將", "请选择执行武将", "请遥择执行武养",
                     "择执行武", "選擇執行武將",
                     # ⚠️ `執`→`轨`、`将`→`養/养`：同一句实测读出过至少 3 种变体
                     "轨行武將", "轨行武将", "择执行武养", "行武將", "行武将")
"""点完标签后底栏会变成这句 —— 这是"武将列表开了"的判据。

⚠️ **OCR 变体多到必须靠短锚字**（真机实测同一句读出 `请遥择执行武养·` /
`请遥择轨行武將。`）⇒ 表里既有长句也有 `行武將` 这种短锚。
⛔ 第一版我只写了长变体，结果**列表明明开了却报"没开"** —— 判据太严和太松一样坏。
"""


def open_via_transport(hwnd, officer_row: int = 0, /, stop_after_officer: bool = False) -> dict:
    """从**干净战略面**一路开到「一覽」目标列表（走 軍事 → 輸送）。

    ⭐ 这条链的每一步都是 2026-09-19 真机一步一验跑出来的，不是推的：

    | 步 | 动作 | 判据（现量） |
    |---|---|---|
    | 1 | `find_city_on_map` → 点都市 → `down`×1 选軍事 → `right` 展开 → `down`×2 选輸送 | 底栏 `輸送士兵至自勢力的設施` |
    | 2 | Enter | 变化 41.04；red「中止」(838,693) |
    | 3 | 点「執行武將」标签 | 底栏变 `請選擇執行武將` |
    | 4 | 点第 `officer_row` 行 | **自动回执行界面**（单选，⛔ 无「決定」键）+ **green「執行」从灰变绿** |
    | 5 | 点 green「執行」 | `check_target_list` ok:true |

    ⛔ **不用 `cmdscreen.open_command`** —— 它的灰项门禁在这条链上**误报**
       （`cmdmenu.read_menu` 读到 0/8 项就判"命令不可用"，而底栏明明写着可用）。
       本函数改用**底栏说明字**当门禁，那是游戏自己写的状态通道，比灰字检测硬得多。

    ⚠️ **本函数不提交任何命令。** 停在「選擇設施」这一屏，Enter 由调用方决定按不按。
       在这一步 `close_transport()` 是**零代价**的（还没选目标）。
    """
    from . import cmdscreen, winio

    out: dict = {"ok": False, "steps": [], "stage": None, "why": ""}

    def _step(tag, before, extra=None):
        after = _grab(hwnd)
        d = float(np.abs(after.astype(int) - before.astype(int)).mean()) if (
            before is not None and after is not None) else None
        rec = {"step": tag, "changed": round(d, 2) if d is not None else None,
               "hint": bottom_hint(after) if after is not None else ""}
        if extra:
            rec.update(extra)
        out["steps"].append(rec)
        return after, rec

    # ── 1. 点都市 → 键盘走到 軍事/輸送 ──────────────────────────────
    xy = cmdscreen.find_city_on_map(hwnd)
    if xy is None:
        out["stage"] = "find_city"
        out["why"] = ("地图上没定位到都市（灰色石墙连通块） ⇒ **什么都不点**。"
                      "可能不在战略面，或当前城不在画面中央。")
        return out
    out["city_xy"] = xy

    before = _grab(hwnd)
    winio.click_client(hwnd, xy[0], xy[1], settle=0.35)
    time.sleep(1.6)
    # 鼠标移开，否则 hover 高亮污染键盘读数（`open_command` 同样这么做）
    l, t, _r, _b = winio.client_rect_screen(hwnd)
    winio.move_cursor_input(l + 700, t + 840)
    time.sleep(0.6)
    winio.press("down")            # 主菜单 設施(0) → 軍事(1)
    time.sleep(0.8)
    winio.press("right")           # 展开軍事子菜单（⚠️ 鼠标悬停不展开，必须按 right）
    time.sleep(1.2)
    winio.press("down", 2, 0.25)   # 出征(0) → 建設(1) → 輸送(2)
    time.sleep(1.0)
    _bgr, rec = _step("menu_to_transport", before, {"city_xy": xy})

    # ⭐ 门禁用底栏说明字，⛔ 不用灰字检测（`open_command` 在这儿误报过）
    if not any(m in rec["hint"] for m in ("輸送士兵", "输送士兵", "送士兵")):
        out["stage"] = "menu_to_transport"
        # ⭐⭐ 游戏会把**真正的原因**写在底栏，要原样转给 agent，⛔ 不要一律归因成"我没点对"。
        # 真机 2026-09-19：刚把平原的兵全送走之后再跑，底栏就是「沒有士兵。」
        # ⇒ 这是**命令确实不可用**（灰项），不是定位失败。两者必须区分开。
        blocked = [m for m in NO_SOLDIER_MARKS if m in rec["hint"]]
        if blocked:
            out["stage"] = "command_unavailable"
            out["game_says"] = rec["hint"]
            out["why"] = ("⛔ **游戏自己说这条命令现在不能用**：底栏原话 %r。"
                          "（真机成因：这座城的士兵刚被送走 ⇒ 没兵可送，輸送变灰项。）"
                          "⇒ **不是定位失败，也不是 bug** —— 换一座有兵的城、"
                          "或先招兵再来。**一个键都没多按。**" % rec["hint"])
            return out
        out["why"] = ("键盘走到「軍事→輸送」之后，底栏没出现「輸送士兵至自勢力的設施」"
                      "（读到 %r）⇒ **不按 Enter**。可能菜单没开、或项序不对。"
                      % rec["hint"])
        return out

    # ── 2. Enter 进輸送执行界面 ─────────────────────────────────────
    before = _grab(hwnd)
    winio.press("enter")
    time.sleep(1.6)
    _bgr, rec = _step("enter_transport", before)
    red = cmdscreen.find_button(hwnd, "red", 600, 900)
    rec["red_abort"] = red
    if red is None:
        out["stage"] = "enter_transport"
        out["why"] = ("进輸送执行界面后没找到红色「中止」按钮 ⇒ 没进去。"
                      "⛔ 停手（界面可能还停在菜单上，调 san9_recover）。")
        return out

    # ── 3. 点「執行武將」标签 ───────────────────────────────────────
    tab, tab_how = cmdscreen.find_officer_tab(hwnd)
    out["officer_tab"] = {"xy": tab, "how": tab_how}
    if tab is None:
        # ⭐ 说明书语义：只剩 1 名可行动武将时标签是灰的、游戏已自动选中 ⇒ 跳过
        out["steps"].append({"step": "officer_tab", "skipped": True,
                             "note": "标签定位不到 ⇒ 按「只剩 1 人、游戏已自动选中」处理，"
                                     "不点。下一步直接验「執行」是不是绿的。"})
    else:
        before = _grab(hwnd)
        winio.click_client(hwnd, tab[0], tab[1], settle=0.35)
        time.sleep(1.5)
        _bgr, rec = _step("open_picker", before, {"clicked": tab, "how": tab_how})
        if not any(m in rec["hint"] for m in HINT_PICK_OFFICER):
            out["stage"] = "open_picker"
            out["why"] = ("点了「執行武將」标签但底栏没变成「請選擇執行武將」"
                          "（读到 %r）⇒ 列表没开。⛔ 停手。" % rec["hint"])
            return out

        # ── 4. 点武将行（单选，⛔ 不按「決定」）────────────────────
        picks = cmdscreen.picker_officers(hwnd)
        out["officers"] = picks
        if officer_row >= len(picks):
            out["stage"] = "pick_officer"
            out["why"] = ("officer_row=%d 越界：列表只有 %d 人（%s）⇒ **不点**。"
                          % (officer_row, len(picks),
                             [p.get("name") for p in picks]))
            return out
        tgt = picks[officer_row]
        before = _grab(hwnd)
        winio.click_client(hwnd, cmdscreen.PICK_ROW_X, tgt["y"], settle=0.35)
        time.sleep(1.5)
        _bgr, rec = _step("pick_officer", before,
                          {"row": officer_row, "name_raw": tgt.get("name"),
                           "clicked_y": tgt["y"]})

    # ── 5. 选将后先停住：这里就是**士兵数量屏**的入口 ───────────────
    if stop_after_officer:
        bgr = _grab(hwnd)
        out["ok"] = True
        out["stage"] = "after_officer"
        out["hint"] = bottom_hint(bgr)
        out["note"] = ("已选执行武将，**按用户实机确认，这里下一步是选士兵数量并按决定**。"
                        "本函数按 stop_after_officer=true 停手，绝不直接点绿色執行。")
        return out

    # ── 6. 点绿色「執行」───────────────────────────────────────────
    # ⭐ 「執行」由灰变绿本身就是"武将真的选上了"的判据（真机：选人前 green=None）
    green = cmdscreen.find_button(hwnd, "green", 600, 900)
    out["green_execute"] = green
    if green is None:
        out["stage"] = "execute_grey"
        out["why"] = ("「執行」按钮仍然不是绿的 ⇒ **武将没选上**（选人前它是灰的，"
                      "这正是本判据的分辨力）。⛔ 不点，免得白点一次。")
        return out

    before = _grab(hwnd)
    winio.click_client(hwnd, green[0], green[1], settle=0.35)
    time.sleep(1.8)
    bgr, rec = _step("click_execute", before, {"clicked": green})

    # ⭐⭐ **门禁要轮询，不能一帧定生死**（2026-09-19 血案，用户当场指出"你没选设施啊"）：
    #    南皮那次点完「執行」，一覽屏**其实已经开出来了**（失败现场截图里标题「選擇設施」、
    #    底栏 `請選擇目標`、15 行数据全在），但我只等 1.8s 就读，那一帧**底栏还没画出来**
    #    ⇒ 门禁 1/3 判失败 ⇒ 把"成功"报成"没开到"，还白退了一次屏。
    #    ⇒ 凡"等界面出现"的判断**一律轮询到超时**，⛔ 不许睡一个固定时长就下结论。
    gate = check_target_list(bgr)
    polls = [{"t": 1.8, "ok": gate["ok"], "hint": gate.get("hint")}]
    for _i in range(6):                     # 最多再等 ~4.8s
        if gate["ok"]:
            break
        time.sleep(0.8)
        bgr = _grab(hwnd)
        gate = check_target_list(bgr)
        polls.append({"t": round(1.8 + 0.8 * (_i + 1), 1),
                      "ok": gate["ok"], "hint": gate.get("hint")})
    rec["gate_polls"] = polls
    out["gate"] = gate
    if not gate["ok"]:
        out["stage"] = "target_list"
        out["why"] = ("点了「執行」后**轮询 %.1f 秒**仍没进「選擇設施」一覽屏：%s"
                      % (polls[-1]["t"], gate["why"]))
        return out

    out["ok"] = True
    out["stage"] = "done"
    out["note"] = ("已停在「選擇設施」一覽屏。⚠️ **什么都还没提交** —— "
                   "现在 `close_transport()` 是零代价的。")
    return out


# ══════════════════════════════════════════════════════════════════════
# 「決定方針」确认屏 —— 一覽屏按 Enter 之后的**最后一屏**（真机实测 2026-09-19 17:3x）
# ══════════════════════════════════════════════════════════════════════

# 底栏说明字：`決定部隊行動方針。`（OCR 实读 `决定部隙行動方针` / `决定部隧行助方针`
# —— 隊 字读法极不稳，⇒ **锚点只用稳的那几个字**）
HINT_PLAN_SCREEN = ("決定部隊行動方針", "决定部队行动方针",
                    "决定部隙行動方针", "决定部隧行助方针",
                    "行動方針", "行動方针", "行动方针", "動方", "行助方")

HINT_SOLDIER_DIALOG = ("決定士兵", "决定士兵", "定士兵")
# 2026-09-19 真机 OCR 锁定的数字键盘中心（客户区 1280×960）。
# 标签 OCR 现量：`最小`≈(485,392)、`消去`≈(698,472)、`00`≈(700,711)、`执行`≈(709,773)。
# 数字键盘三列、四行：7/8/9 → 4/5/6 → 1/2/3 → 0/00。
SOLDIER_KEY_X = (563, 642, 721)
SOLDIER_KEY_Y = (532, 593, 653)
SOLDIER_ZERO_XY = (578, 711)
SOLDIER_DOUBLE_ZERO_XY = (703, 711)


# 这一屏的字段行（真机 OCR 现量的 y，容差 ±12）。⚠️ 只当**读数**用，不当点击坐标。
PLAN_ROWS = {
    "命令設施": 424,   # 起点城（x≈515 是值）
    "目標": 483,       # 目标城（x≈607 是值，形如 `安德港（平原）`）
    "到達預定": 424,   # 与命令設施同行，x≈834 是值（形如 `8日`）
    "自主撤退": 533,   # x≈673 是值（`許可` / `不許可`）
}

PLAN_NOT_SOLDIER_COUNT = (
    "⛔ **这一屏没有士兵数量。** 屏上那两个 `1000`（y≈330 x≈207 与 y≈830 x≈968）"
    "是**地图上两个港口的兵力标注**，跟弹窗无关 —— 用户 2026-09-19 当场纠正我。\n"
    "⇒ **士兵数量在更前面一屏**（选完执行武将、选目标城之前）。本模块尚未做那一屏。"
)


def check_plan_screen(bgr) -> dict:
    """在不在「決定方針」确认屏？**判据 = 底栏说明字 + 绿/红按钮成对存在。**

    ⛔ 不用 `go` 锚点 —— 这屏保留战略面外框（与「全武將一覽」同病）。
    """
    hint = bottom_hint(bgr)
    hits = [m for m in HINT_PLAN_SCREEN if m in hint]
    out = {"ok": bool(hits), "hint": hint, "hint_hit": hits, "why": ""}
    if not hits:
        out["why"] = ("底栏读到 %r，没有「決定部隊行動方針」的任何锚点 ⇒ "
                      "**不在确认屏**，什么都不点。" % hint)
    return out


def read_plan(bgr) -> dict:
    """读确认屏上的四个字段。**读不出就写 `None`，⛔ 不猜。**"""
    boxes = _ocr_boxes(bgr, upscales=(2, 3))
    out: dict = {"fields": {}, "raw_rows": {}}
    for name, y in PLAN_ROWS.items():
        got = [(b["x"], (b.get("text") or "").strip()) for b in boxes
               if abs(b["y"] - y) <= 12 and (b.get("text") or "").strip()]
        out["raw_rows"][name] = sorted(got)
    # 目標行：取标签右侧最长的那段当值
    tg = [t for x, t in out["raw_rows"].get("目標", []) if x > 520]
    out["fields"]["target_raw"] = max(tg, key=len) if tg else None
    cs = [t for x, t in out["raw_rows"].get("命令設施", []) if 480 < x < 620]
    out["fields"]["from_raw"] = max(cs, key=len) if cs else None
    eta = [t for x, t in out["raw_rows"].get("到達預定", []) if x > 780]
    out["fields"]["eta_raw"] = max(eta, key=len) if eta else None
    rt = [t for x, t in out["raw_rows"].get("自主撤退", []) if x > 600]
    out["fields"]["retreat_raw"] = max(rt, key=len) if rt else None
    out["note"] = PLAN_NOT_SOLDIER_COUNT
    return out


def _ocr_text_at(bgr, x0: int, y0: int, x1: int, y1: int) -> str:
    """全屏 OCR 后拼接指定区域，避免裁小图 OCR 误读。"""
    got = []
    for b in _ocr_boxes(bgr, upscales=(2, 3)):
        t = (b.get("text") or "").strip()
        if t and x0 <= b["x"] <= x1 and y0 <= b["y"] <= y1:
            got.append((b["x"], t))
    return "".join(t for _x, t in sorted(got))


def _digits_from_text(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())


def set_soldier_quantity(hwnd, amount: int, /) -> dict:
    """在选将之后的「士兵」弹窗设置数量，并确认回到输送执行屏。

    真机实测：执行武将后点击紫色「士兵」(约 421,562) →
    底栏 `決定士兵` → 数字弹窗；当前值/上限在约 (652,221)/(772,221)，
    `消去` 约 (698,472)，数字键盘，绿色 `執行` 约 (709,773)。
    """
    from . import winio, cmdscreen
    out = {"ok": False, "stage": None, "why": "", "amount": amount}
    if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
        out.update(stage="bad_amount", why="士兵数量必须是正整数")
        return out
    b0 = _grab(hwnd)
    # 紫色「士兵」按钮：先用全屏 OCR 文字框锁定，不猜坐标
    boxes = _ocr_boxes(b0, upscales=(2, 3))
    soldier = next(((b["x"], b["y"]) for b in boxes
                    if "士兵" in (b.get("text") or "") and 390 <= b["y"] <= 590), None)
    if soldier is None:
        out.update(stage="soldier_button", why="找不到「士兵」按钮 ⇒ 不点")
        return out
    winio.click_client(hwnd, soldier[0], soldier[1], settle=0.3)
    time.sleep(0.9)
    b1 = _grab(hwnd)
    hint = bottom_hint(b1)
    if not any(m in hint for m in HINT_SOLDIER_DIALOG):
        out.update(stage="soldier_dialog", why="点击士兵后未进入数量弹窗：%r" % hint)
        return out
    out["opened"] = True
    # 数量框当前值：全屏 OCR 坐标 y≈221，x≈620..790
    # 两个数字框是「当前值」(x≈657) 与「上限」(x≈802)。OCR 每个框会以 2/3 倍率
    # 重复返回，不能把重复文字直接拼接；本函数每次都清除并重新输入，故不依赖当前值识别。
    nums = []
    for b in _ocr_boxes(b1, upscales=(2, 3)):
        digits = _digits_from_text(b.get("text"))
        if digits and 610 <= b["x"] <= 850 and 300 <= b["y"] <= 370:
            if not any(abs(b["x"] - x) < 12 and abs(b["y"] - y) < 12 for x, y, _ in nums):
                nums.append((b["x"], b["y"], digits))
    nums.sort()
    out["current_boxes"] = [{"x": x, "y": y, "digits": d} for x, y, d in nums]
    out["current"] = int(nums[0][2]) if nums else None

    # **总是**清除再输入，避免 OCR 把 15000 读成 10000 时把错误值当成已就绪。
    # 真机客户区按钮中心：消去 (698,472)；数字键盘 7/8/9 行 y=532，
    # 4/5/6 y=596，1/2/3 y=660，0/00 y=711。
    winio.click_client(hwnd, 698, 472, settle=0.15)
    time.sleep(0.2)
    key_xy = {"7": (563, 532), "8": (642, 532), "9": (721, 532),
              "4": (563, 596), "5": (642, 596), "6": (721, 596),
              "1": (563, 660), "2": (642, 660), "3": (721, 660),
              "0": (578, 711)}
    for ch in str(amount):
        xy = key_xy.get(ch)
        if xy is None:
            out.update(stage="keypad", why="数字键盘没有可定位的按键 %s" % ch)
            return out
        winio.click_client(hwnd, *xy, settle=0.1)
    time.sleep(0.4)
    b2 = _grab(hwnd)
    verify_boxes = []
    for b in _ocr_boxes(b2, upscales=(2, 3)):
        digits = _digits_from_text(b.get("text"))
        if digits and 610 <= b["x"] <= 720 and 300 <= b["y"] <= 370:
            if not any(abs(b["x"] - x) < 12 and abs(b["y"] - y) < 12 for x, y, _ in verify_boxes):
                verify_boxes.append((b["x"], b["y"], digits))
    verify_boxes.sort()
    verify = verify_boxes[0][2] if verify_boxes else ""
    out["after_input"] = verify
    if verify != str(amount):
        out.update(stage="quantity_verify", why="数量复核未精确读到 %d：%r ⇒ 不确认" % (amount, verify))
        return out
    # 数量弹窗的绿色按钮颜色与普通执行栏不同，`find_button(green)` 真机曾读不到；
    # 这里用**全屏 OCR 文字框**锁定「执行」(y≈773)，再点击文字框中心。
    qboxes = _ocr_boxes(b2, upscales=(2, 3))
    green_box = next((b for b in qboxes
                      if "执行" in (b.get("text") or "") and 730 <= b["y"] <= 810), None)
    green = (green_box["x"], green_box["y"]) if green_box else None
    red_box = next((b for b in qboxes
                    if "中止" in (b.get("text") or "") and 730 <= b["y"] <= 810), None)
    red = (red_box["x"], red_box["y"]) if red_box else None
    out["buttons"] = {"green": green, "red": red,
                       "green_box": green_box, "red_box": red_box}
    if green is None:
        out.update(stage="quantity_execute", why="数量弹窗全屏 OCR 找不到绿色执行文字 ⇒ 不点")
        return out
    before = _grab(hwnd)
    winio.click_client(hwnd, green[0], green[1], settle=0.3)
    time.sleep(1.0)
    after = _grab(hwnd)
    out["changed"] = round(float(np.abs(after.astype(int) - before.astype(int)).mean()), 2)
    out["hint_after"] = bottom_hint(after)
    # 回到输送执行屏：数量行再次出现，且弹窗底栏消失
    text_after = _ocr_text_at(after, 380, 520, 720, 590)
    if "士兵" not in text_after or str(amount) not in _digits_from_text(text_after):
        out.update(stage="quantity_confirm", why="确认后未回到输送执行屏，读到 %r ⇒ 不继续选设施" % text_after)
        return out
    out["ok"] = True
    out["stage"] = "done"
    out["note"] = "已选择士兵数量 %d 并按数量弹窗的执行；现在才允许进入选择目标设施。" % amount
    return out


def execute_plan(hwnd, /, expect_target: str | None = None) -> dict:
    """在「決定方針」确认屏上点绿色「執行」—— **这一步真的把命令提交给游戏。**

    ⭐ 真机实测链（2026-09-19 17:3x，一步一验跑出来的）：

    | 步 | 动作 | 判据（现量） |
    |---|---|---|
    | 0 | （前置）一覽屏选好目标 → Enter | 底栏变 `決定部隊行動方針`，变化 28.16 |
    | 1 | `find_button(green)` | (708,612)；红「中止」(818,613) —— **成对存在** |
    | 2 | 点绿「執行」 | 变化 **45.0**，底栏清空，`green` 跳回 (1215,864)=战略面進行键 |
    | 3 | 读屏 | 弹事件框「周倉 成爲平原的大將了」⇒ **命令已提交** |

    ⚠️ **提交后常弹事件框**，且它可能自己消失（真机第 4 步 Enter 只变了 0.11）
       ⇒ 收尾判据一律是**底栏回到 `請選擇命令起點`**，⛔ 不是"按了几次键"。

    ⭐ **独立证据（游戏自己的账）**：`san9_journal` 读到
       「因輸送行動，濮陽的士兵增為 49735。」「廖化完成輸送到濮陽的任務了。」
       ⇒ 输送链在游戏里真实生效，不是"看起来点上了"。

    `expect_target`：传了就**先只读核对**确认屏上的目标城，不符 ⇒ **一下都不点**。
    """
    from . import winio, cmdscreen

    out: dict = {"ok": False, "stage": None, "touched_game": False, "why": ""}

    bgr = _grab(hwnd)
    gate = check_plan_screen(bgr)
    out["gate"] = gate
    if not gate["ok"]:
        out["stage"] = "plan_gate"
        out["why"] = gate["why"]
        return out

    plan = read_plan(bgr)
    out["plan"] = plan

    # ── 只读预检：目标不符就零动作退出 ────────────────────────────
    if expect_target is not None:
        got = plan["fields"].get("target_raw")
        if not got or expect_target not in got:
            out["stage"] = "expect_target_mismatch"
            out["why"] = ("确认屏的目标读作 %r，不含你要的 %r ⇒ "
                          "**一下都没点**。⛔ 宁可不提交，也不提交到错的城。"
                          % (got, expect_target))
            return out

    green = cmdscreen.find_button(hwnd, "green", 500, 900)
    red = cmdscreen.find_button(hwnd, "red", 500, 900)
    out["buttons"] = {"green_execute": green, "red_abort": red}
    if green is None:
        out["stage"] = "no_green"
        out["why"] = ("确认屏上找不到绿色「執行」按钮 ⇒ **什么都不点**。"
                      "（红「中止」= %r，两个应当成对存在）" % (red,))
        return out

    before = _grab(hwnd)
    winio.click_client(hwnd, green[0], green[1], settle=0.35)
    out["touched_game"] = True
    time.sleep(2.0)
    after = _grab(hwnd)
    d = float(np.abs(after.astype(int) - before.astype(int)).mean())
    out["changed"] = round(d, 2)
    out["hint_after"] = bottom_hint(after)

    # ⭐ 提交成功的判据：确认屏的门禁**不再成立**（底栏不再是行動方針）
    still = check_plan_screen(after)
    if still["ok"]:
        out["stage"] = "submit"
        out["why"] = ("点了「執行」但底栏还是 %r ⇒ **没提交成功**，⛔ 不重复点。"
                      % still["hint"])
        return out

    out["ok"] = True
    out["stage"] = "submitted"
    out["note"] = ("命令**已提交**（画面变化 %.2f，确认屏已消失）。"
                   "⚠️ 之后常弹事件框，可能自己消失 ⇒ 用 `close_transport()` "
                   "以底栏 `請選擇命令起點` 为判据收尾，⛔ 别数按键次数。"
                   % d)
    return out


def close_transport(hwnd, /, max_esc: int = 3) -> dict:
    """从一覽屏退回干净战略面。**Esc 一次一验，退到位就停。**

    真机实测（2026-09-19）：Esc×1 退出一覽（变化 29.11，门禁转 False）→
    Esc×2 退出輸送执行界面（变化 41.96，底栏回 `請選擇命令起點`）。

    ⭐ **判据是底栏回到 `請選擇命令起點`**（战略面的常态提示），
       ⛔ 不是"按了几次" —— 按次数当判据就是盲按。

    ⚠️ 2026-09-19 血案：我曾把 `請選擇命令起點。` 误当成"卡住的残留状态"，
       在干净战略面上按 Esc 然后报"Esc 按不动"。**这句就是战略面的常态底栏。**
    """
    from . import winio

    out: dict = {"ok": False, "escs": [], "why": ""}
    for k in range(max(1, max_esc)):
        before = _grab(hwnd)
        hint = bottom_hint(before)
        if any(m in hint for m in HINT_STRATEGY_IDLE):
            out["ok"] = True
            out["note"] = "底栏已是 %r（战略面常态提示）⇒ 已经干净，按了 %d 次 Esc。" % (hint, k)
            return out
        winio.press("escape")
        time.sleep(1.2)
        after = _grab(hwnd)
        d = float(np.abs(after.astype(int) - before.astype(int)).mean())
        rec = {"esc": k + 1, "changed": round(d, 2), "hint": bottom_hint(after)}
        out["escs"].append(rec)
        # ⛔ 按了屏幕一点没变 ⇒ 立刻停手（同一状态上重复按是白按）
        if d < 2.0:
            out["why"] = ("第 %d 次 Esc 后画面几乎没变（%.2f）⇒ 这一层退不掉，"
                          "**停手不连按**。底栏 %r。" % (k + 1, d, rec["hint"]))
            return out

    hint = bottom_hint(_grab(hwnd))
    if any(m in hint for m in HINT_STRATEGY_IDLE):
        out["ok"] = True
        out["note"] = "按了 %d 次 Esc 后底栏回到 %r ⇒ 干净战略面。" % (max_esc, hint)
        return out
    out["why"] = ("按了 %d 次 Esc 底栏仍是 %r，没回到战略面常态提示 ⇒ "
                  "如实报，⛔ 不加码乱按。调 san9_recover 或请人工介入。" % (max_esc, hint))
    return out
