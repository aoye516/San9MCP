"""菜鸟策略层 —— 目标是"能自己把长流程玩下去"，不是"玩得好"。

## ⛔ 铁律

`act_turn()` **永不抛异常**。任何异常 → 记一笔 → 这旬空过。
一旬失败不该葬送整局 —— 这是"能连跑 60 旬"和"连跑 5 旬"的分水岭。

## 这一版有多菜（诚实说明）

不会打仗、不会调度、不认识敌我态势。它只会：

```
看顶栏有没有钱 → 读自己的城 → 每座城挑一个能干的内政活 → 全派人去干 → 过旬
```

够菜，但**可持续**，而且每一步都留证据（`act_turn()` 的返回值和 `notes`）。

## 为什么先读菜单再下命令

命令**置灰**时光标照样停得上去、按 enter 却无事发生（实测帧差 24.7 vs 进界面的 42~53）。
不看菜单就盲下命令，会白跑一整条链（约 6 秒 + 6 次点击）才发现进不去。
`cmdmenu.open_and_read` 用**颜色**就能读出哪些命令现在可用（彩度 > 40），代价只有 3 次按键。
"""

from __future__ import annotations

import time

from . import cmdmenu, cmdscreen, ocr, winio

# 内政优先级：先做长线收益的（民心/开发），后做花钱的。
# 依据：巡察=智力、開墾/商業=政治、訓練=武力、徵兵=統率（说明书 P.13/P.14）。
FACILITY_PRIORITY = ["巡察", "開墾", "商業", "訓練", "徵兵"]

MIN_GOLD = 500
"""資金 低于这个数就不下内政命令。

設施 命令每条要 50×人数（5 人就是 250），钱不够游戏会拒绝。
攒钱过旬比"下一条注定失败的令"强。
"""

MAX_ATTEMPTS_PER_TURN = 5
"""一旬最多**尝试**几次（按尝试算，不按成功算）。

13 行设施里有 8 座城 + 5 个港口，取 5 是"一旬能顾到的上限"：再多单旬就要
超过 3 分钟，长跑 60 旬会拖到 3 小时。**不是漏了城市，是每晚只能推进这么多次。**

⚠️ 这条按尝试算很关键：原来只在"成功"时扣预算，于是一旦命令全失败，
就会一路把 13 座城全试一遍（烟测实测 188 秒一旬）。
"""

CMD_TO_OP = {v: k for k, v in cmdscreen.OP_TO_CMD.items()}
"""命令名 → op（`OP_TO_CMD` 是反过来的）。"""


def _is_clear(s) -> bool:
    v = s.view()
    return v["screen"] == "strategy" and bool(v["clear"])


