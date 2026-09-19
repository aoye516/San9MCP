"""人材 → 登庸：逐步驱动（把在野/他势力武将招募过来）。

⭐ 铁律：每个 `step_*` **恰好一次改屏动作**。

真机 2026-09-20 现量（用户直接把「選擇武將」屏打开给我看）：

    人材(主菜单2) → 登庸(子菜单3)
      → 点「對象武將」→ **「選擇武將」全屏表**（就是这一屏！）
        → ↑↓ 选行 → Enter 确认
          → 军师气泡（等它自己消失 ~4s）
            → 点绿「執行」

与「探索」的关系
================
**前段完全同族**（同一个「人材」子菜单、同一个命令界面外观、同样"军师气泡"坑），
差别只在**目标选择器**：

| | 探索 | 登庸 |
|---|---|---|
| 目标 | 地域（選擇地域表，**鼠标点行**）| **武将**（選擇武將全屏表，**↑↓ + Enter**）|
| 表行原点 | `ROW0_Y=304` | ⚠️ **323** |
| 列 | 地域/到達預定/州/都市/勢力/工作物/在野 | **武將/身分/忠誠/勢力/所在/統率/武力/智力/政治/到達預定** |
| 数字读得出吗 | ⛔ 读不出（5×12 小字号）| ✅ **读得出**（字号大，OCR 直接中）|

⚠️ 三张表的行原点各不相同，⛔ 别互相套用：
**设施表 304 · 地域表 304 · 選擇武將表 323**。
"""

from __future__ import annotations

import collections
import time

from san9 import cmdscreen, namelock, ocr, targetlist, winio

# ── 菜单项序（主菜单 6 项：設施0 軍事1 人材2 計略3 外交4 任免5）────
MAIN_INDEX = 2                       # 人材
SUB_INDEX = 3                        # 召來0 移動1 探索2 **登庸3**

# ── 「選擇武將」全屏表的几何（真机现量）────────────────────────────
ROW0_Y = 323
ROW_PITCH = 30
N_SLOTS = 15
"""行几何。实测行中心 `y = 323, 353, 384, 413, 444, 473, 504, 534, 563, 594, 624, 653, 684, 713, 744`，
pitch **30.00**，共 **15** 行。

⚠️ **原点 323 ≠ 设施表/地域表的 304** —— 三张表的表头高度不同，⛔ 别套用。
"""

HIGHLIGHT_MIN_MEAN = 45.0
"""判定"哪一行是高亮"的亮度门槛（灰度均值）。

实测：高亮行 **90.9**，其余行 **23~30** —— 间隔极大，所以这个判据很稳。
"""

COLUMNS = (
    # (字段名, x0, x1, 是否数字)
    ("name",  215, 300, False),
    ("status", 320, 380, False),   # 身分：在野 / 一般 / 軍師 …
    ("loyal", 400, 460, True),     # 忠誠（在野 是 `---`）
    ("force", 465, 540, False),    # 勢力（在野 是空）
    ("where", 560, 660, False),    # 所在（城池/港）
    ("lead",  680, 740, True),     # 統率
    ("war",   750, 810, True),     # 武力
    ("intel", 820, 880, True),     # 智力
    ("pol",   890, 950, True),     # 政治
    ("eta",   990, 1070, True),    # 到達預定（行军天数）
)
"""各列 x 范围（客户端坐标，真机从单一尺度 OCR 框现量）。

⚠️ x 是**框的左边界**，所以每列给的是自己那一格左右各留了点余量的区间。
"""

TABLE_SCAN_Y = (305, 815)
"""表体纵向范围（避开上方两行能力页签 与 下方「中止」按钮）。"""

DIALOG_AUTO_DISMISS_S = 4.0
"""等「軍師」气泡自己消失要多久（同 `searchcmd`）。

⛔ **这段时间一个键、一下鼠标都不许动** —— 气泡可能正好压着「執行」，
   此时点它只是把气泡关掉，命令**不会**提交（2026-09-20 在探索上踩实了，见 docs/04 §14.3）。
"""


# ── 只读：判表在不在 / 读高亮行 / 读整表 ────────────────────────────

def _row_y(row: int) -> int:
    return ROW0_Y + row * ROW_PITCH


def _gray(bgr):
    return (bgr[:, :, 0].astype(float) * 0.114
            + bgr[:, :, 1].astype(float) * 0.587
            + bgr[:, :, 2].astype(float) * 0.299)


