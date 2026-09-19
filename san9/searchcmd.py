"""人材 → 探索：逐步驱动。

⭐ 铁律：每个 `step_*` **恰好一次改屏动作**。⛔ 不许把两步串进一个函数
（2026-09-18/19 被抓三次的教训）。

真机验证（2026-09-20 凌晨，用户在场）：平原 → 樂陵，全链一次通过。

    人材(主菜单2) → 探索(子菜单2)
      → 点「目標地域」
        → **点「在野」列标题排序**   ← 有人才的地域排到最上面
          → 点某一行（= 选中并直接返回）
            → 点绿「執行」提交

与「移動」的四处关键差别：

| | 移動 | 探索 |
|---|---|---|
| 目标是 | **设施**（選擇設施表）| **地域**（**選擇地域**表，另一张表）|
| 选目标的方式 | 方向键选行 + **Enter** | **鼠标点行 = 选中并直接返回**（不用方向键、不用 Enter）|
| 排序 | 无 | ⭐ **点「在野」列标题**（(724,268)）把有人才的地域排到最前 |
| 执行武将 | ≥2 人要手动勾选 + 「決定」 | 用户说明：**只能派一个将领**（本次因只剩 1 人自动填充，⛔ 多人路径未验）|

⛔⛔ 两个**真机才发现的坑**（都写进 docs/04 §14）：

1. **排序那一下会把「行高亮」清掉**（`targetlist.highlight_row` 报 `row=null`，
   最强跟次强只差 0.62），而且**方向键唤不回来**（按了 4 下画面逐像素不变）。
   ⇒ 探索选行**只能鼠标点**，⛔ 不许复用「移動」那套 `select_target`（高亮定位）。
2. **对话框是"信息展示"，几秒自动消失**（用户 2026-09-20 原话：
   「不需要操作的时候展示一会就会消失」）。
   实例：提交前自动弹**軍師对话框**（張寶「成是成敗全憑時運哪……」）；
   提交后执行武将回应一句（裴元紹「屬下領命，只要是有利的情報，屬下都會帶回來的。」）。
   ⇒ ⛔ **不要去按 Enter**。按了虽然当下没坏处（仍在干净战略面），
   但那是"猜默认按钮"，属于本项目明令禁止的动作。
"""
from __future__ import annotations

import io
import json
import os
import time

from san9 import cmdscreen, namelock, ocr, paths, targetlist, winio

# ── 菜单项序（主菜单 6 项：設施0 軍事1 人材2 計略3 外交4 任免5）────
MAIN_INDEX = 2                       # 人材
SUB_INDEX = 2                        # 召來0 移動1 探索2 登庸3

# ── 「選擇地域」表的几何：行与设施表**完全相同**（真机现量）──
ROW0_Y = targetlist.ROW0_Y           # 304
ROW_PITCH = targetlist.ROW_PITCH     # 30
N_SLOTS = targetlist.N_SLOTS         # 15
REGION_COL_X = 280
"""点行时用的 x（「地域」列，行内任意位置都算命中该行）。"""

REGION_COL_BAND = (225, 285, 340, 805)
"""读「地域」列名字用的框 `(x0, y0, x1, y1)`（`annotate.text_rows` 同序）。"""

SORT_HEADER_XY = (724, 268)
"""⭐「**在野**」列标题的客户端坐标。

2026-09-20 用户悬停给出：`GetCursorPos` 六次采样 → 屏幕 (725,301)、
转客户端 = **(724, 268)**（`client_rect_screen` = (1,33,1280,960)），完全稳定。
点了它之后：列标题变 `在野▼`、**在野 > 0 的地域全部排到最上面**
（实测 row0..6 是 樂陵/下邳/安陽/陸口/會稽/江津/陰平，都是 1；row7 起全是 0）。
⇒ 一次点击就把"上哪找人才"扫出来了。
"""

