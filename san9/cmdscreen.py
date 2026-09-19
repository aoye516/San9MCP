"""命令执行界面（巡察/徵兵/召來…）与「選擇武將」弹窗的操作。

本模块的每一条坐标/判据都来自实测（2026-09-17），不是目测：
见 `docs/05-操作知识库.md` 第一、七节。

实测出来的完整链路
------------------
    点地图上的都市          → 打开命令菜单（「設施」默认选中）
    鼠标移远（避免 hover 污染）
    right                   → 展开子菜单（默认选中第一项，如「巡察」）
    enter                   → 进入该命令的执行界面
    点「執行武將」标签       → 打开「選擇武將」弹窗  ← 入口是标签，不是表格区！
    点武将行（或左侧方格）   → 勾选；可多点
    点「決定」(绿)           → 回到命令执行界面，執行武將列表已填好、資金显示 50×人數
    点「執行」(绿)           → 下达命令，武将会回一句话

三条硬约束（踩过才知道）
------------------------
1. **鼠标必须先真正移动过，点击才会被接收。** `SetCursorPos` 不产生原始输入事件，
   DirectX 游戏可能仍停在"键盘模式"、把随后的点击全部丢掉（实测 6 次点击全 0.00 变化）。
   现在 `winio.click()` 内部已经改成 SendInput 真实移动 + 按住 80ms，别绕过去。
2. **测键盘前必须把鼠标移远**，否则橙/金高亮是鼠标悬停造成的，读数会全错。
3. **弹窗里的按钮没有 hover 效果**，所以"悬停扫描"找不到它们；要靠颜色/形状检测。
"""

from __future__ import annotations

import time

import numpy as np

import re

from . import annotate, lexicon, ocr, paths, winio

# ---------------------------------------------------------------- 实测几何

# 命令执行界面（巡察/召來…）——对话框位置与都市无关，实测两次一致
CMD_TABS_Y = 362          # 「執行武將 / 武將 / 智力」标签行中心
CMD_TAB_X = {"執行武將": 410, "武將": 515, "智力": 620}
# ⚠️ 「執行 / 中止」的 y **随命令变化**（实测：巡察 708~744、徵兵 740~770）——
# 因为不同命令的字段行数不同（巡察 3 行、徵兵 4 行）。所以行带要放宽，
# 靠"这一带里没有别的同色按钮"来保证唯一性，而不是靠固定 y。
CMD_BTN_BAND = (640, 800)

# 「選擇武將」弹窗
PICK_ROWS_Y0 = 413        # 第一行（張角）中心
PICK_ROW_PITCH = 30       # 行距：413 / 444 / 474 / 504
PICK_ROW_X = 290          # 点名字即可勾选（x≈256 的方格也能勾）
PICK_BTN_BAND = (720, 762)
PICK_DECIDE = (839, 743)   # 「決定」绿色按钮（find_button 失败时的回退）。
# ⚠️ 原写 (880, 742) —— **实测「決定」在 (839,743)**（`find_button('green')` 三次都是 839）。
# 880 是错的，回退一旦触发就点空。
PICK_CANCEL = (965, 741)  # 「中止」红色按钮


def _shot(hwnd: int, tag: str) -> str | None:
    """失败留证用的截图。**延迟导入 atoms** —— atoms 反过来 import 本模块，顶层导入会成环。

    ⛔ 2026-09-19 补：`run_facility_command` 的失败路径原来**连图都不拍**，
    等于没有证据（"失败必留证"这条纪律我自己破坏了）。
    """
    try:
        from . import atoms
        return atoms._shot(hwnd, tag)
    except Exception:
        return None


def _frame(hwnd: int) -> np.ndarray:
    """抓一帧（BGR）。所有定位都在同一帧内做，避免跨帧坐标系漂移。"""
    w, h, buf = winio.grab(hwnd)
    return np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)[:, :, :3].copy()


def _color_runs(img: np.ndarray, kind: str, y0: int, y1: int,
                x0: int = 0, x1: int = 1280, cover: float = 0.6,
                min_width: int = 30) -> list[tuple[int, int]]:
    """在一条横带里找"整块同色按钮"的 x 范围。

    返回 [(x_start, x_end), ...]，按宽度降序。
    按钮内部有白字，所以会断成几段 —— 这里合并相邻段（间隙 <= 24px）。
    """
    b = img[:, :, 0].astype(int)
    g = img[:, :, 1].astype(int)
    r = img[:, :, 2].astype(int)
    if kind == "green":
        m = (g > 80) & (g - r > 40) & (g - b > 20)
    else:
        m = (r > 90) & (r - g > 40) & (r - b > 40)
    band = m[y0:y1, :]
    col = band.sum(axis=0)
    thr = (y1 - y0) * cover
    segs: list[list[int]] = []
    cur = None
    for x in range(x1):
        if x >= x0 and col[x] >= thr:
            if cur is None:
                cur = [x, x]
            else:
                cur[1] = x
        else:
            if cur is not None:
                segs.append(cur)
                cur = None
    if cur is not None:
        segs.append(cur)
    # 合并被文字切断的段
    merged: list[list[int]] = []
    for s in segs:
        if merged and s[0] - merged[-1][1] <= 24:
            merged[-1][1] = s[1]
        else:
            merged.append(s)
    out = [(a, b2) for a, b2 in merged
           if b2 - a >= min_width and a >= x0 and b2 <= x1]
    out.sort(key=lambda z: z[0] - z[1])
    return out


def _color_mask(img: np.ndarray, kind: str) -> np.ndarray:
    b = img[:, :, 0].astype(int)
    g = img[:, :, 1].astype(int)
    r = img[:, :, 2].astype(int)
    if kind == "green":
        return (g > 80) & (g - r > 40) & (g - b > 20)
    if kind == "cyan":
        # 「一覽」按钮（青绿）。2026-09-19 从实拍帧 `S28_after_click_target.jpg` 量出：
        #   一覽 按钮 BGR 均值 (80,84,49) ⇒ B-R=31、G-R=35
        #   同屏的「中止」是 (35,57,98) ⇒ R 最大，不会被本条吸进来
        # ⚠️ 阈值取得比实测松（25/20），留余量；⛔ 别调到能把红按钮吸进来的程度。
        return (b > 80) & (b - r > 25) & (g - r > 20)
    return (r > 90) & (r - g > 40) & (r - b > 40)