def highlight_row(hwnd, bgr=None) -> dict:
    """读**当前高亮在哪一行**（只读）。

    判据 = 该行横条的灰度均值 > `HIGHLIGHT_MIN_MEAN`。
    实测高亮行 90.9、其余 23~30 ⇒ 间隔足够大，不需要更复杂的判据。

    ⛔ 认不出就返回 `row=None`，**绝不猜**（⛔ 方向键是相对移动，猜错就会选错人）。
    """
    if bgr is None:
        bgr = ocr._grab_bgr(hwnd)
    if bgr is None:
        return {"row": None, "why": "抓不到画面", "means": []}
    g = _gray(bgr)
    means = []
    for r in range(N_SLOTS):
        y = _row_y(r)
        means.append((r, round(float(g[y - 13:y + 13, 210:1140].mean()), 1)))
    bright = [(r, m) for r, m in means if m > HIGHLIGHT_MIN_MEAN]
    if len(bright) == 1:
        return {"row": bright[0][0], "mean": bright[0][1], "means": means,
                "why": None}
    if not bright:
        return {"row": None, "means": means, "threshold": HIGHLIGHT_MIN_MEAN,
                "why": ("15 行**一行都不亮**（最高 %.1f < %.1f）⇒ "
                        "要么不在「選擇武將」屏上，要么这一屏本来就没有高亮行。"
                        % (max(m for _, m in means), HIGHLIGHT_MIN_MEAN))}
    return {"row": None, "means": means, "threshold": HIGHLIGHT_MIN_MEAN,
            "candidates": [r for r, _ in bright],
            "why": ("有 %d 行同时超过门槛（%s）⇒ 分不清高亮在哪，**不按键**。"
                    % (len(bright), [r for r, _ in bright]))}


def table_present(hwnd) -> dict:
    """结构判据：**「選擇武將」全屏表开着吗**（只读）。

    判据 = 表体里**存在一条明显更亮的横条**（那就是高亮行）。
    ⛔ 不用底栏文字（OCR 繁简混排不可靠，见 docs/04 §12.5）；
    ⛔ 也不用"彩色标签没了"（军师气泡也会盖住标签，会假阳性）。
    """
    h = highlight_row(hwnd)
    return {"ok": h.get("row") is not None, "row": h.get("row"),
            "mean": h.get("mean"), "why": h.get("why")}


SCALES = (2, 3, 4, 5)
"""读这张表用的 OCR 放大倍数。

⛔ 必须**多尺度**：游戏字体小、有字间距，单次 OCR 会漏行/漏列。
   2026-09-20 离线调优（`shots/dengyong_pick.png` + 人工真值）：

   | SCALES    | 耗时   | 名字 | **身分** | 四维 |
   |---|---|---|---|---|
   | (2,3)     | 6.2s  | 8/15 | 8/15  | 53/60 |
   | (2,3,4)   | 8.6s  | 9/15 | 12/15 | 53/60 |
   | **(2,3,4,5)** | 13.4s | 11/15 | **15/15** | 53/60 |

   ⇒ 取 `(2,3,4,5)`：`身分` 全对（这是"谁是**在野**"的关键列，不能少）。
   13 秒对"两步工具"的第一步是划算的。

⛔ 但**不能**用 `targetlist._ocr_boxes(upscales=(2,3))` 那种直接拼接 ——
   那会把同一格读成 `"2525"`，必须按格投票。
"""

STATUS_KNOWN = ("在野", "一般", "軍師", "君主", "都督", "太守")
"""`身分` 列的已知取值。用于把 OCR 的错字归一（见 `_pick_status`）。"""

_CAND: list | None = None