TABLE_TEAL_BAND_INDEX = 0
"""「選擇地域」右下那个青绿「地圖」按钮所在的色带下标（`movecmd.TEAL_BANDS[0]`）。

真机现量：地域表的「地圖」在 **(739, 783)**，设施表的在 (691, 783)
—— x 不同但落在同一条带里 ⇒ 用"带里有没有青绿按钮"当**表格已打开**的门禁，两张表通用。
"""

SORT_VERIFY_ROWS = 3
"""排序后要复核前几行的地名（少了验不出，多了慢 —— 每行一次 OCR）。"""

ARROW_BOX = (750, 264, 763, 280)
"""列标题右侧那个**排序箭头**的框 `(x0, y0, x1, y1)`（客户端坐标）。

2026-09-20 用两张实拍帧（`shots/sort_before.png` 未排序 / `sort_after.png` 排序后）
**逐像素对比**定位：只在点过列标题之后出现，是个 **4×4 px 的小三角**
（x 754..757 / y 270..273）。

    ▼（降序）  上面 4 px 宽、下面 2 px 宽 —— 实测就是这一种
    ▲（升序）  应该反过来（未采到样本，靠形状推断）

⭐ 为什么非要有这个东西：**「在野」是数字列，字形库没有这个字号**（读不出），
   而反复点列标题会在 **降序 / 升序** 之间来回切 —— 分不清方向时
   `region_row=0` 可能正好是**最差**的那个地域。箭头不需要认数字就能定方向。
"""


def sort_direction(hwnd, bgr=None) -> dict:
    """读「在野」列标题上的排序箭头（只读）。

    返回 `{dir, ink, profile, why}`，`dir` ∈ `desc`（在野多的在前）/ `asc` / `none`（没排过）/ `unclear`。

    ⛔ 不用 OCR（4×4 px 的字形 OCR 认不出）；用**行墨迹剖面**判形状：
       上宽下窄 = ▼（降序），上窄下宽 = ▲（升序）。
    ⛔ `unclear` / `none` 时**不许猜方向** —— 由调用方决定怎么报。
    """
    if bgr is None:
        bgr = ocr._grab_bgr(hwnd)
    if bgr is None:
        return {"dir": "unclear", "ink": 0, "profile": [], "why": "抓不到画面"}
    x0, y0, x1, y1 = ARROW_BOX
    sub = bgr[y0:y1, x0:x1]
    gray = sub[:, :, 0].astype(int) * 0.114 + sub[:, :, 1].astype(int) * 0.587 \
        + sub[:, :, 2].astype(int) * 0.299
    prof = (gray > 120).sum(axis=1).tolist()
    ink = int(sum(prof))
    out = {"dir": "unclear", "ink": ink, "profile": prof}
    if ink < 6:
        out["dir"] = "none"
        out["why"] = "框里几乎没有墨迹 ⇒ **还没排过序**（列标题上没有箭头）。"
        return out
    half = len(prof) // 2
    top, bot = sum(prof[:half]), sum(prof[half:])
    if top > bot:
        out["dir"] = "desc"
        out["why"] = "箭头**上宽下窄** ⇒ ▼ 降序（在野多的在前）。"
    elif bot > top:
        out["dir"] = "asc"
        out["why"] = "箭头**上窄下宽** ⇒ ▲ 升序（**在野 0 的排在最前** ⇒ row0 不是最优！）。"
    else:
        out["why"] = ("墨迹上下对称（top=%d bot=%d）⇒ **看不出方向**。" % (top, bot))
    out["split"] = {"top": top, "bot": bot}
    return out


# ── 候选表 ───────────────────────────────────────────────────────────

_CAND: list[str] | None = None