def find_button_in(img, kind: str, y0: int, y1: int,
                   x0: int = 0, x1: int = 1280) -> tuple[int, int] | None:
    """**纯函数版** `find_button` —— 吃一张帧，不碰窗口。

为什么拆出来（2026-09-19）
--------------------------------
离线重放历史截图时也需要找按钮（`scripts/probe_move_flow.py` 要把「移動」
那条链逐帧量出来），但 `find_button(hwnd, ...)` 第一件事就是 `_frame(hwnd)`
⇒ **离线根本调不了**。

⛔ **不许另写一份检测逻辑**（项目教训：「能力被复刻，禁令就管不住副本」）——
   `find_button` 改而调用本函数，检测判据只有**唯一一处**实现。
"""
    import cv2

    m = _color_mask(img, kind)
    band = m[y0:y1, x0:x1]
    if not band.any():
        return None

    n, _lab, st, _ce = cv2.connectedComponentsWithStats(
        np.ascontiguousarray(band.astype(np.uint8)), 8)
    if n <= 1:
        return None

    cands = []
    for k in range(1, n):
        bx, by, bw, bh, area = (int(v) for v in st[k])
        fill = area / max(1, bw * bh)
        like_button = (30 <= bw <= 220 and 16 <= bh <= 70
                       and area >= 300 and fill >= 0.25)
        cands.append((like_button, area, bx, by, bw, bh))
    if not cands:
        return None
    # 只挑"像按钮"的，取面积最大。
    # ⛔ **不要退回"面积最大的"** —— 实测命令对话框的**红边框**是 3x576 的长条，
    # 面积比按钮还大，退回就会点到边框上（返回 (872,670)，真按钮在 (747,828)）。
    # 找不到就返回 None —— "找不到就说找不到"，绝不给一个看起来像坐标的假结果。
    like = [c for c in cands if c[0]]
    if not like:
        return None
    _ok, area, bx, by, bw, bh = max(like, key=lambda c: c[1])
    return (x0 + bx + bw // 2, y0 + by + bh // 2)


def find_button(hwnd: int, kind: str, y0: int, y1: int,
                x0: int = 0, x1: int = 1280) -> tuple[int, int] | None:
    """在横带 [y0,y1) 里找某个颜色的按钮，返回中心坐标。

    ⚠️ **不能用"整条带里某列有多少同色像素"当判据**（旧的 `_color_runs` 就是这么干的）。
    因为带高是给"按钮可能出现在哪"留的余量（比如 `CMD_BTN_BAND=(640,800)` 有 160px），
    而按钮本体只有 ~37px 高 —— `cover=0.6` 就要求某列有 96 个同色像素，
    **永远达不到**，于是永远返回 None。实测「執行」明明绿得很清楚却找不到，
    「決定」却能找到（因为它的带只有 42px 高）—— 就是这个原因。

    正确做法：**在整条带里挑"最像按钮"的连通块**（宽高比 + 面积），
    不要先做行剖面 —— 实测命令界面 y=753 有一条 267px 宽的**红色装饰横条**，
    它把行剖面的峰值顶高、把子带拉长，结果选中的是那条装饰（返回 (808,757)，
    而真正的「中止」在 (827,727)）→ 点下去什么也没发生。

    实测对按钮：宽 30~220、高 16~70、面积 ≥ 300、填充率 ≥ 0.25。

    ⭐ 2026-09-19：检测逻辑已抽到 `find_button_in(img, ...)`，本函数只剩取帧。
       ⛔ 别在这里重新写一份判据 —— 离线（`scripts/probe_move_flow.py`）和在线
       必须走**同一个**实现。
    """
    return find_button_in(_frame(hwnd), kind, y0, y1, x0, x1)


# ---------------------------------------------------------------- 步骤

HINT_REG = (300, 905, 1000, 960)
"""底栏提示栏的区域。

⛔ 老值是 `(0, 878, 1000, 960)` —— **那个区域什么都读不出来**。
实测（2026-09-18，7 区域 × 5 尺度全扫描）：老区域在 up3/4/5/6/8 **全部返回空**；
换成这条窄带之后，北海菜单下稳定读出 **「沒有可執行的武將。」**（5 个尺度全命中）。
原因大概是老区域太宽、起点太靠上，把地图噪声一起带进去，文字检测直接 miss。

⭐ 这条栏很值钱：**游戏会主动告诉我们它在等什么 / 为什么不给做。**
"""


NO_GO_FAILS = ("command_disabled", "submenu_unreadable", "hint_blocked",
               "no_officer", "already_done", "no_money",
               "no_merchant", "at_war", "chaos")
"""「命令下不了」这一类失败的 failed_at 取值。

前两个是**症状**（界面上读不出来的表现），后 6 个是 `_not_available()`
把底栏原话翻出来的**具名原因**。`run_facility_command` 见到任一取值都走"不硬做"分支。
"""

# 底栏原话 → **具名原因**。游戏自己把病因写出来了，我们就别自己编因果。
# 每条 = (底栏里出现的字样, 原因代号, 给 agent 的解释, 下一步怎么办)
# 底栏原话 → **具名原因**。游戏自己把病因写出来了，我们就别自己编因果。
# 每条 = (底栏里出现的字样, 原因代号, 给 agent 的解释, 下一步怎么办, **必需锚字**)
# ⛔ 最后的「锚字」是**防假阳性**的关键 —— 光靠模糊比对会把中性提示也吸进来：
#    实测「請選擇執行武將。」和「沒有可執行的武將」相似度 0.533，
#    不加锚字就会把一个**正常提示**判成"没有武将"，**把好命令拦死**。
#    锚字取该病因里最有区分度、且 OCR 不容易读错的字（用**归一化后**的字形）。
HINT_REASONS = (
    ("沒有可執行的武將", "no_officer",
     "本設施本旬**沒有可執行的武將**。每名武將在一個戰略面（一旬）裡只能執行一項命令，"
     "本旬的人已經派完了。",
     "換一座設施，或直接過旬（下一旬武將行動會刷新）。", "没"),
    ("沒有可執行的武将", "no_officer",
     "本設施本旬**沒有可執行的武將**（每人每旬只能執行一項命令）。",
     "換一座設施，或直接過旬。", "没"),
    ("這個指令已經執行完畢", "already_done",
     "這條命令本回合**已經執行過了**。設施類 9 條全部是「1 回合 1 次」——"
     "同一個設施、同一條命令，本旬不能做第二次。",
     "換一條命令、換一座設施，或過旬再來。", "完"),
    ("這個指令已經執行完", "already_done",
     "這條命令本回合**已經執行過了**（設施類 1 回合 1 次）。",
     "換一條命令、換一座設施，或過旬再來。", "完"),
    ("資金不足", "no_money",
     "**資金不足**。巡察 / 商業 / 開墾 / 修築 需要 50 × 人數。",
     "減少人數，或先做商業賺錢。", "不足"),
    ("沒有商人", "no_merchant",
     "該都市**沒有商人**，買進 / 賣出 做不了。",
     "換一座有商人的都市。", "商"),
    ("交戰", "at_war",
     "該都市**正在交戰**中，只有 軍事 / 任免 / 人材-召來 能執行。",
     "等戰事結束，或改用允許的命令類別。", "战"),
    ("混亂", "chaos",
     "該都市**處於混亂**，連軍事命令都不能執行。",
     "等混亂解除。", "乱"),
)

# 繁→简 + 常见错字的归一表（只收我们真正需要区分的字）
_NORM = str.maketrans({
    "這": "这", "個": "个", "執": "执", "經": "经", "畢": "毕", "將": "将",
    "沒": "没", "銀": "银", "錢": "钱", "亂": "乱", "戰": "战", "資": "资",
    "軍": "军", "勢": "势", "敵": "敌", "糧": "粮",
})
_CJK_ONLY = re.compile(r"[^\u4e00-\u9fff]")
FUZZY_MIN = 0.6


def _norm_for_match(s: str) -> str:
    """归一化后再比 —— 否则 OCR 错一个字就认不出来。

    实测（鄴·巡察已做过一次）底栏原话是「這個指令已經執行完畢。」，
    但 OCR 读成 **「这们指今已经执行完事·」** —— **错了 4 个字**。
    精确子串匹配必然失手。所以：去标点 → 繁转简 → 再模糊比对。
    """
    return _CJK_ONLY.sub("", (s or "")).translate(_NORM)


def explain_hint(text: str) -> dict | None:
    """把**游戏底栏的原话**翻译成具名原因。

    ⛔ **匹配必须容错**：这套小字 OCR 几乎必然读错一两个字。
    ⛔ **但也必须防误判**：光靠相似度会把中性提示吸进来
    （实测「請選擇執行武將。」≈「沒有可執行的武將」0.533）。
    所以两道闸：① 精确子串 → ② 模糊比 ≥ `FUZZY_MIN` **且含该条的锚字**。

    认得出来 → `{"code","matched","why","nxt","how"}`；认不出来 → `None`。
    **认不出来时调用方必须如实说"没认出来"，绝不硬套一个原因上去。**
    """
    t = _norm_for_match(text)
    if not t:
        return None
    for key, code, why, nxt, anchor in HINT_REASONS:          # ① 精确
        if _norm_for_match(key) in t:
            return {"code": code, "matched": key, "why": why, "nxt": nxt,
                    "how": "exact"}
    import difflib                                            # ② 模糊 + 锚字
    best = None
    for key, code, why, nxt, anchor in HINT_REASONS:
        if anchor and anchor not in t:
            continue                    # ⛔ 没有锚字 → 直接排除，不给相似度机会
        r = difflib.SequenceMatcher(None, _norm_for_match(key), t).ratio()
        if best is None or r > best[0]:
            best = (r, key, code, why, nxt, anchor)
    if best and best[0] >= FUZZY_MIN:
        r, key, code, why, nxt, anchor = best
        return {"code": code, "matched": key, "why": why, "nxt": nxt,
                "how": "fuzzy", "ratio": round(r, 3), "anchor": anchor}
    return None


def _not_available(hwnd: int, failed_at: str, **extra) -> dict:
    """命令不可用时的统一返回 —— **优先报病因，不报症状**。

    `failed_at` 传进来的是**症状**（如 `submenu_unreadable` = 子菜单读不出来），
    这里会读底栏、翻成**具名原因**（`already_done` / `no_officer` / …）覆盖它，
    并把原始症状留在 `symptom` 字段里备查。
    """
    h = read_hint(hwnd)
    out: dict = {"ok": False, "failed_at": failed_at,
                 "game_says": h or None, **extra}
    ex = explain_hint(h)
    if ex:
        out["failed_at"] = ex["code"]
        out["reason_code"] = ex["code"]
        out["reason"] = ex["why"]
        out["next"] = ex["nxt"]
        out["symptom"] = failed_at
        out["hint_matched"] = ex["matched"]
    else:
        out["reason"] = ("命令不可用，而且**底栏没给出可识别的原因**"
                         "（底栏读到的是 %r）→ 停手不硬做，留证据给人看。"
                         % (h or "",))
    return out


def read_hint(hwnd: int) -> str:
    """读底栏提示。读不到返回空串（**空串不要当判据**）。

    实测它有时会直接把病因写出来 —— 北海本旬没有可执行武将时，
    底栏写着「沒有可執行的武將。」，而命令全是灰的。这比任何推断都直接。

    ⭐ **多尺度取"最长"的那条，不是"第一条非空"。**
    实测同一帧不同尺度读出来的字数不一样，短的那条往往是**掉字**的版本；
    取最长能显著降低错字/掉字概率。
    """
    best = ""
    for up in (4, 5, 6, 3):
        try:
            items = ocr.read_screen(hwnd, HINT_REG, upscale=up)
            t = "".join((i["text"] or "").strip() for i in items).strip()
        except Exception:
            continue
        if len(t) > len(best):
            best = t
    return best


def submenu_items(hwnd: int, tries: int = 3, gap: float = 0.8) -> list[dict]:
    """读**当前已展开的**子菜单项。**读不到要重试** —— 别拿一次空结果当结论。

    返回 `[{i, name, enabled}]`（名字常读不出，只当参考；项序才是真的）。

    ⚠️ **读到 0 项 ≠「没有命令」，但绝不能当作「命令可用」**。
    实测（北海，2026-09-18）：该设施本旬**没有可行动的武将**，8 条设施命令全灰，
    子菜单**一项都读不出来**（灰字对比度低，行检测 miss）。
    原来的代码在这种情况下照样按 Enter → 什么都没打开 → 却因为"画面变了 20.08"
    被判成"打开成功" → 一路点到"选武将"才失败，报出来的原因是
    「没找到「總括選擇」按钮」—— **报的是症状，不是病因**（用户当场指出）。
    """
    from . import cmdmenu
    for k in range(max(1, tries)):
        try:
            sub = cmdmenu.read_menu(hwnd).get("sub") or []
        except Exception:
            sub = []
        if sub:
            return [{"i": i, "name": (it.get("name") or "").strip() or None,
                     "enabled": bool(it.get("enabled"))}
                    for i, it in enumerate(sub)]
        if k < tries - 1:
            time.sleep(gap)
    return []


def open_command(hwnd: int, city_xy: tuple[int, int] | None,
                 sub_index: int = 0, settle: float = 1.6,
                 menu_index: int = 0, check_grey: bool = True,
                 submenu_gate: bool = True) -> dict:
    """点都市 → 键盘走菜单 → 进入「主菜单第 menu_index 项 / 其子菜单第 sub_index 项」。

    ⭐ `city_xy=None` = **命令菜单已经开着**（调用方用 `atoms.open_command_menu` 开好的），
       跳过"点都市"那一下，只走键盘。加这个入口是为了让「人材 → 移動」能复用
       同一份键盘逻辑 —— ⛔ 不许在别的模块里再抄一遍走菜单的代码
       （项目教训：「能力被复刻，禁令就管不住副本」）。

    ⭐ `submenu_gate=False` = **不要用「子菜单项读了 0 项」来硬停**。
       2026-09-19 真机踩到：`san9_move` 第一次跑就撞上**假阴性** ——
       底栏明明已经是「移動我軍團的武將到對象設施」（= 光标确实停在「移動」上，说明字是游戏写的），
       而 `submenu_items` 重试 3 次都读 0 项（人材子菜单只有 4 项，颜色行检测更不可靠）
       ⇒ `open_command` 拒绝按 Enter ⇒ 整条链失败，**报出来的原因还是"键盘走菜单失败"**，
       会把人往"定位不对"的方向带。
       而 `submenu_items` 的 docstring 自己就写着「读到 0 项 ≠「没有命令」」。
       ⇒ 给**有硬后置门**的调用方（`movecmd` 用 `looks_like_move_screen` 验结果）
         一个显式开关跳过这道**启发式前置门**：按了 Enter 没进去，
         后置门一样会拦住，而且报的原因更准。
       ⛔ 默认 `True` —— **已验收的 `設施` 命令行为一字不改**。

    主菜单 6 项固定：設施(0) 軍事(1) 人材(2) 計略(3) 外交(4) 任免(5)。
    菜单打开后「設施」是默认选中项、子菜单默认选中第 0 项。

    ⚠️ 键序必须「先 down 选主项、再 right 展开子菜单」——
    实测**鼠标悬停主项只换高亮、不展开子菜单**（子 agent 踩过），必须按 right。

    ⛔ **按 Enter 之前先确认目标项不是灰的**（2026-09-18 血泪）。
    踩过：北海本旬没有可行动的武将，六条設施命令**全是灰的**。
    原来这里照样按 Enter → 什么都没打开 → 但画面确实变了 20.08 →
    被判成"打开成功"，一路走到"选武将"才失败，最后报出来的是
    **「没找到「總括選擇」按钮」—— 报的是症状，不是病因**（用户当场指出）。
    现在：发现是灰的 → **不按 Enter**，直接带着项序和原因返回。
    """
    if city_xy is not None:
        winio.click_client(hwnd, city_xy[0], city_xy[1], settle=0.35)
        time.sleep(settle)
    # 鼠标移到菜单外，否则 hover 高亮会污染键盘读数
    l, t, _, _ = winio.client_rect_screen(hwnd)
    winio.move_cursor_input(l + 700, t + 840)
    time.sleep(0.8)
    if menu_index:
        winio.press("down", menu_index, 0.25)
        time.sleep(0.9)
    winio.press("right", 1, 0.3)
    time.sleep(1.4)
    if sub_index:
        winio.press("down", sub_index, 0.25)
        time.sleep(1.2)

    warn: dict = {}
    if check_grey:
        # ⭐ **① 先读底栏 —— 它比"灰字"可靠得多。**
        # 此刻光标已经停在目标子项上，底栏会显示「这一项为什么不能做」。
        # 实测（鄴·巡察，本回合已做过一次）：
        #   - 子菜单灰字行检测**只读到 1/8 项，还把这个灰项判成 enabled=True** → 按 Enter 白跑
        #   - 而底栏**明明白白写着「這個指令已經執行完畢。」**
        # 所以顺序反过来：底栏命中 → 直接报病因，**连 Enter 都不按**。
        # ⚠️ `HINT_REASONS` 只收录**表示"不能做"**的话，所以"命中"就等于"被挡住"；
        #    正常说明（如「巡視都市以掌握民心。」）不命中，会照常往下走。离线单测已确认不误判。
        pre = read_hint(hwnd)
        warn["hint_before_enter"] = pre
        ex = explain_hint(pre)
        if ex:
            # 症状记 `hint_blocked`（= 靠底栏判出来的）；failed_at 会被换成具名病因
            return _not_available(hwnd, "hint_blocked", **warn)

        # ⭐ **② 兜底：底栏没给出可识别原因时，才靠子菜单的可用状态判断。**
        items = submenu_items(hwnd)
        if not items:
            if submenu_gate:
                # ⛔ **读到 0 项 → 停手，绝不当作"可用"往下按 Enter。**
                # 这是"读漏了却不说"这个毛病在**关键路径**上的后果：
                # 北海（本旬无武将、8 条全灰）会被读成"一切正常"。
                return _not_available(hwnd, "submenu_unreadable", sub_read="0/8", **warn)
            # ⭐ 调用方**已有硬后置门**（`movecmd` 用 `looks_like_move_screen` 验结果）
            # ⇒ 不用这道**启发式**前置门硬停（它 2026-09-19 在「人材」子菜单上假阴性，
            #   底栏明明写着「移動我軍團的武將到對象設施」却因为读 0 项拒绝按 Enter）。
            # 照常按 Enter；真没进去的话后置门会拦住，且报的原因更准。
            warn["submenu_gate_skipped"] = True
            warn["submenu_read"] = "0 项（本调用方不用它当门禁）"
        else:

            warn["sub_read"] = "%d/8" % len(items)
            if sub_index < len(items):
                it = items[sub_index]
                warn["item"] = it
                if not it["enabled"]:
                    # ⚠️ **子菜单"灰不灰"的读数不可靠，只能当线索、不能当结论。**
                    # 实测踩过（陳留·巡察，在任 4/4）：明明是**白字可用**的「巡察」，
                    # 却被读成 `enabled=False` → **把好命令拦死（假阴性）**，
                    # 报的还是 `command_disabled` 这个指向错误的方向。
                    # 现在：底栏（hint gate）才是权威——它已经在**上一步**拦掉真正被挡的命令；
                    # 走到这里说明底栏**没说不能做** ⇒ 不信灰读数，照常按 Enter。
                    # 真按了没反应，`_finish_without_picker` 会在「執行」不绿时拦住并留图。
                    warn["grey_doubt"] = it
            else:
                # 读到了几项但没覆盖目标项 → **无法确认**，如实标出来再往下走。
                # （不硬拦：子菜单行检测本来就常有缺口，全拦会把能用的命令也卡死。）
                warn["incomplete"] = True
                warn["note"] = ("子菜单只读到 %d 项，覆盖不到第 %d 项 → 这次没做置灰检查"
                                % (len(items), sub_index))

    # ⛔ **上面那一段只是"检查"，这里才是真正进命令 —— 千万别在检查里 return 掉。**
    # 踩过（2026-09-18）：我在"确认可用"那一支直接 `return {"ok": True}`，
    # **按 Enter 那行就再也走不到了** → 菜单停在原地 →
    # 后面 open_officer_picker 点在菜单外把菜单关掉 → 一路失败，
    # 报出来的是 `commanded=（無設施）白馬港`，**我差点给游戏安一个不存在的缺陷**。
    winio.press("enter", 1, 0.3)
    time.sleep(2.2)
    return {"ok": True, **warn}


TAB_SEARCH_REGIONS = [(340, 315, 700, 485), (330, 305, 720, 505)]
"""找「執行武將」标签的搜索区（y 放得很宽，因为**标签行的 y 随界面高度漂**）。"""


TAB_COLOR_BAND = (300, 520)      # 竖直搜索范围（**界面上部**：标签行必在命令名下面、
#                                  内容区上面。收窄是必要的 —— 界面下部还有别的蓝色元素，
#                                  放宽到 720 实测会把 y 拉到 698）
TAB_COLOR_X = (340, 700)         # 水平范围
TAB_COLOR_SAT = 55               # 彩色度阈值（max-min of BGR）
TAB_COLOR_MIN = 30               # 峰值低于它 = 这一帧没有标签行
TAB_COLOR_FRAC = 0.25            # 峰值行两侧按这个比例收边


def find_blue_labels_in(img) -> list[dict]:
    """找命令界面里的**蓝色标签**（`目標設施` / `執行武將` / `武將` 这类）。

    纯函数版 —— 吃一张帧。`find_officer_tab` 与 `find_target_label` 共用它，
    ⛔ 检测判据只有这一处实现（项目教训：「能力被复刻，禁令就管不住副本」）。

    返回按 y 从小到大排的列表：`[{"x": 中心x, "y": 中心y, "y0","y1","x0","x1"}, ...]`

    判据（2026-09-19 沿用 `find_officer_tab` 实测结论）：
      · **只找蓝色系**（`B >= R and B >= G`）—— 界面下方还有「執行」(绿)
        和「中止」(红) 按钮，饱和度比标签还高，只按 sat 找会定位到按钮行（实测 y 被拉到 703）
      · 逐行统计 sat>55 的像素数，**取峰值最高的那一段**（不是首尾区间 ——
        界面上别处也有零星彩色，首尾会被拉偏，实测偏 88px）
      · 用 `x` 范围反查该段的水平边界，所以两个标签的 x 各按各的量
    """
    B = img[:, :, 0].astype(int)
    G = img[:, :, 1].astype(int)
    R = img[:, :, 2].astype(int)
    sat = np.maximum(np.maximum(B, G), R) - np.minimum(np.minimum(B, G), R)
    blue = (sat > TAB_COLOR_SAT) & (B >= R) & (B >= G)
    y0, y1 = TAB_COLOR_BAND
    x0, x1 = TAB_COLOR_X
    sub = blue[y0:y1, x0:x1]
    prof = sub.sum(axis=1)
    peak = int(prof.max())
    if peak < TAB_COLOR_MIN:
        # ⭐ **不是「找不到」，而是「这一屏本来就没有蓝色标签」** —— 见 `_finish_without_picker`。
        return []
    ys = np.flatnonzero(prof >= peak * TAB_COLOR_FRAC)
    if ys.size == 0:
        return []
    segs: list[list[int]] = [[int(ys[0]), int(ys[0])]]
    for a, b in zip(ys[:-1], ys[1:]):
        if int(b) - int(a) > 3:
            segs.append([int(b), int(b)])
        else:
            segs[-1][1] = int(b)

    out = []
    for lo, hi in segs:
        rows = sub[lo:hi + 1].any(axis=0)
        cols = np.flatnonzero(rows)
        if cols.size == 0:
            continue
        out.append({"x0": int(x0 + cols[0]), "x1": int(x0 + cols[-1]),
                    "y0": int(y0 + lo), "y1": int(y0 + hi),
                    "x": int(x0 + (cols[0] + cols[-1]) // 2),
                    "y": int(y0 + (lo + hi) // 2)})
    return out


TARGET_LABEL_Y_MIN = 340
"""找「目標XX」标签时的**下界** —— 用来把**对话框标题横幅**排除掉。

⛔ 2026-09-19 真机（平原·探索）：`find_blue_labels_in` 在那屏上读到 3 个候选：

    (459,308) 宽116 · (455,321) 宽102 · (440,430) 宽111

前两个**不是标签**，是对话框左上那个**紫色标题横幅「探索」** ——
紫底同样满足「B≥R 且饱和度高」的蓝色系判据。
真正的 `目標地域` 标签是 **(440,430)**。
按"取最上面那个"去点 ⇒ 点到标题横幅上，**屏幕前后两帧完全相同**（真机验证）。

⚠️ 而这个 bug 在**移动**上没暴露（那次标题横幅没落进 `TAB_COLOR_BAND`）
   ⇒ **别因为"移动跑通了"就以为这条检测是干净的。**

实测：标题横幅 y≈300..325；`目標設施` 390 · `目標地域` 430 ⇒ 340 落在两者中间。
⛔ 不许改成"按宽度区分" —— 116 vs 111 分不开。
"""


def find_target_label(hwnd: int) -> tuple[tuple[int, int] | None, str]:
    """现场找「目標XX」标签（`目標設施` / `目標地域`）的点击中心。

    ⚠️ 判据与 `find_officer_tab` **同源、方向相反**：
    这类命令界面（移動 / 探索）上有两个彩色标签 ——「目標…」在上、「執行武將」在下。
    `find_officer_tab` 取**最靠下**那段拿「執行武將」；
    本函数取**最靠上**那段拿「目標…」，但**必须排掉标题横幅**（见 `TARGET_LABEL_Y_MIN`）。

    ⛔ 点错成「執行武將」的后果不是"没反应"，是**打开选将弹窗**（界面多开一层），
       所以这一步必须认准 —— 用 y 排序取 `[0]`，不是取峰值。
    """
    try:
        img = _frame(hwnd)
    except Exception:
        return None, "no_frame"
    labels = find_blue_labels_in(img)
    if not labels:
        return None, "no_colored_tab"
    labels = [d for d in labels if d["y0"] >= TARGET_LABEL_Y_MIN]
    if not labels:
        return None, "only_title_banner"     # 只读到标题横幅 ⇒ 如实说，别乱点
    labels.sort(key=lambda d: d["y0"])
    lo = labels[0]
    return (lo["x"], lo["y"]), "color_top"


def find_officer_tab(hwnd: int) -> tuple[tuple[int, int] | None, str]:
    """现场找「執行武將」标签的点击中心。返回 ((x, y), 依据)。

    ⚠️ **不能写死 y**：命令界面高度随命令内容变，标签行跟着漂 ——
    实测「商業」界面标签在 y≈362（= CMD_TABS_Y），「訓練」界面在 y≈415，**差 53px**。
    写死 362 在矮界面上会点到界面上方空白，弹窗根本不出来。

    ⚠️ **也别靠 OCR**：这四个字实测被读成「批行式將」（四个字全错）。

    可靠判据是**颜色**：「執行武將」是界面里唯一的高饱和彩色标签
    （实测底色 BGR=(191,76,114) → sat=115），界面背景 sat≈2。
    逐行统计 sat>55 的像素数，**峰值行就是标签行**
    （实测标签行 240/92/78/92/100 vs 其它行 0~20，约 12 倍余量）。
    """
    try:
        img = _frame(hwnd)
    except Exception:
        return None, "no_frame"
    labels = find_blue_labels_in(img)
    if not labels:
        # ⭐ **这不是「找不到」，而是「这一屏本来就没有蓝色标签」** —— 见 `_finish_without_picker`。
        # 本旬只剩 1 名可行动武将时，标签会**置灰**（不是蓝色），台上已自动选好。
        # ⛔ 以前这里返回硬编码 (410, CMD_TABS_Y=362) 去点 —— 实测点空、
        #    一路报到「没找到『總括選擇』按钮」，**白点一下还指错方向**。
        return None, "no_colored_tab"
    # ⚠️ 取"**最靠下**"的那一段，而不是"峰值最高"的 ——
    # 「移動」界面上有**两个**蓝色标签（「目標設施」在上、「執行武將」在下），
    # 峰值最高的往往是「目標設施」→ 点它会重开"选目标"模式而不是选武将（实测踩过）。
    # 而「執行武將」总是**最下面**那个蓝标签，两种界面对这个判据都成立。
    labels.sort(key=lambda d: d["y1"])
    lo = labels[-1]
    return (CMD_TAB_X["執行武將"], lo["y"]), "color"


def open_officer_picker(hwnd: int) -> dict:
    """点「執行武將」标签，打开「選擇武將」弹窗。

    ⚠️ 入口是**标签**，不是下面那块空表格。
    点表格区实测完全没有反应（21 个点位反复试过），而点标签立刻打开弹窗。
    """
    xy, how = find_officer_tab(hwnd)
    if xy is None:
        # ⛔ **定位不到就不点。** 调用方会用「執行」按钮的颜色判断这是不是
        # "只剩 1 名武将、已自动选好"那种情形（见 `_finish_without_picker`）。
        return {"xy": None, "how": how, "ok": False}
    winio.click_client(hwnd, xy[0], xy[1], settle=0.35)
    time.sleep(2.0)
    return {"xy": list(xy), "how": how, "ok": True}


def officer_rows(hwnd: int, count: int = 4) -> list[int]:
    """列出「選擇武將」里武将行的 y 中心。

    做法：先程序化检出「武將」列的文字行，跳过表头那一行；
    检出失败才退回实测行距（413 / 444 / 474 / 504，行距 30）。
    """
    img = _frame(hwnd)
    rows = annotate.text_rows(img, (265, 380, 340, 560), thr=150, min_run=3)
    ys = [(r[0] + r[1]) // 2 for r in rows]
    if len(ys) > count:            # 第一段是表头
        return ys[1:count + 1]
    if len(ys) == count:
        return ys
    return [PICK_ROWS_Y0 + i * PICK_ROW_PITCH for i in range(count)]


def select_officers(hwnd: int, rows: list[int], x: int = PICK_ROW_X) -> list[int]:
    """逐个勾选武将行，返回实际点击过的 y。"""
    done = []
    for y in rows:
        winio.click_client(hwnd, x, y, settle=0.30)
        time.sleep(1.3)
        done.append(y)
    return done


PICKALL_REGIONS = [(250, 650, 600, 790), (250, 630, 640, 810)]
"""找「總括選擇」按钮的搜索区（左下角，OCR 实测读成「總括遥择」）。"""


def pick_all_officers(hwnd: int) -> dict:
    """在「選擇武將」弹窗里点「總括選擇」**全选**，返回点击坐标与依据。

    ⭐ 为什么用全选按钮而**不是**逐行点：
    不同命令的选武将弹窗**大小不同** —— 「設施」命令的弹窗小（6 行、行距 30、
    第一行 y≈395），「移動」命令的弹窗大（6 行、行距 **50**、第一行 y≈395）。
    逐行点必须跟着弹窗尺寸改行 y 检测，很脆；而「總括選擇」是全选按钮，
    位置基本固定（实测 y≈702~735，两种弹窗都在这个带里），稳得多。

    ⚠️ 弹窗里有**两个**带"括"字的按钮：「總括選擇」(左) 和「總括解除」(右)。
    只按"括"匹配会误点「總括解除」= 全清（实测踩过）→ 必须取 **x 最小的**那个。
    """
    boxes = []
    for reg in PICKALL_REGIONS:
        for up in (3, 4, 5):
            try:
                items = ocr.read_screen(hwnd, reg, upscale=up)
            except Exception:
                continue
            for it in items:
                t = (it["text"] or "").replace(" ", "")
                if "括" in t:
                    boxes.append((it["box"][0], it["box"], t))
    if not boxes:
        return {"ok": False, "why": "没找到「總括選擇」按钮（OCR 没读到含『括』的按钮）"}
    boxes.sort(key=lambda z: z[0])
    b = boxes[0][1]
    xy = ((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)
    winio.click_client(hwnd, xy[0], xy[1], settle=0.5)
    time.sleep(1.5)
    return {"ok": True, "xy": list(xy), "raw": boxes[0][2],
            "candidates": [(z[1], z[2]) for z in boxes[:3]]}


def confirm_picker(hwnd: int) -> tuple[int, int] | None:
    """点「決定」（绿色）。返回点击位置，找不到返回 None。"""
    pos = find_button(hwnd, "green", *PICK_BTN_BAND, x0=700, x1=1010)
    if pos is None:
        pos = PICK_DECIDE
    winio.click_client(hwnd, pos[0], pos[1], settle=0.35)
    time.sleep(2.2)
    return pos


def click_execute(hwnd: int) -> tuple[int, int] | None:
    """点「執行」（绿色，只有选了武将才变绿）。"""
    pos = find_button(hwnd, "green", *CMD_BTN_BAND, x0=560, x1=800)
    if pos is None:
        return None
    winio.click_client(hwnd, pos[0], pos[1], settle=0.35)
    time.sleep(2.2)
    return pos


def click_abort(hwnd: int) -> tuple[int, int] | None:
    """点「中止」（红色）。"""
    pos = find_button(hwnd, "red", *CMD_BTN_BAND, x0=700, x1=1010)
    if pos is None:
        return None
    winio.click_client(hwnd, pos[0], pos[1], settle=0.35)
    time.sleep(1.8)
    return pos


# ---------------------------------------------------------------- 右侧「移動列表」

LIST_TEXT_X = (1116, 1218)   # 城市名的文字列范围（实测：两侧是空档，右边 1244 是滚动条）
LIST_X_CLICK = 1160          # 点击用 x（名字中间）


def city_rows(hwnd: int) -> list[dict]:
    """检出右侧「移動列表」的条目行。

    返回 [{'y': 中心, 'height': 行高, 'peak': 亮像素峰值, 'highlight': 是否是当前城市}]。
    **当前城市那一行是一条实心亮带**（实测宽 60+/102，高约 30），所以能直接认出来。
    """
    img = _frame(hwnd)
    gray = img[:, :, 0] * 0.114 + img[:, :, 1] * 0.587 + img[:, :, 2] * 0.299
    sub = gray[:, LIST_TEXT_X[0]:LIST_TEXT_X[1]]
    width = LIST_TEXT_X[1] - LIST_TEXT_X[0]
    rows = (sub > 95).sum(axis=1)
    segs: list[dict] = []
    cur = None
    # y 范围只取列表本体：上面的 部隊/武將/設施 标签、下面的大/小/進行 都会被误认成行
    # 用户 2026-09-18 指出：**列表一屏放不下，下面还有好几行**。
    # 这里原来写死 y<700，把下面的行自己切掉了 —— 所以只看到 13 行。
    for y in range(372, 800):
        v = int(rows[y])
        if v >= 4:
            if cur is None:
                cur = {"y0": y, "y1": y, "peak": v}
            else:
                cur["y1"] = y
                cur["peak"] = max(cur["peak"], v)
        else:
            if cur is not None:
                if cur["y1"] - cur["y0"] >= 10:
                    segs.append(cur)
                cur = None
    if cur is not None and cur["y1"] - cur["y0"] >= 10:
        segs.append(cur)
    out = []
    for s in segs:
        h = s["y1"] - s["y0"] + 1
        y = (s["y0"] + s["y1"]) // 2
        out.append({"y": y, "height": h, "peak": s["peak"],
                    "highlight": s["peak"] >= width * 0.5 and h >= 20,
                    "name": None})
    out = _attach_names(hwnd, out)
    cur = current_city_name(hwnd)
    for r in out:
        r["is_current"] = bool(cur) and r["name"] == cur
    return out


def current_city_name(hwnd: int) -> str | None:
    """当前选中的城市 —— 右下角的「地域：XXX（YYY）」标签，OCR 一下就有。

    这比"列表里哪行高亮"可靠：高亮带会被标签/按钮的亮度干扰。
    """
    items = ocr.read_screen(hwnd, (1040, 925, 1280, 962))
    raw = "".join(i["text"] for i in items)
    m = re.search(r"[（(]([\u4e00-\u9fff]{2,4})[)）]", raw)
    if m:
        name, conf, _ = lexicon.correct(m.group(1))
        return name
    m = re.search(r"地域[::]\s*([\u4e00-\u9fff]{2,4})", raw)
    if m:
        name, conf, _ = lexicon.correct(m.group(1))
        return name
    return None


def _attach_names(hwnd: int, rows: list[dict]) -> list[dict]:
    """给检出的行配上 OCR 读出的名字。

    行的 y 范围来自亮度剖面（可靠），名字来自多尺度 OCR（会漏行）。
    两者按 y 就近合并；**没读到的行不硬编名字**，但如果它夹在两个已知行之间
    且行距一致，就按行距推断并标 `inferred=True`（诚实标注，不冒充实测）。
    """
    if not rows:
        return rows
    y_lo = min(r["y"] for r in rows) - 30
    y_hi = max(r["y"] for r in rows) + 30
    try:
        named = ocr.read_list_multiscale(hwnd, (LIST_TEXT_X[0] - 6, y_lo,
                                                LIST_TEXT_X[1] + 6, y_hi))
    except Exception as e:                       # OCR 挂了不该拖垮整个功能
        for r in rows:
            r["name_error"] = str(e)
        return rows
    for r in rows:
        cand = [n for n in named if abs(n["y"] - r["y"]) <= 14]
        if cand:
            best = max(cand, key=lambda n: (n["votes"], n["conf"]))
            r["name"] = best["name"]
            r["conf"] = best["conf"]
    # 夹在两个已知行之间、行距一致 → 推断
    known = [r for r in rows if r["name"]]
    for i, r in enumerate(rows):
        if r["name"]:
            continue
        left = next((k for k in reversed(known) if k["y"] < r["y"]), None)
        right = next((k for k in known if k["y"] > r["y"]), None)
        if left and right:
            pitch = (right["y"] - left["y"]) / (rows.index(right) - rows.index(left))
            r["name"] = f"{left['name']}+{round((r['y']-left['y'])/pitch)} 行(推断)"
            r["inferred"] = True
    return rows


def find_city_row(hwnd: int, name: str) -> int | None:
    """按名字找右侧列表里的行号。找不到返回 None。"""
    for i, r in enumerate(city_rows(hwnd)):
        if r.get("name") == name:
            return i
    return None


def select_city_row(hwnd: int, index: int) -> dict:
    """点右侧列表第 index 行（0 起）。点自己的城市名会把该城固定到画面中间。"""
    rows = city_rows(hwnd)
    if index >= len(rows):
        return {"ok": False, "error": f"列表只有 {len(rows)} 行，取不到第 {index} 行",
                "rows": rows}
    y = rows[index]["y"]
    before = _frame(hwnd)
    winio.click_client(hwnd, LIST_X_CLICK, y, settle=0.35)
    time.sleep(1.8)
    after = _frame(hwnd)
    changed = float(np.abs(after.astype(int) - before.astype(int)).mean())
    return {"ok": True, "row": index, "y": y, "changed": round(changed, 2),
            "is_current": rows[index]["highlight"]}


def find_city_on_map(hwnd: int) -> tuple[int, int] | None:
    """在地图上找都市：灰色石墙是一大块低饱和中高亮的连通区，取最大块的质心。"""
    img = _frame(hwnd)
    b, g, r = img[:, :, 0].astype(int), img[:, :, 1].astype(int), img[:, :, 2].astype(int)
    sat = np.maximum(np.maximum(b, g), r) - np.minimum(np.minimum(b, g), r)
    gray = b * 0.114 + g * 0.587 + r * 0.299
    mask = ((sat < 30) & (gray > 95) & (gray < 205)).astype("uint8")
    # ⚠️ 地图会滚动，都市可能在画面任何位置（实测从 y 446 跑到 629），
    # 所以窗口要覆盖整块地图，只排除四周的常驻面板。
    mask[:100, :] = 0        # 顶栏
    mask[880:, :] = 0        # 底部提示栏
    mask[:, :260] = 0        # 左侧情报面板
    mask[:, 1010:] = 0       # 右侧移动列表
    bl = annotate.blobs(img, mask, min_area=1500)
    if not bl:
        return None
    # 先按"离画面中央近"排，再按面积 —— 因为切城之后目标城就在中央，
    # 而画面边缘可能还挂着别的城（实测点错过一次「北平」，那不是我们的城，点它不出菜单）
    hh, ww = img.shape[:2]
    cx, cy = ww // 2, hh // 2
    bl.sort(key=lambda z: ((z["xy"][0] - cx) ** 2 + (z["xy"][1] - cy) ** 2))
    return bl[0]["xy"]


# ---------------------------------------------------------------- 命令名 → 子菜单序号

# 设施类命令在「設施」子菜单里的位置（顺序同 citymenu.SUBMENU_ITEMS["設施"]）
FACILITY_ORDER = ["巡察", "商業", "開墾", "修築", "徵兵", "訓練", "買進", "賣出", "撤除"]

OP_TO_CMD = {
    "city.patrol": "巡察",
    "city.commerce": "商業",
    "city.farm": "開墾",
    "city.fortify": "修築",
    "city.draft": "徵兵",
    "city.train": "訓練",
    "city.buy_food": "買進",
    "city.sell_food": "賣出",
    "city.demolish": "撤除",
}


def sub_index_of(cmd: str, menu_index: int = 0) -> int:
    """该命令在**它所属菜单**的子菜单里是第几项（0 起）。

    菜单默认选中第 0 项，所以按几次 down 就返回几。
    主菜单 6 项固定：設施(0) 軍事(1) 人材(2) 計略(3) 外交(4) 任免(5)。
    """
    try:
        from .citymenu import SUBMENU_ITEMS
        from .lexicon import MENUS
        menu = MENUS[menu_index] if 0 <= menu_index < len(MENUS) else None
        items = SUBMENU_ITEMS.get(menu or "")
        if items and cmd in items:
            return items.index(cmd)
    except Exception:
        pass
    try:
        return FACILITY_ORDER.index(cmd)
    except ValueError:
        raise KeyError(f"「{cmd}」找不到（menu_index={menu_index}）："
                       f"設施表={FACILITY_ORDER}") from None


# ---------------------------------------------------------------- 一键跑完整条链

CITY_CENTER = (640, 480)
"""选中设施之后，**它的城管图形落在屏幕正中** —— 点这里就是点城市中心。

（用户 2026-09-18 提示："选中城市之后往中间一点应该是最稳定的"。
实测同一个 (640,480)：选平原 → 标题「平原」；选小沛 → 标题「小沛」。）

⛔ 不要用 `find_city_on_map(hwnd)` 来定位城市 —— 实测它返回
**1 个 750x613、面积 18 万像素的巨块**（城墙+道路+港口糊成一片）的质心 (578,518)，
那是"糊出来的平均位置"，会点到旁边的设施上。这就是"命令时对时错"的根因。
"""

LEFT_TITLE = (40, 145, 345, 220)

CMD_TITLE_REGIONS = [
    (340, 240, 660, 420),      # 主区：命令界面标题栏「命令設施 X」
    (320, 225, 700, 440),      # 稍宽兜底
]
"""命令界面标题栏「命令設施　許昌」的位置候选区（按顺序试）。

⚠️ **y 范围必须够宽** —— 命令界面的高度**随命令内容变**：
「商業」界面高（标题行 real y≈290），「訓練」界面矮（标题行 real y≈348）。
原来写死 (350,265,590,335)，只够到 335 → 訓練 的标题落在区域外，
verify_city 报 commanded=null，**看起来像"点错了"，其实点得完全正确**（实测踩过）。
"""
CMD_TITLE = CMD_TITLE_REGIONS[0]          # 兼容旧引用

LEFT_TITLE_SKIP = ("一般", "重要", "要衝")


def commanded_facility(hwnd: int) -> str | None:
    """**我现在在指挥哪个设施/在读哪个命令**。

    两条来源，按可靠性排序：
    ① **命令界面的标题栏**「命令設施　平原」—— 最直接（命令名 + 设施名都在这一行）
    ② 左上「设施情报」面板的标题（只在菜单/情报面板露出来时才有）
    """
    import re as _re
    for reg in CMD_TITLE_REGIONS:
        for up in (3, 4, 5):
            try:
                items = ocr.read_screen(hwnd, reg, upscale=up)
            except Exception:
                continue
            # ⚠️ **必须按 y 分组再拼**，不能把整区所有文字拼成一行 ——
            # 界面上「命令設施 X」和「執行武將」是**上下两行**；
            # 整区拼接会造出 "命令設施 批行式將" 这种东西，
            # 把「執行武將」的错读当地名返回（实测踩过）。
            lines: dict = {}
            for it in items:
                lines.setdefault(round(it["y"] / 16), []).append(it)
            for k in sorted(lines):
                group = sorted(lines[k], key=lambda i: i["x"])
                txt = "".join((i["text"] or "").strip() for i in group)
                # ⚠️ 必须是 {1,4} 不是 {2,4}：**「鄴」是单字城名**，要求 2 字以上
                # 会直接匹配失败 → commanded=null → 看起来像"点错了"（实测踩过）。
                # ⚠️ 「命令設施」这四个字 OCR 经常**掉字**（实测读成「令设施」，
                # 掉了"命"）→ 正则必须容忍缺字，否则点对了也判成"没点对"。
                m = _re.search(r"命?令?[設设][施拖]\s*([\u4e00-\u9fff]{1,4})", txt)
                if m:
                    name = m.group(1)
                    try:
                        from . import lexicon
                        fixed, conf, _how = lexicon.correct(name)
                        if conf >= 0.7:
                            name = fixed
                    except Exception:
                        pass
                    return name
    # ⛔ **门禁：战略面通畅的时候，根本不存在「我在指挥谁」这件事。**
    # 不加这道门，下面两层兜底会在干净战略面上从噪声里读出垃圾 ——
    # 实测 `san9_look` 在干净面上报出「正在指挥『（無設施）白馬港』」（D4）。
    # 判据用 `go` 锚点：有东西盖住战略面时它才会低（0.09~0.35）。
    if _go_clear(hwnd):
        return None
    name = left_panel_title(hwnd)
    if name:
        return name
    # ⚠️ **有些命令界面根本没有「命令設施 X」这一行** —— 那是不绑定设施的命令
    # （实测「委任」= 军团编组界面，顶部只有一个大标题「委任」）。
    # 这不是"点错了"，所以这里兜底读对话框大标题（命令名），并标记出来，
    # 免得 verify_city 把"点对了"判成"没点对"（实测踩过，白跑一次）。
    try:
        t, conf = ocr.read_dialog_title(hwnd)
        if t and conf >= 0.7:
            return f"（無設施）{t}"
    except Exception:
        pass
    return None


def left_panel_title(hwnd: int) -> str | None:
    """读左上设施情报面板的标题。

    这是"开到的是不是我要的城"的唯一可靠判据（实测 3 区域 × 3 尺度 6/6 命中）。
    ⛔ 不要用右下「地域：X（Y）」—— 那个跟的是**鼠标悬停处**，不是选中项
    （实测选的是濮陽，它显示「頓丘（平原）」），曾因此导致连锁乱点。
    """
    try:
        items = ocr.read_screen(hwnd, LEFT_TITLE, upscale=5)
    except Exception:
        return None
    cands = []
    for it in items:
        t = (it["text"] or "").strip()
        if len(t) < 2 or t in LEFT_TITLE_SKIP:
            continue
        cands.append(t)
    return max(cands, key=len) if cands else None


PICK_NAME_REG = (262, 392, 342, 575)
"""「選擇武將」弹窗里**武将名那一列**的区域（表头 y≈385，所以从这里往下取）。

实测布局（鄴·巡察，2026-09-18）：数据行 y = **413 / 443 / 473 / 503**（行距 30），
名字列 x ≈ 265~340。
"""


def picker_officers(hwnd: int, max_rows: int = 8) -> list[dict]:
    """读「選擇武將」弹窗里的**候选武将名单** → `[{row, y, name}]`。

    ## 两条必须守住的

    1. **行号是准的，名字不一定。** 行位置来自多尺度 OCR 的 y 聚类（稳）；
       而这个名字列**只读出 2/4**（实测鄴读出「张燕」「管亥」，另两行读不出）。
       → 所以**选谁一律按行号**，名字只用来"给人看"。
    2. **名字读不出就给 `None`，绝不编。** 曾经栽过：`_attach_names` 会编
       「南皮+1 行(推断)」冒充真名，agent 会以为真有这个设施。
    """
    img = _frame(hwnd)
    bands = annotate.text_rows(img, PICK_NAME_REG, thr=150, min_run=3)
    ys = [(int(b[0]) + int(b[1])) // 2 for b in bands]
    ys = [y for y in ys if y >= 396]          # 去掉表头那一行

    # 多尺度 OCR 收敛名字（同一行取"字数最多"的那个读法）
    votes: list[tuple[int, str]] = []
    for up in (3, 4, 5, 6):
        try:
            items = ocr.read_screen(hwnd, PICK_NAME_REG, upscale=up)
        except Exception:
            continue
        for it in items:
            t = "".join(ch for ch in (it["text"] or "")
                        if "\u4e00" <= ch <= "\u9fff")
            if t and it["y"] >= 396:
                votes.append((int(it["y"]), t))

    out: list[dict] = []
    for i, y in enumerate(ys[:max_rows]):
        best = None
        for vy, vt in votes:
            if abs(vy - y) <= 12:
                if best is None or len(vt) > len(best):
                    best = vt
        out.append({"row": i, "y": y, "name": best})

    # 兜底：亮度剖面一行都没检出时，退回实测行距（413 起步、行距 30）。
    # ⚠️ 这时**不知道有几个人**，所以标出来 —— 不能让 agent 以为这就是全部。
    if not out:
        for i in range(max_rows):
            out.append({"row": i, "y": PICK_ROWS_Y0 + i * PICK_ROW_PITCH,
                        "name": None, "fallback": True})
    return out


def run_facility_command(hwnd: int, city_row: int, cmd: str,
                         officer_rows: list[int] | None = None,
                         select_all: bool = False,
                         menu_index: int = 0,
                         read_officers: bool = False) -> dict:
    """把「选城市 → 开菜单 → 进命令 → 选武将 → 執行」整条链跑完，逐步留证。

    officer_rows=None 且 select_all=False 时，只打开命令界面不选人（用于侦察）。
    `menu_index` 选主菜单第几项（0=設施 1=軍事 2=人材 3=計略 4=外交 5=任免）。
    返回每一步的实测数据，便于判断哪一步失败。
    """
    steps: list[dict] = []
    r0 = select_city_row(hwnd, city_row)
    steps.append({"step": "select_city", **r0})
    if not r0.get("ok"):
        return {"ok": False, "steps": steps, "failed_at": "select_city"}

    # ⛔ 原来是 `find_city_on_map(hwnd)` —— 那个函数返回巨型糊块的质心
    #    （实测 (597,528)），落在城与港之间的空地上，所以命令时对时错。
    #    已验证配方：select_city_row 之后设施落在屏幕正中，点正中即可。
    pos = CITY_CENTER
    before = _frame(hwnd)
    oc = open_command(hwnd, pos, sub_index=sub_index_of(cmd, menu_index),
                      menu_index=menu_index, check_grey=True)
    after = _frame(hwnd)
    ch = float(np.abs(after.astype(int) - before.astype(int)).mean())
    steps.append({"step": "open_command", "cmd": cmd, "city_xy": list(pos),
                  "changed": round(ch, 2), "ok": ch > 3.0 and oc.get("ok", True),
                  "grey_check": oc.get("item")})

    # ⛔ **这条命令在界面里是灰的**（或子菜单压根读不出来）—— 这是**病因**，不是症状。
    # 不要往下走"选武将"，否则失败会报成"没找到「總括選擇」按钮"，完全指错方向
    # （2026-09-18 用户当场指出：北海没有可行动的武将，所以打不开命令面板）。
    if oc.get("failed_at") in NO_GO_FAILS:
        who = commanded_facility(hwnd)              # 菜单还开着，标题读得到
        steps.append({"step": "verify_city", "commanded": who, "ok": bool(who)})
        # ⭐ **优先引用游戏自己的话 + 用机器可读的具名代号。**
        # 底栏会把病因直接写出来：
        #   北海本旬无武将        → 「沒有可執行的武將。」
        #   鄴的巡察已做过一次    → 「這個指令已經執行完畢。」（設施類 1 回合 1 次）
        # 这比我们任何推断都直接。**别自己编因果，先读游戏怎么说。**
        game_says = oc.get("game_says") or read_hint(hwnd)
        steps.append({"step": "read_hint", "text": game_says})
        # ⛔ **失败必留证** —— 这个分支原来也没拍图（只拍过 verify_city 那条路）
        nogo_shot = _shot(hwnd, "nogo_%s_%s" % (cmd, oc.get("failed_at")))

        if oc.get("reason"):
            target = who or ("第 %d 行" % city_row)
            reason = "「%s」在「%s」下不了命令 —— %s" % (cmd, target, oc["reason"])
            if game_says:
                reason += "（游戏底栏原话：「%s」）" % game_says
        elif game_says:
            reason = ("「%s」在「%s」下不了命令 —— 游戏底栏写着：「%s」。"
                      "但这条提示**我们还没收录**，所以不硬猜原因 —— 请据此判断。"
                      % (cmd, who or ("第 %d 行" % city_row), game_says))
        else:
            reason = ("「%s」在「%s」下不了命令，而且底栏也读不到原因 —— "
                      "**停手不硬做**（宁可不下，也不要乱点）。"
                      % (cmd, who or ("第 %d 行" % city_row)))
        return {"ok": False, "steps": steps,
                "failed_at": oc["failed_at"],
                "reason_code": oc.get("reason_code"),
                "commanded": who,
                "game_says": game_says or None,
                "disabled_item": oc.get("item"),
                "sub_read": oc.get("sub_read"),
                "symptom": oc.get("symptom"),
                "reason": reason,
                "next": oc.get("next"),
                "shot": nogo_shot}

    if ch <= 3.0:
        return {"ok": False, "steps": steps, "failed_at": "open_command",
                "reason": "点了城管图形但画面几乎没变 —— 菜单很可能没打开"}
    # ★ 用**左上标题**核实打开的确实是这座城市 —— 不再只靠"画面变了"
    who = commanded_facility(hwnd)
    steps.append({"step": "verify_city", "commanded": who, "ok": bool(who)})
    if not who:
        # ⛔ **失败必留证**（原来这里连图都不拍，等于没有证据 —— 2026-09-19 补）
        # ⭐ 同时把**底栏**读回来：游戏常常已经把"为什么不行"写在那儿了。
        #    （实测 鄴·巡察 本回合已做过一次时，底栏写的是「這個指令已經執行完畢。」）
        h = read_hint(hwnd)
        steps.append({"step": "read_hint", "phase": "verify_city_failed", "text": h})
        shot = _shot(hwnd, f"verifycity_fail_cityrow{city_row}")
        ex = explain_hint(h)
        if ex:
            # 底栏给出了可识别的病因 → 报**病因**，不报"标题读不出"这个症状
            return {"ok": False, "steps": steps,
                    "failed_at": ex["code"], "reason_code": ex["code"],
                    "commanded": None, "game_says": h or None,
                    "symptom": "verify_city", "shot": shot,
                    "reason": "「%s」下不了命令 —— %s（游戏底栏原话：「%s」）"
                              % (cmd, ex["why"], h),
                    "next": ex["nxt"]}
        return {"ok": False, "steps": steps, "failed_at": "verify_city",
                "commanded": None, "game_says": h or None, "shot": shot,
                "reason": "菜单开了但左上设施情报面板没出标题 —— 不知道在指挥谁，"
                          "不往下做（宁可不做也不乱点）。底栏读到的是 %r。" % (h or "")}

    if officer_rows is None and not select_all and not read_officers:
        return {"ok": True, "steps": steps, "note": "停在命令界面，未选武将"}

    tab = open_officer_picker(hwnd)
    steps.append({"step": "open_picker", "ok": bool(tab.get("xy")), **tab})
    if not tab.get("ok"):
        # ⭐⭐ **全新场景（2026-09-19 用户指出）**：
        # 本旬**只剩 1 名可行动武将**时，「執行武將」标签会**置灰**（不是蓝色），
        # 台上已自动填好那一个人 —— **不用进选将弹窗，直接按「執行」**。
        # （旧代码在这里退回硬编码坐标 (410,362) 去点，白点一下还报错方向。）
        return _finish_without_picker(hwnd, steps, cmd,
                                      explicit_rows=officer_rows, who=who)

    if read_officers:
        # ⭐ 「降级模型」**第一轮**：只把名单端出来，**不选、不提交**。
        # agent 看过名单之后再带 session 回来补 `officers`（见 san9mcp/panels.py）。
        # 这样 agent 不必凭空知道"这城有谁、哪几行能点"。
        offs = picker_officers(hwnd)
        steps.append({"step": "read_picker", "n": len(offs)})
        if not offs:
            return {"ok": False, "steps": steps, "failed_at": "read_picker",
                    "commanded": who,
                    "reason": "选将弹窗开了，但**一个武将行都没读出来** —— "
                              "没法给 agent 一份可信的名单，停手（宁可不做）"}
        return {"ok": True, "steps": steps, "stage": "need_officers",
                "commanded": who, "officers": offs}

    return _pick_and_finish(hwnd, steps, officer_rows=officer_rows,
                            select_all=select_all, commanded=who)


def finish_officer_command(hwnd: int, rows_idx: list[int] | None = None,
                           select_all: bool = False) -> dict:
    """选将弹窗**已经开着**时，把剩下的事做完（选 → 決定 → 執行）。

    用于「降级模型」**第二轮**：agent 看过名单之后带着 session 回来。
    `rows_idx` 是**名单里的行号**（0 起，就是第一轮 `officers[].row`）——
    这里现读一遍名单把行号翻成 y，**不用 agent 关心 y**。

    ⚠️ **不假设弹窗还开着** —— 先读名单核实。读空就停手报错，
    绝不往空界面上乱点（踩过太多次）。
    """
    steps: list[dict] = []
    offs = picker_officers(hwnd)
    steps.append({"step": "verify_picker_open", "n": len(offs), "ok": bool(offs)})
    if not offs:
        return {"ok": False, "steps": steps, "failed_at": "picker_closed",
                "reason": "选将弹窗不在了（session 期间被关掉了？）—— 请重新走一轮"}

    officer_rows = None
    if not select_all:
        if not rows_idx:
            return {"ok": False, "steps": steps, "failed_at": "no_rows",
                    "reason": "既没传 officers=\"all\"，也没传行号数组",
                    "officers": offs}
        bad = [i for i in rows_idx if not (0 <= i < len(offs))]
        if bad:
            return {"ok": False, "steps": steps, "failed_at": "row_out_of_range",
                    "reason": ("行号 %s 超出范围 —— 这份名单只有 %d 行（0~%d）"
                               % (bad, len(offs), len(offs) - 1)),
                    "officers": offs}
        ymap = {o["row"]: o["y"] for o in offs}
        officer_rows = [ymap[i] for i in rows_idx]
        steps.append({"step": "map_rows", "rows_idx": rows_idx, "y": officer_rows})

    r = _pick_and_finish(hwnd, steps, officer_rows=officer_rows,
                         select_all=select_all)
    r.setdefault("picked_rows_idx", rows_idx if not select_all else "all")
    return r


def _pick_and_finish(hwnd: int, steps: list[dict],
                     officer_rows: list[int] | None = None,
                     select_all: bool = False, commanded=None) -> dict:
    """选将弹窗**已开** → 选人 → 決定（验变绿）→ 執行。"""
    if select_all:
        # ⭐ 全选走「總括選擇」按钮，**不再逐行点** —— 弹窗尺寸随命令变，
        #    逐行点的行 y 检测很脆（設施弹窗行距 30、移動弹窗行距 50）。
        pk = pick_all_officers(hwnd)
        steps.append({"step": "pick_all", **pk})
        if not pk.get("ok"):
            return {"ok": False, "steps": steps, "failed_at": "pick_all",
                    "commanded": commanded,
                    "reason": "「總括選擇」没找到 —— 选武将弹窗很可能压根没打开"}
        # ★ 硬判据：**「決定」变绿 = 真的选上人了**。
        #   灰着就说明一个都没选上，此时点下去等于空提交（宁可不做）。
        g = find_button(hwnd, "green", *PICK_BTN_BAND, x0=700, x1=1010)
        steps.append({"step": "pick_all_verify", "decide_green": list(g) if g else None,
                      "ok": g is not None})
        if g is None:
            return {"ok": False, "steps": steps, "failed_at": "pick_all_none",
                    "commanded": commanded,
                    "reason": "按了「總括選擇」但「決定」没变绿 = 一个都没选上"}
    else:
        rows = officer_rows if officer_rows is not None else officer_rows_of(hwnd)
        steps.append({"step": "officer_rows", "officer_rows": rows,
                      "count": len(rows)})
        if not rows:
            return {"ok": False, "steps": steps, "failed_at": "no_officers",
                    "commanded": commanded, "reason": "没给行号，也读不出行"}
        picked = select_officers(hwnd, rows)
        steps.append({"step": "select_officers", "clicked_y": picked})
        # ★ 同一个硬判据：选完**「決定」必须是绿的**（灰着 = 一个都没选上）。
        g = find_button(hwnd, "green", *PICK_BTN_BAND, x0=700, x1=1010)
        steps.append({"step": "decide_green", "at": list(g) if g else None,
                      "ok": g is not None})
        if g is None:
            return {"ok": False, "steps": steps, "failed_at": "decide_grey",
                    "commanded": commanded,
                    "reason": "点了这几行但「決定」还是灰的 = 没选上（行号可能不对）。"
                              "**没有提交任何东西。**"}
    dpos = confirm_picker(hwnd)
    steps.append({"step": "confirm", "at": list(dpos) if dpos else None})
    epos = click_execute(hwnd)
    steps.append({"step": "execute", "at": list(epos) if epos else None,
                  "ok": epos is not None})
    out = {"ok": epos is not None, "steps": steps, "commanded": commanded,
           "failed_at": None if epos else "execute"}
    if epos is not None:
        # ⭐ 读命令的**结果播报框** = 游戏自己报的账。
        # **設施 9 条是「立即」见效**，所以读到这个框就是数字闭环本身，
        # 不必等过旬。（实现放在 atoms 里，这里局部导入避免循环依赖。）
        try:
            from .atoms import capture_result_box
            out["result_box"] = capture_result_box(hwnd)
        except Exception as e:
            out["result_box"] = {"found": False,
                                 "why": "%s: %s" % (type(e).__name__, e)}
    return out


def _finish_without_picker(hwnd: int, steps: list[dict], cmd: str,
                           explicit_rows=None, who=None) -> dict:
    """「執行武將」标签**不是蓝色**时的收尾。

    ⭐ **典型情形（2026-09-19 用户指出）**：本旬**只剩 1 名可行动武将** ——
    标签会**置灰**（不是蓝色），台上已经自动填好那一个人，
    **不需要进选将弹窗，直接按「執行」就行**。

    ⛔ **绝不点猜出来的坐标。** 唯一判据是**「執行」按钮的颜色**：
      - 绿   ⇒ 武将确实已就位 → 直接执行（没有选择可做）
      - 灰/找不到 ⇒ 没就位 → **停手报错**
    """
    if explicit_rows and sorted(set(int(r) for r in explicit_rows)) != [0]:
        return {"ok": False, "steps": steps, "commanded": who,
                "failed_at": "no_picker_explicit_rows",
                "reason": ("要求只选行 %s，但「執行武將」标签是**灰的** —— "
                           "本旬大概只剩 1 名可行动武将，**游戏已自动选中，没有可挑的名单**，"
                           "所以无法兑现「只选某几行」，**停手**。"
                           "本旬只有 1 人时请改用 officers=all。" % (explicit_rows,)),
                "next": "换一座本旬武将更多的设施；或先用 san9_look 看设施"}

    epos = click_execute(hwnd)          # 内部用 find_button 定位绿按钮，找不到返回 None
    steps.append({"step": "execute_without_picker",
                  "at": list(epos) if epos else None, "ok": epos is not None})
    if epos is None:
        # ⭐ 先读底栏 —— 它可能直接说明为什么不行（比如"命令界面压根没打开"的那种）。
        h = read_hint(hwnd)
        ex = explain_hint(h)
        if ex:
            return {"ok": False, "steps": steps, "commanded": who,
                    "failed_at": ex["code"], "reason_code": ex["code"],
                    "symptom": "no_officer_tab", "game_says": h or None,
                    "reason": "%s（游戏底栏原话：「%s」）" % (ex["why"], h),
                    "next": ex["nxt"]}
        return {"ok": False, "steps": steps, "commanded": who,
                "failed_at": "no_officer_tab", "game_says": h or None,
                "reason": ("「執行武將」标签不是蓝色、且**「執行」按钮也不是绿的** "
                           "⇒ 武将没就位 → **停手，绝不点任何猜出来的坐标**。"
                           "（也可能这个命令界面压根没打开 —— 底栏读到的是 %r）" % (h or "",))}
    out = {"ok": True, "steps": steps, "commanded": who,
           "executed": True, "single_officer": True,
           "note": ("本旬只剩 1 名可行动武将 → 游戏已自动选中，"
                    "**不需要进选将弹窗，直接执行了**（没有选择可做）")}
    try:
        from .atoms import capture_result_box
        out["result_box"] = capture_result_box(hwnd)
    except Exception as e:
        out["result_box"] = {"found": False, "why": "%s: %s" % (type(e).__name__, e)}
    return out


def officer_rows_of(hwnd: int, count: int = 5) -> list[int]:
    """打开弹窗后，把可勾选的武将行 y 列出来（最多 count 行）。"""
    return officer_rows(hwnd, count=count)

# ---------------------------------------------------------------- 关掉命令菜单

# 实测（2026-09-17 夜）：**Esc 关不掉都市命令菜单**（连按 4 次帧差 0.5，菜单纹丝不动）。
# 说明书 P.9 里也没有"关闭命令菜单"的手段 —— 右键在都市以外是**弹出另一个菜单**
# （進行/情報/儲存/載入），只有在「一覽情報」这类画面右键才是"返回"。
# → 关掉它只能**点地图上没东西的地方**。
#
# ⛔⛔ 2026-09-19：这里原来有 `CLOSE_POINTS` **7 个硬编码落点 × 2 轮**
#   （[(350,250),(250,290),(900,240),(380,870),(330,160),(960,300),(420,200)]），
#   一次调用最多**盲点 14 下**。用户当面制止后**整段删除**。
#   **定格规矩：不许出现"几个备用坐标依次试"的写法。**
#   要关菜单 → **先程序化定位一个空白点，再点一次**。

BLANK_MAP_ZONE = (12, 74, 1086, 852)    # 地图视口（真正的边界由 BLANK_AVOID 收紧）
# ⛔ 点「地图空白处」时**必须避开**的区域。这是**实测的界面布局**，不是「候选点列表」。
# 依据：`san9ai/mcp/shots/0001_look.png`（北海命令菜单打开时的一帧，1280×960 客户区）
#
# ⚠️ **为什么必须要这张表**：光靠「低方差」分不清「平坦地形」和「平坦的 UI 面板」——
# 实测 48×48 最小方差点落在 **(996,688)**，那是右侧「條件/效果」**文字面板**；
# 点下去屏幕毫无变化（go 卡在 0.14），`recover` 因此失败、**把菜单留在了屏幕上**。
# 修法是「把不可能是地图的地方先划掉」，而不是「多点几个地方试试」（后者是禁止的）。
BLANK_AVOID = (
    (0, 0, 1280, 74),          # 顶栏：年月 / 信望 / 資金 / 兵糧 / 疆界 / 情報 / 功能
    (0, 74, 352, 900),         # 左侧「設施情報」面板（含 PANEL_REG=(15,135,350,700)）
    (826, 28, 1218, 262),      # 右上「小地图」面板
    (1086, 262, 1280, 900),    # 右侧「移動リスト」
    (530, 400, 1030, 760),     # 命令菜单本体 + 右侧「條件/效果」文字块 ← 这次选错就在这
    (300, 755, 1010, 900),     # 底栏提示
)
BLANK_PATCH = 48                        # 判「平坦」用的补丁边长
BLANK_STEP = 40                         # 候选网格步长
# 补丁灰度标准差的上限。**防"点到 UI 面板"靠的是 `BLANK_AVOID` 那张避让表，
# 不是这个阈值** —— 阈值只用来避开**高纹理的都市/港口图标**。
# ⚠️ 原来卡 13.0，实测在陳留那片多树地形上**一个候选都过不了** → recover 返回
#    "找不到足够平坦的点" → 菜单留在屏幕上（清理失败）。
#    放宽到 24.0：地图上普通地形都能过，图标（纹理高得多）仍然被挡在外面。
BLANK_STD_MAX = 24.0


def _go_clear(hwnd: int) -> bool:
    from . import anchors
    gw, gh, buf = winio.grab(hwnd)
    st = anchors.AnchorBook().state(buf, gw, gh, "strategy")
    go = next((a for a in st if a["name"] == "go"), None)
    return bool(go and go["found"] and go["score"] >= 0.8)


def locate_blank_map_point(hwnd: int) -> dict:
    """**程序化定位**一个"地图空白处"（用来取消选中、关掉命令菜单）。

    ⛔ 绝不返回硬编码坐标。判据：在地图视口内按网格取样，取
    **48×48 补丁灰度标准差最小** 的位置；标准差必须 < `BLANK_STD_MAX` 才算空白地形。
    都市 / 港口图标、文字、界面边框都是高纹理，会被这个判据自然避开。

    找不到（地图被界面盖住、或整片都是高纹理）→ 返回 `{"found": False, ...}`，
    **调用方必须什么都不做** —— 不许退回硬编码坐标。
    """
    import cv2
    img = _frame(hwnd)
    gray = cv2.cvtColor(np.ascontiguousarray(img[:, :, :3]), cv2.COLOR_BGR2GRAY)
    x0, y0, x1, y1 = BLANK_MAP_ZONE
    h, w = gray.shape[:2]
    x1, y1 = min(x1, w - BLANK_PATCH), min(y1, h - BLANK_PATCH)

    best: tuple | None = None
    for y in range(y0, y1, BLANK_STEP):
        for x in range(x0, x1, BLANK_STEP):
            # 补丁与避让区**只要有重叠**就跳过（不是只看左上角）
            px1, py1 = x + BLANK_PATCH, y + BLANK_PATCH
            if any(ax0 < px1 and x < ax1 and ay0 < py1 and y < ay1
                   for (ax0, ay0, ax1, ay1) in BLANK_AVOID):
                continue
            sd = float(gray[y:y + BLANK_PATCH, x:x + BLANK_PATCH].std())
            if best is None or sd < best[0]:
                best = (sd, x, y)
    if best is None:
        return {"found": False, "why": "候选网格没有一个落在地图区"}
    sd, x, y = best
    if sd > BLANK_STD_MAX:
        return {"found": False, "std": round(sd, 2),
                "why": "地图上没有足够平坦的位置（最小标准差 %.1f > %.1f）—— 不点"
                       % (sd, BLANK_STD_MAX)}
    return {"found": True, "x": x + BLANK_PATCH // 2, "y": y + BLANK_PATCH // 2,
            "std": round(sd, 2)}


def close_menu(hwnd: int, tries: int = 2) -> bool:
    """关掉都市命令菜单。返回是否回到通畅的战略面。

    ⛔⛔ 2026-09-19 重写（用户当面制止）：旧实现是 `CLOSE_POINTS` 7 个硬编码落点
    × 2 轮，一次调用最多盲点 14 下 —— **已整段删除**。

    现在的做法：
      1. 已经通畅 → True
      2. **程序化定位**一个地图空白点（`locate_blank_map_point`）
      3. **只点这一个**，然后验 `go` 锚点
      4. 没关掉 → **重新定位**（画面变了，位置跟着变）→ 再点一次
      5. 两次都不行 → **返回 False，留图，绝不换坐标继续试**

    `tries` 是"定位并点"的次数上限，每次**重新定位**、不复用旧坐标，默认 2。
    """
    if _go_clear(hwnd):
        return True
    for _ in range(max(1, tries)):
        pt = locate_blank_map_point(hwnd)
        if not pt.get("found"):
            return False                    # ⛔ 定位不到 → 什么都不做
        winio.click_client(hwnd, pt["x"], pt["y"], settle=0.3)
        time.sleep(0.6)
        if _go_clear(hwnd):
            return True
    return False