def name_candidates() -> list:
    """名字候选表（`config/roster_s9.json` 的 722 名，含 aka）。

    ⭐ 用途是**给名字加一层校验**（`locked` / `candidate` / `unknown`）。

    ⛔⛔ **`unknown` 必须谨慎解读**：名册是**繁體**，而 OCR 经常读成**简体**
       （实测 `許靖`→`许靖`、`陳琳`→`陈琳`、`韓胤`→`韩胤` 全被判成 ratio 0.5 ⇒ unknown）。
       ⇒ **`unknown` 大多数只是"写法是简体"，不等于"这人不在册"**。
       ⛔ 别拿它当"这人一定存在"的断言（722 表是外部来源，`membership_unverified`）。
       📌 待办：把简体形折叠进候选表（同一件事卡在 docs/04 §8.4 的「简繁折叠」上）。
    """
    global _CAND
    if _CAND is not None:
        return _CAND
    import io
    import json
    import os
    from san9 import paths
    cands: list = []
    try:
        with io.open(os.path.join(paths.CONFIG, "roster_s9.json"), encoding="utf-8") as f:
            d = json.load(f)
        for o in d.get("officers") or []:
            nm = str(o.get("name") or "").strip()
            if nm:
                # ⚠️ 必须是 **dict** 形态 —— `namelock._expand` 只认 str / dict，
                #    传元组会 `TypeError: normalize() argument 2 must be str, not tuple`
                #    （2026-09-20 实测踩到，而且被 except 吞掉、静默变成 None）。
                cands.append({"name": nm, "aka": list(o.get("aka") or [])})
    except Exception:
        cands = []
    _CAND = cands
    return _CAND


def _pick_status(texts) -> str | None:
    """从同一格的多个读数里定 `身分`。

    1. 先找**精确命中已知取值**的（取票数最高）；
    2. 都没有时，退一步：**含「野」且长度 ≤3** 的算 `在野`
       （「在野」这个词 OCR 常把「在」读花，但「野」很稳；长度限制是防止
        把别人的名字误当身分 —— 这一格理论上只可能出现身分）。
    ⛔ 再判不出就返回 `None`，不猜。
    """
    if not texts:
        return None
    hit = [(t, n) for t, n in texts.items() if t in STATUS_KNOWN]
    if hit:
        return max(hit, key=lambda x: (x[1], len(x[0])))[0]
    loose = [(t, n) for t, n in texts.items()
             if "野" in t and len(t) <= 3 and any("\u4e00" <= c <= "\u9fff" for c in t)]
    if loose:
        return "在野"
    return None


def read_rows(hwnd, rows=None, bgr=None) -> list[dict]:
    """读「選擇武將」表的若干行（只读）。

    返回每行 `{row, name, status, loyal, force, where, lead, war, intel, pol, eta, *_raw}`。

    ⭐ 数字列**读得出**（字号够大）—— 与地域表那个 5×12 的「在野」不同。
    ⛔ 读不出/读歪的字段一律 `None` 并保留 `*_raw`，**不猜**。
    """
    if bgr is None:
        bgr = ocr._grab_bgr(hwnd)
    if bgr is None:
        return []
    if rows is None:
        rows = list(range(N_SLOTS))
    # ⭐ 多尺度读，**按格投票**（同一格在几个尺度下读到什么，取最常见的）
    cells: dict = collections.defaultdict(collections.Counter)
    for up in SCALES:
        try:
            boxes = ocr.read(bgr, upscale=up)
        except Exception:
            continue
        for b in boxes:
            try:
                bx, by = int(b["x"]), int(b["y"])
            except Exception:
                continue
            if not (TABLE_SCAN_Y[0] <= by <= TABLE_SCAN_Y[1]):
                continue
            r = round((by - ROW0_Y) / ROW_PITCH)
            if not (0 <= r < N_SLOTS):
                continue
            txt = (b.get("text") or "").strip()
            if not txt:
                continue
            for field, x0, x1, _num in COLUMNS:
                if x0 <= bx <= x1:
                    cells[(r, field)][txt] += 1
                    break
    out = []
    for r in rows:
        item = {"row": r, "y": _row_y(r)}
        for field, _x0, _x1, is_num in COLUMNS:
            votes = cells.get((r, field))
            if not votes:
                item[field] = item[field + "_raw"] = None
                continue
            if is_num:
                digs = {t: n for t, n in votes.items() if t.isdigit()}
                if digs:
                    best = max(digs.items(), key=lambda x: (x[1], len(x[0])))[0]
                    item[field] = int(best)
                    item[field + "_raw"] = best
                else:
                    item[field] = None
                    item[field + "_raw"] = max(votes, key=lambda t: (votes[t], len(t)))
            elif field == "name":
                best = max(votes.items(), key=lambda x: (x[1], len(x[0])))[0]
                item["name"] = best
                item["name_raw"] = best
                # ⭐ 拿 722 人名册校一下：locked=精确命中 / candidate=形近 / unknown=不在册
                try:
                    lk = namelock.lock(best, name_candidates())
                    item["name_status"] = lk.get("status")
                    item["name_nearest"] = lk.get("nearest")
                    item["name_ratio"] = lk.get("ratio")
                except Exception:
                    item["name_status"] = None
            elif field == "status":
                item[field] = _pick_status(votes)
                item[field + "_raw"] = "／".join(sorted(votes, key=lambda t: -votes[t])[:3])
            else:
                best = max(votes.items(), key=lambda x: (x[1], len(x[0])))[0]
                item[field] = None if best == "---" else best
                item[field + "_raw"] = best
        out.append(item)
    return out