def region_candidates() -> list[str]:
    """「地域」列的地名候选表。

    = `config/roster_facilities.json` 的 185 个**显示名**（`樂陵港`）
      **再加上去掉「港 / 關」后缀的形式**（`樂陵`）—— 因为「選擇地域」表里
      写的是**地名**，不是显示名。

    真机核对 11/11：樂陵/高唐/安陽/陸口/江津/白馬/東萊/臨淄/東阿/安德 全部命中
    （它们都是 `…港` 显示名），下邳/會稽/陰平/頓丘/界橋/東平/河間/泰山/清河 本来就同名。
    ⛔ 候选表只用于**读名字**，⛔ 不用它当"这地域存在"的断言（185 表是外部来源）。
    """
    global _CAND
    if _CAND is not None:
        return _CAND
    names: list[str] = []
    try:
        with io.open(os.path.join(paths.CONFIG, "roster_facilities.json"),
                     encoding="utf-8") as f:
            d = json.load(f)
        for f_ in d.get("facilities") or []:
            n = str(f_.get("name") or "").strip()
            if not n:
                continue
            names.append(n)
            for suf in ("港", "關"):
                if n.endswith(suf) and len(n) > len(suf):
                    names.append(n[:-len(suf)])
    except Exception:
        pass
    _CAND = sorted(set(names))
    return _CAND


# ── 只读：读「地域」列 / 判在不在表上 ───────────────────────────────

def _row_y(row: int) -> int:
    return ROW0_Y + row * ROW_PITCH


def read_row_names(hwnd, rows) -> list[dict]:
    """读「地域」列若干行的名字（只读，**一次抓帧**）。

    ⭐ 用 `ocr.read_place_list` —— 它**先程序化检出行、再逐行裁出来单独 OCR**。
       ⛔ 2026-09-20 真机踩到：自己用 `_ocr_boxes` 整块读、按"最近行"归位，
         会被**跨行的整条框**污染（实测读出 `北海北海`、`医北東即東莱` 这种拼接串）。

    ⛔ 名字读不出**不影响选行**（选行一律按 `row`）。名字只用于报告与事后核对。
    """
    x0, y0, x1, y1 = REGION_COL_BAND
    region = (x0, y0, x1, y1)
    found: list[dict] = []
    err = ""
    # ⭐ 优先用**多尺度投票**版（`read_list_multiscale`）—— 它专治"游戏字体小 +
    #    有字间距 ⇒ 单次 OCR 漏行"。单尺度那版（`read_place_list`）实测在这张表上
    #    只读出 1~4 行（2026-09-20 真机），漏得太多。
    try:
        ms = ocr.read_list_multiscale(hwnd, region)
        for it in ms:
            raws = [r for r in (it.get("raws") or []) if r]
            found.append({"y": int(it["y"]),
                          "raw": (max(raws, key=len) if raws else it.get("name")) or ""})
    except Exception as e:                                   # pragma: no cover
        err = "多尺度读取失败（%s），退回单尺度" % e
        try:
            found = ocr.read_place_list(hwnd, region)
        except Exception as e2:
            return [{"row": r, "y": _row_y(r), "raw": "", "name": None,
                     "status": "error", "why": "读地名抛异常：%s / %s" % (e, e2)}
                    for r in rows]
    by_row: dict[int, dict] = {}
    for it in found:
        r = round((int(it["y"]) - ROW0_Y) / ROW_PITCH)
        if 0 <= r < N_SLOTS and r not in by_row:
            by_row[r] = it
    out = []
    for r in rows:
        it = by_row.get(r)
        if not it:
            out.append({"row": r, "y": _row_y(r), "raw": "", "name": None,
                        "status": "unread", "why": "这一行没检出文字"})
            continue
        lk = namelock.lock(it.get("raw") or "", region_candidates())
        out.append({"row": r, "y": int(it["y"]), "raw": it.get("raw"),
                    "name": lk.get("locked"), "status": lk.get("status"),
                    "nearest": lk.get("nearest"), "ratio": lk.get("ratio"),
                    "why": lk.get("why")})
    for o in out:
        if err:
            o["reader_note"] = err
    return out


def read_row_name(hwnd, row: int) -> dict:
    """读第 `row` 行的地域名（一次抓帧 · 只读）。"""
    return read_row_names(hwnd, [row])[0]