class BasicPolicy:
    """固定优先级轮转内政 + 全选武将。外加一次"出征能走到哪"的探测。"""

    name = "basic"

    def __init__(self, session, want_war: bool = True):
        self.s = session
        self.hwnd = session.hwnd
        self.want_war = want_war
        self.war_tried = False          # 军事探测整个会话只做一次
        self.war_note: dict | None = None
        self._last_city_row = 0

    # ------------------------------------------------------------ 对外唯一入口

    def act_turn(self) -> dict:
        """这一旬干什么：自己决定、自己执行。返回可审计的记录。

        返回 {'notes': [...], 'actions': [...], 'topbar': {...}, 'probe': {...}}
        绝不抛异常。`actions` 里的每条都带 `ok` 和 `failed_at`，便于统计成功率。
        """
        rec: dict = {"notes": [], "actions": [], "topbar": {}, "probe": {}}
        t0 = time.time()
        try:
            self._act_turn(rec)
        except Exception as e:
            rec["notes"].append(f"策略异常（已吞掉，这旬空过）：{type(e).__name__}: {e}")
        finally:
            try:
                self._settle(rec)
            except Exception as e:
                rec["notes"].append(f"收尾清理异常：{e}")
            rec["seconds"] = round(time.time() - t0, 1)
            rec["war"] = self.war_note
        return rec

    # ------------------------------------------------------------ 主流程

    def _act_turn(self, rec: dict) -> None:
        s = self.s
        # 用 wait_until_clear 而不是 ensure_clear：刚答完弹窗时游戏会进入
        # 过旬演出，那是"它自己在跑"，要等，不能当遮挡处理。
        if not s.wait_until_clear(timeout=90, where="policy/start"):
            rec["notes"].append("战略面等不回来 → 这旬不下命令，只过旬")
            return

        top = self._topbar(rec)
        gold = top.get("gold")
        if isinstance(gold, int) and gold < MIN_GOLD:
            rec["notes"].append(f"資金 {gold} < {MIN_GOLD} → 攒钱，这旬不下内政命令")
            return

        cities = self._cities(rec)
        if not cities:
            rec["notes"].append("读不到城市列表 → 这旬只过旬")
            return

        tries = MAX_ATTEMPTS_PER_TURN
        self._abort_cities = False
        for idx, row in cities:
            if tries <= 0 or self._abort_cities:
                break
            tries -= 1                       # 按**尝试**扣，不按成功扣
            self._do_city(idx, row, rec)

        if self.want_war and not self.war_tried:
            self._probe_war(rec)

    def _settle(self, rec: dict) -> None:
        """收尾：**必须**回到干净战略面，否则下一旬 `_one_turn` 第 1 步就停住。

        但注意：等不回来**只记一笔，不抛异常**。下一旬还会再试。
        """
        if not _is_clear(self.s):
            ok = self.s.wait_until_clear(timeout=45, where="policy/settle")
            rec["notes"].append(f"收尾清理 -> {ok}")

    # ------------------------------------------------------------ 读局势

    def _topbar(self, rec: dict) -> dict:
        """读顶栏（年月/君主/信望/資金/兵糧）。顶栏是**免费的状态通道**。"""
        try:
            tb = ocr.read_topbar(self.hwnd)
        except Exception as e:
            rec["notes"].append(f"顶栏读取失败：{e}")
            return {}
        rec["topbar"] = {k: v for k, v in tb.items() if k != "raw"}
        rec["topbar_raw"] = tb.get("raw", "")[:60]
        return rec["topbar"]

    def _cities(self, rec: dict) -> list[tuple[int, dict]]:
        """右侧「移動列表」里的设施行。当前城排最前。返回 [(列表行号, 行数据)]。"""
        try:
            rows = cmdscreen.city_rows(self.hwnd)
        except Exception as e:
            rec["notes"].append(f"城市列表读取失败：{e}")
            return []
        if not rows:
            return []
        rec["probe"]["cities"] = [{"row": i, "name": r.get("name"),
                                   "current": bool(r.get("is_current"))}
                                  for i, r in enumerate(rows)]
        # ⛔ **只对"城"下内政命令，港口跳过。**
        # 港口（樂陵港/安德港/東阿…）能开的命令和都市不一样，
        # 拿都市的「down×序号进菜单」去点港，必然落到别的菜单上 ——
        # 实测：一旬 5 次全在港口附近空点，用户看着就是"点了半天啥也没点到"。
        pairs = [(i, r) for i, r in enumerate(rows)
                 if "港" not in (r.get("name") or "")]
        if len(pairs) != len(rows):
            rec["notes"].append(
                f"跳过 {len(rows) - len(pairs)} 个港口行（港不能做内政）")
        pairs.sort(key=lambda p: (not p[1].get("is_current"), p[0]))
        return pairs

    # ------------------------------------------------------------ 一座城

    def _do_city(self, idx: int, row: dict, rec: dict) -> bool:
        """给一座城下一条内政命令。返回是否成功下达。"""
        s = self.s
        name = row.get("name") or f"行{idx}"
        self._last_city_row = idx

        avail = self._probe_facility(idx, rec)
        if avail:
            cmd = next((c for c in FACILITY_PRIORITY if c in avail), None)
            if cmd is None:
                rec["notes"].append(f"{name}: 内政命令都不可用（可用={avail}）→ 跳过")
                return False
        else:
            # ⛔ **探测不可靠时不再盲试。**
            # 之前为了"别漏掉城市"改成盲试第一个，代价是：切城其实没成功时，
            # 照样按都市的键盘路径去点菜单，落到什么项全看运气 ——
            # 用户看到的就是"在一个港口上点了半天、什么都没点到"。
            # 宁可这旬不做这座城，也不许乱点。
            rec["notes"].append(
                f"{name}: 菜单探测不可靠（{avail!r}）→ 跳过这座城（不盲试）")
            return False

        op = CMD_TO_OP.get(cmd)
        if not op:
            rec["notes"].append(f"{name}: 「{cmd}」没有对应的 op → 跳过")
            return False

        try:
            res = s.act([{"op": op, "city_row": idx, "all_officers": True}])
        except Exception as e:
            rec["actions"].append({"city": name, "city_row": idx, "cmd": cmd,
                                   "op": op, "ok": False, "failed_at": "exception",
                                   "detail": f"{type(e).__name__}: {e}"})
            rec["notes"].append(f"{name}: {cmd} 抛异常（已吞）: {e}")
            return False

        one = (res or [{}])[0]
        ok = bool(one.get("ok"))
        rec["actions"].append({"city": name, "city_row": idx, "cmd": cmd, "op": op,
                               "ok": ok, "failed_at": one.get("failed_at"),
                               "detail": one.get("reason") or one.get("error")
                                         or one.get("note")})
        rec["notes"].append(f"{name}: {cmd} → {'✅成功' if ok else '❌失败'}"
                            f"（{one.get('failed_at') or '-'}）")
        if one.get("failed_at") == "select_city":
            # 切城没成功 = 后面所有步骤都在错的城上 —— 立刻收手，别再祸害下一行。
            self._abort_cities = True
            rec["notes"].append("⛔ 切城失败 → 这旬不再碰别的城（避免连续乱点）")
        return ok

    def _probe_facility(self, idx: int, rec: dict) -> list[str] | None:
        """读「設施」子菜单，返回**可用**命令名。探测失败返回 None。

        读完一定 `close_menu`：菜单开着会让下一旬第 1 步判"战略面不通畅"。
        """
        out = None
        try:
            r = cmdmenu.open_and_read(self.hwnd, 0, city_row=idx)
            if r.get("found"):
                out = list(r.get("sub_enabled") or [])
            rec["probe"].setdefault("menus", []).append(
                {"city_row": idx, "found": r.get("found"), "count": r.get("sub_count"),
                 "enabled": r.get("sub_enabled"), "disabled": r.get("sub_disabled")})
        except Exception as e:
            rec["probe"].setdefault("menus", []).append(
                {"city_row": idx, "error": f"{type(e).__name__}: {e}"})
        finally:
            try:
                cmdscreen.close_menu(self.hwnd)
            except Exception as e:
                rec["notes"].append(f"close_menu 异常：{e}")
        return out

    # ------------------------------------------------------------ 军事（只探测）

    def _probe_war(self, rec: dict) -> None:
        """军事探测：读「軍事」子菜单，出征可用就**进一次界面看看长什么样**。

        ⚠️ 这是项目未知领域（阵形/布阵可能是拖拽）。所以这一版**只探测、不下达**：
        进界面 → 截图 + OCR 标题 → Esc 退回。走不通就如实记一笔。
        **绝不让出征把长跑搭进去。**

        真正"派兵去打"要等 `explore/` 里出征链路的探索有结论（见计划卡点 C）。
        """
        self.war_tried = True
        note: dict = {"menu": "軍事"}
        self.war_note = note
        try:
            r = cmdmenu.open_and_read(self.hwnd, 1, city_row=self._last_city_row)
            note["probe"] = {"found": r.get("found"),
                             "enabled": r.get("sub_enabled"),
                             "disabled": r.get("sub_disabled")}
            en = list(r.get("sub_enabled") or [])
            if "出征" not in en:
                note["verdict"] = f"出征不可用（可用={en}）→ 本轮不试"
                rec["notes"].append(f"军事：出征不可用，可用={en}")
                return

            # 出征是子菜单第 0 项 —— 光标此时正停在第 0 项上，直接 enter 进界面
            self.s.drv.label = "policy/war-probe"
            winio.press("enter")
            time.sleep(1.2)
            shot = self.s.shot("war_probe")
            title, conf = ocr.read_dialog_title(self.hwnd)
            note["entered"] = {"title": title, "title_conf": round(conf, 2),
                               "shot": shot, "note": "只探测，没有下达任何命令"}
            rec["notes"].append(f"军事：进了出征界面（标题 OCR={title}）→ 只探测，未下达")
            rec["probe"]["war_screen"] = note["entered"]
        except Exception as e:
            note["verdict"] = f"探测异常：{type(e).__name__}: {e}"
            rec["notes"].append(f"军事探测异常（已吞）：{e}")
        finally:
            try:
                self.s.drv.key("esc", 1, 0.5)
                cmdscreen.close_menu(self.hwnd)
            except Exception:
                pass


def make(name: str, session, want_war: bool = True):
    """按名字造策略。目前只有一个。"""
    return BasicPolicy(session, want_war=want_war)