# ── 步骤 ─────────────────────────────────────────────────────────────

def step_open_screen(hwnd, row: int, expect: str | None = None, gate: bool = True) -> dict:
    """选右侧列表第 `row` 行 → 开命令菜单 → 键盘走到「人材 → 登庸」→ Enter。

    ⭐ 复用 `movecmd.step_open_screen`（已参数化）——
       ⛔ 不许再抄一份走菜单的代码（教训：「能力被复刻，禁令就管不住副本」）。
    """
    from san9 import movecmd
    out = movecmd.step_open_screen(hwnd, row, expect,
                                   sub_index=SUB_INDEX, name="登庸", gate=gate)
    out["step"] = "open_screen"
    return out


def step_open_picker(hwnd) -> dict:
    """点「對象武將」标签（界面上**靠上**那个彩色标签）→ 弹出「選擇武將」全屏表。

    门禁：**表真的出来了**（表体里出现明显更亮的横条）。⛔ 不看底栏文字。
    """
    out: dict = {"step": "open_picker"}
    from san9 import movecmd
    cs = movecmd.looks_like_move_screen(hwnd)
    out["command_screen_before"] = cs
    if not cs.get("ok"):
        out["why"] = ("前置门：不像是「登庸」命令界面（%s）⇒ **一个像素都没点**。"
                      % cs.get("why"))
        return out
    xy, how = cmdscreen.find_target_label(hwnd)
    out["label"] = {"xy": xy, "how": how}
    if xy is None:
        out["why"] = "找不到「對象武將」标签（how=%s）⇒ **一个像素都没点**。" % how
        return out
    winio.click_client(hwnd, xy[0], xy[1], settle=0.35)
    for wait in (1.2, 0.8, 0.8, 0.8):
        time.sleep(wait)
        t = table_present(hwnd)
        if t.get("ok"):
            out["table"] = t
            out["clicked"] = list(xy)
            out["ok"] = True
            return out
    out["why"] = ("点了「對象武將」但**没出表**（表体里一直没出现高亮横条）"
                  "⇒ 停手，由调用方决定。")
    return out


def step_move_to_row(hwnd, target: int, tries: int = 3) -> dict:
    """用 ↑↓ 把高亮移到第 `target` 行。

    ⛔ **相对移动，所以必须先读到当前位置**（`highlight_row`）——
       读不到就**一个键都不按**（猜错就会登庸错人，比失败更糟）。
    ⛔ 每按一轮都**重新读数**，到位就停（不盲按固定次数）。
    """
    out: dict = {"step": "move_to_row", "target": target}
    if not (0 <= target < N_SLOTS):
        out["why"] = "target=%d 越界（表上只有 %d 行）⇒ **一个键都没按**。" % (target, N_SLOTS)
        return out
    if not table_present(hwnd).get("ok"):
        out["why"] = "表不在 ⇒ **一个键都没按**。"
        return out
    moved = []
    for _ in range(max(1, tries)):
        h = highlight_row(hwnd)
        cur = h.get("row")
        out["current"] = cur
        if cur is None:
            out["why"] = ("读不到高亮在哪（%s）⇒ **一个键都没按** —— "
                          "方向键是相对移动，猜错就会选错人。" % h.get("why"))
            return out
        if cur == target:
            out["presses"] = moved
            out["ok"] = True
            return out
        key = "down" if target > cur else "up"
        steps = min(abs(target - cur), 5)
        for _ in range(steps):
            winio.press(key)
            time.sleep(0.25)
            moved.append(key)
    out["presses"] = moved
    h = highlight_row(hwnd)
    out["current_after"] = h.get("row")
    if h.get("row") == target:
        out["ok"] = True
        return out
    out["why"] = ("按了 %s 之后高亮停在 %s 行（目标 %d）⇒ 没到位，⛔ 不硬按。"
                  % (moved, h.get("row"), target))
    return out