def region_table_present(hwnd) -> dict:
    """结构判据：**「選擇地域」表打开了吗**。

    判据 = `movecmd.TEAL_BANDS[0]` 里有青绿按钮（表格右下那只「地圖」）。
    ⛔ 不用底栏文字（OCR 繁简混排，见 docs/04 §12.5）。
    ⚠️ 设施表用同一个判据也成立（它的「地圖」在 (691,783)，同一条带）
    ⇒ 这是"**有张表开着**"的判据，不是"这是地域表"的判据。
       区分靠**上下文**（调用方知道自己在哪条命令里），⛔ 别拿它当身份断言。
    """
    from san9 import movecmd
    xy, how = movecmd.find_teal_button(hwnd, band_index=TABLE_TEAL_BAND_INDEX)
    return {"ok": xy is not None, "xy": xy, "how": how}


def command_screen_present(hwnd) -> dict:
    """结构判据：还停在「探索」命令界面（1~2 个彩色标签 + 红「中止」）。"""
    from san9 import movecmd
    return movecmd.looks_like_move_screen(hwnd)


def execute_ready(hwnd) -> tuple[int, int] | None:
    """绿「執行」在不在。**这是游戏自己给的硬信号**：目标 + 执行武将都齐了才变绿。"""
    bgr = ocr._grab_bgr(hwnd)
    if bgr is None:
        return None
    from san9 import movecmd
    return cmdscreen.find_button_in(bgr, "green", *movecmd.MOVE_BTN_BAND)


# ── 步骤 ─────────────────────────────────────────────────────────────

def step_open_screen(hwnd, row: int, expect: str | None = None, gate: bool = True) -> dict:
    """选右侧列表第 `row` 行 → 开命令菜单 → 键盘走到「人材 → 探索」→ Enter。

    ⭐ **直接复用** `movecmd.step_open_screen`（已参数化）——
       ⛔ 不许在这里再抄一份走菜单的代码（项目教训：「能力被复刻，禁令就管不住副本」）。
    """
    from san9 import movecmd
    out = movecmd.step_open_screen(hwnd, row, expect,
                                   sub_index=SUB_INDEX, name="探索", gate=gate)
    out["step"] = "open_screen"
    return out


def step_open_target_table(hwnd) -> dict:
    """点「目標地域」标签 → 「選擇地域」表。

    门禁：**表真的出来了**（右下青绿按钮）。⛔ 不看底栏文字。
    """
    out: dict = {"step": "open_target_table"}
    cs = command_screen_present(hwnd)
    out["command_screen_before"] = cs
    if not cs.get("ok"):
        out["why"] = ("前置门：不像是「探索」命令界面（%s）⇒ **一个像素都没点**。"
                      % cs.get("why"))
        return out
    xy, how = cmdscreen.find_target_label(hwnd)
    out["label"] = {"xy": xy, "how": how}
    if xy is None:
        out["why"] = ("找不到「目標地域」标签（how=%s）⇒ **一个像素都没点**。"
                      % how)
        return out
    winio.click_client(hwnd, xy[0], xy[1], settle=0.35)
    # ⚠️ 轮询，⛔ 不睡一个固定时长就下结论（项目血案：一帧定生死）
    polls = []
    for i, wait in enumerate((1.2, 0.8, 0.8, 0.8, 0.8)):
        time.sleep(wait)
        t = region_table_present(hwnd)
        polls.append(bool(t.get("ok")))
        if t.get("ok"):
            out["gate"] = t
            out["gate_polls"] = polls
            out["clicked"] = list(xy)
            out["ok"] = True
            return out
    out["gate_polls"] = polls
    out["why"] = ("点了「目標地域」但**没出表**（右下一直没有青绿按钮，轮询 %.1fs）"
                  "⇒ 停手，由调用方决定。" % (1.2 + 0.8 * 4))
    return out


def step_sort_by_talent(hwnd) -> dict:
    """⭐ 点「在野」列标题 → 把**有人才的地域排到最上面**。

    门禁（**两道都要过**）：
      ① 行序真的变了（读前 `SORT_VERIFY_ROWS` 行的地名做前后对比）
      ② 列标题上的箭头是 **▼ 降序**（`sort_direction`）

    ⚠️ **为什么要管方向**：反复点列标题会在 **降序/升序** 之间切，
       而「在野」是数字列、本工具读不出值 ⇒ 方向搞错时 `row=0` 会正好是**最差**的地域。
       所以：点一次 → 读箭头 → 若是 ▲ 就**再点一次同一个坐标**（最多 2 次）→ 再读。
       ⛔ 这**不是**被禁的"换个坐标再试" —— 坐标始终是同一个，
         而且每次点完都有**硬读数回验**（箭头形状），不是盲试。

    ⛔ 最终拿不到 `desc` 就 `ok=false`（**宁可失败，也不假装 row0 最优**）。
    """
    out: dict = {"step": "sort_by_talent", "xy": list(SORT_HEADER_XY)}
    if not region_table_present(hwnd).get("ok"):
        out["why"] = "表不在（右下没有青绿按钮）⇒ **一个像素都没点**。"
        return out

    rows = list(range(SORT_VERIFY_ROWS))
    before = read_row_names(hwnd, rows)
    out["names_before"] = [{"row": r["row"], "raw": r["raw"], "name": r["name"]} for r in before]
    out["direction_before"] = sort_direction(hwnd)

    clicks = []
    for attempt in range(2):
        winio.click_client(hwnd, SORT_HEADER_XY[0], SORT_HEADER_XY[1], settle=0.35)
        time.sleep(1.4)
        t = region_table_present(hwnd)
        d = sort_direction(hwnd)
        clicks.append({"attempt": attempt + 1, "table": bool(t.get("ok")), "dir": d.get("dir")})
        out["clicks"] = clicks
        if not t.get("ok"):
            out["why"] = "点了列标题之后**表不见了** ⇒ 停手（可能点到了表外）。"
            return out
        if d.get("dir") == "desc":
            break
    out["direction_after"] = sort_direction(hwnd)
    out["direction_final"] = out["direction_after"]

    after = read_row_names(hwnd, rows)
    out["names_after"] = [{"row": r["row"], "raw": r["raw"], "name": r["name"]} for r in after]
    a = tuple(r["raw"] or "" for r in before)
    b = tuple(r["raw"] or "" for r in after)
    out["order_changed"] = a != b

    if out["direction_after"].get("dir") != "desc":
        out["why"] = ("点完「在野」列标题后，**箭头不是 ▼ 降序**（读到 %r：%s）"
                      "⇒ 分不清 row0 是\"在野最多\"还是\"最少\"，**不当作成功**。"
                      "%s"
                      % (out["direction_after"].get("dir"),
                         out["direction_after"].get("why"),
                         "" if out["order_changed"] else "（另外前 %d 行名字一字未变，排序也没生效）"
                         % SORT_VERIFY_ROWS))
        return out
    if not out["order_changed"]:
        out["why"] = ("箭头是 ▼ 降序了，但**前 %d 行名字一字未变** ⇒ "
                      "多半是这张表本来就已按在野降序排好（不算失败，但**本次没有新信息**）。"
                      % SORT_VERIFY_ROWS)
        out["already_sorted"] = True
    out["ok"] = True
    return out


DIALOG_AUTO_DISMISS_S = 4.0
"""等「軍師」气泡**自己消失**要多久。

用户 2026-09-20 原话：「**不需要操作的时候展示一会就会消失**」。
实测：4.5s 内已经回到干净战略面。

⛔⛔ **这段时间里一个键、一下鼠标都不许动** ——
   2026-09-20 真机踩到一个很隐蔽的坑：军师气泡（張寶「看來只有盡人事，聽天命了……」）
   **正好压在「執行」按钮上**，此时点「執行」**只是把气泡关掉，命令并没有提交**
   （底栏从 `選擇目標地域` 变成 `執行探索` 就是那一下的 hover 说明，看着像成功，其实不是）。
   再点一次才真提交。用户一句话点破：「你搞复杂了，只需要等 3-5s 直接点就行了」。
   ⇒ 正确做法：**等气泡自己消失 → 再点「執行」**，而不是去轮询、更不是连点。
"""