def step_confirm(hwnd, settle_s: float = 1.8) -> dict:
    """按 Enter 确认选中的武将（= 决定登庸对象）。

    ⚠️ 确认后会弹**軍師气泡**（盖住「對象武將」/「執行武將」两行）⇒
       本步**不拿彩色标签当门禁**，只判「**已经离开那张全屏表**」+ 报出绿「執行」在不在。
    """
    out: dict = {"step": "confirm"}
    if not table_present(hwnd).get("ok"):
        out["why"] = "表不在 ⇒ **一个键都没按**。"
        return out
    h = highlight_row(hwnd)
    out["highlight_before_enter"] = h.get("row")
    winio.press("enter")
    time.sleep(settle_s)
    out["left_table"] = not bool(table_present(hwnd).get("ok"))
    if not out["left_table"]:
        out["why"] = "按了 Enter 但**全屏表还在** ⇒ 没确认上，⛔ 不再按。"
        return out
    g = execute_ready(hwnd)
    out["execute_button"] = list(g) if g else None
    if g is None:
        out["why"] = ("已离开全屏表、回到命令界面，但**「執行」是灰的**"
                      "⇒ 对象没选上，或执行武将没定。"
                      "⭐ 该城本旬有 **≥2** 名可行动武将时游戏**不会**自动填人，"
                      "需要先点「執行武將」选人 —— ⛔ 那条本工具还没做。")
        return out
    out["ok"] = True
    return out


def execute_ready(hwnd) -> tuple[int, int] | None:
    """绿「執行」在不在。**游戏自己给的硬信号**：对象 + 执行武将都齐了才变绿。"""
    bgr = ocr._grab_bgr(hwnd)
    if bgr is None:
        return None
    from san9 import movecmd
    return cmdscreen.find_button_in(bgr, "green", *movecmd.MOVE_BTN_BAND)


def step_execute(hwnd, settle_s: float = 4.0, after_s: float = 9.0) -> dict:
    """点绿「執行」提交。

    ⛔⛔ **先等 `DIALOG_AUTO_DISMISS_S` 秒再点** —— 军师气泡可能正好压在「執行」上，
       此时点下去只是把气泡关掉、**命令不提交**（探索上已踩实，见 docs/04 §14.3）。
    ⛔ 不轮询（用户 2026-09-20：「不用轮询逻辑」）。
    """
    out: dict = {"step": "execute"}
    g = execute_ready(hwnd)
    out["green_execute"] = list(g) if g else None
    if g is None:
        out["why"] = "「執行」不是绿的 ⇒ 对象或执行武将还没齐 ⇒ 不点。"
        return out
    out["waited_for_bubble_s"] = settle_s
    time.sleep(settle_s)
    g2 = execute_ready(hwnd)
    out["green_execute_after_wait"] = list(g2) if g2 else None
    if g2 is None:
        out["why"] = "等气泡消失后**找不到绿「執行」** ⇒ 界面已变，⛔ 不点。"
        return out
    winio.click_client(hwnd, g2[0], g2[1], settle=0.35)
    time.sleep(after_s)
    out["go_clear"] = bool(cmdscreen._go_clear(hwnd))
    out["checked_after_s"] = after_s
    if not out["go_clear"]:
        out["why"] = ("点了「執行」但 %.1fs 后还没回到干净战略面 ⇒ ⚠️ **可能只是还没到位**"
                      "（这个时间不固定），请人看一眼屏幕；必要时调 `san9_recover`。"
                      "⛔ 本工具**不会**去按键猜。" % after_s)
        return out
    out["ok"] = True
    return out


def step_abort(hwnd) -> dict:
    """把界面收回**干净战略面**（只读/dry_run 的收尾）。

    ⭐ **委托给 `atoms.recover`**（Esc 优先、零坐标、带 `go` 锚点判据）。
    ⛔ 不许自己另写一套退屏逻辑（探索上就是这么踩坑的，见 docs/04 §14.7）。
    """
    from san9 import atoms
    out: dict = {"step": "abort"}
    try:
        r = atoms.recover(hwnd) or {}
    except Exception as e:                                   # pragma: no cover
        out["why"] = "调用 recover 抛异常：%s" % e
        return out
    out["recover"] = {k: v for k, v in r.items()
                      if k in ("recovered", "how", "actions", "why", "shot")}
    # ⚠️ `atoms.recover` 的键是 **`recovered`** 不是 `ok`。
    out["ok"] = bool(r.get("recovered"))
    if not out["ok"]:
        out["why"] = "recover 没把界面收回干净战略面（how=%s）⇒ 请人看一眼屏幕。" % r.get("how")
    return out