def step_pick_row(hwnd, row: int, settle_s: float = 1.6) -> dict:
    """**鼠标点第 `row` 行** = 选中该地域**并直接返回命令界面**（真机实测：不需要 Enter）。

    ⛔⛔ 为什么不用方向键：排序那一下把**行高亮**清掉了，`targetlist.highlight_row`
       报 `row=null`，而且方向键也唤不回来（按 4 下画面逐像素不变）
       ⇒ "移動"那套靠高亮定位的 `select_target` **在这里用不了**。

    ⚠️ 点完之后会冒出一个**「軍師」气泡**（盖住「目標地域」/「執行武將」两行），
       它**几秒自动消失、不用操作**。这会让"蓝标签 1~2 个"这条判据暂时失效
       ⇒ 本步**不拿标签当门禁**，只判「**已经离开地域表**」（结构）+ 报出绿「執行」在不在。

    ⛔ 不轮询。用户 2026-09-20：「不用轮询逻辑」。
    """
    out: dict = {"step": "pick_row", "row": row}
    if not (0 <= row < N_SLOTS):
        out["why"] = "row=%d 越界（表上只有 %d 行）⇒ **一个像素都没点**。" % (row, N_SLOTS)
        return out
    if not region_table_present(hwnd).get("ok"):
        out["why"] = "表不在 ⇒ **一个像素都没点**。"
        return out
    out["picked_region"] = {k: v for k, v in read_row_name(hwnd, row).items()
                            if k in ("row", "raw", "name", "status", "nearest", "ratio")}
    winio.click_client(hwnd, REGION_COL_X, _row_y(row), settle=0.35)
    time.sleep(settle_s)
    out["left_table"] = not bool(region_table_present(hwnd).get("ok"))
    g = execute_ready(hwnd)
    out["execute_button"] = list(g) if g else None
    if not out["left_table"]:
        out["why"] = ("点了第 %d 行，但**地域表还在** ⇒ 点击没生效"
                      "（⛔ 不重试、不换坐标 —— 请人看一眼屏幕）。" % row)
        return out
    if g is None:
        out["why"] = ("已离开地域表、回到命令界面，但**「執行」是灰的**"
                      "⇒ 目标没选上，或执行武将没定。"
                      "⭐ 该城本旬有 **≥2** 名可行动武将时游戏**不会**自动填人，"
                      "需要先点「執行武將」选人 —— ⛔ **这条本工具还没做**。")
        return out
    out["ok"] = True
    return out


def step_execute(hwnd, settle_s: float = 4.0, after_s: float = 9.0) -> dict:
    """点绿「執行」提交。

    ⛔⛔ **先等 `DIALOG_AUTO_DISMISS_S` 秒再点** —— 这是本步最容易搞错的地方。
       点行之后会弹「軍師」气泡，它**正好压在「執行」按钮上**；
       此时点下去**只是把气泡关掉，命令根本没提交**（底栏从 `選擇目標地域`
       变成 `執行探索` 那一下是 hover 说明，很容易被误判成"成功了"）。
       ⇒ 等它自己消失（用户：「不需要操作的时候展示一会就会消失」）→ 再点。
       ⛔ 不用轮询（用户 2026-09-20：「不用轮询逻辑」），也不连点。

    提交后再等 `after_s` 秒读**一次**状态（⛔ 只是读数，不是轮询）。
    实测：点完 1.5s 还在过渡（底栏空），4.5s 已干净 —— 但**这个时间不固定**
    （气泡消失 + 武将回应 + 过渡叠在一起），另一次真机 4.5s 时还没干净
    ⇒ 固定等 **9s** 再读一次，宁可多等几秒也不做轮询。
    就算这一次读到没干净，**也可能只是还没到位** —— 所以失败文案里明说"请看一眼屏幕"。
    """
    out: dict = {"step": "execute"}
    g = execute_ready(hwnd)
    out["green_execute"] = list(g) if g else None
    if g is None:
        out["why"] = ("「執行」不是绿的 ⇒ **目标或执行武将还没齐** ⇒ 不点。"
                      "（这正是本判据的分辨力：齐了才变绿）")
        return out
    out["waited_for_bubble_s"] = DIALOG_AUTO_DISMISS_S
    time.sleep(DIALOG_AUTO_DISMISS_S)
    # ⚠️ 等完之后**再测一次按钮位置** —— 气泡消失后布局才最终确定，
    #    ⛔ 不复用等之前量到的坐标。
    g2 = execute_ready(hwnd)
    out["green_execute_after_wait"] = list(g2) if g2 else None
    if g2 is None:
        out["why"] = ("等气泡消失之后**找不到绿「執行」** ⇒ 界面已经不是那个状态了，"
                      "⛔ 不点。请人看一眼屏幕。")
        return out
    winio.click_client(hwnd, g2[0], g2[1], settle=0.35)
    time.sleep(after_s)
    out["go_clear"] = bool(cmdscreen._go_clear(hwnd))
    out["checked_after_s"] = after_s
    if not out["go_clear"]:
        out["why"] = ("点了「執行」但 %.1fs 后**还没回到干净战略面** ⇒ "
                      "⚠️ **可能只是还没到位**（这个时间不固定），请人看一眼屏幕："
                      "若已回到战略面就是成功了；若还停在弹窗上，调 `san9_recover` 收拾。"
                      "⛔ 本工具**不会**去按键猜（按了只会关掉下一个气泡、白费一次机会）。"
                      % after_s)
        return out
    out["ok"] = True
    return out


def step_abort(hwnd, poll_s: float = 9.0) -> dict:
    """把界面收回**干净战略面**（dry_run 的收尾 / 只读模式的收尾）。

    ⭐ **委托给 `atoms.recover`** —— 它就是这条命的专用原语：
       Esc 优先（零坐标、不可能点错）→ 底栏 `go` 锚点判据 → 失败才轮到
       "点红「中止」/ 点地图空白"。
       ⛔ 不许在这里另写一套退屏逻辑（项目教训：「能力被复刻，禁令就管不住副本」）。

    ⚠️ 2026-09-20 真机踩到：早先这里自己找 `movecmd.MOVE_BTN_BAND` 里的红按钮，
       而**地域表开着时它的「中止」在 (966,813)** —— 不在那条带里
       ⇒ 报"找不到红「中止」"、**把游戏留脏**（工具因此判 `close_failed`）。
       实测 `recover` 用 **Esc×2** 就干净退出（画面变化 19.55 → 31.46，`go=0.8892`），
       且这正好又一次印证用户那句「**Esc 是全局返回键**」。
    """
    from san9 import atoms
    out: dict = {"step": "abort"}
    try:
        r = atoms.recover(hwnd)
    except Exception as e:                                   # pragma: no cover
        out["why"] = "调用 recover 抛异常：%s" % e
        return out
    out["recover"] = {k: v for k, v in (r or {}).items()
                      if k in ("recovered", "how", "actions", "why", "actions_done",
                               "sessions_cleared", "shot")}
    # ⚠️ `atoms.recover` 的键是 **`recovered`**，不是 `ok`。
    #    2026-09-20 我自己写错过一次：拿 `r.get("ok")` 判 ⇒ 永远 False，
    #    明明 Esc×2 已经收回干净战略面，却报成 close_failed。
    out["ok"] = bool((r or {}).get("recovered"))
    if not out["ok"]:
        out["why"] = ("recover 没把界面收回干净战略面（how=%s）⇒ 请人看一眼屏幕。"
                      % (r or {}).get("how"))
    return out
