"""长跑 harness —— 一条命令、无人介入、连续推进 N 旬，跑完出可复盘报告。

```bash
python -m san9.cli play --turns 60 --mode auto
```

验收标准是**"连续 N 旬不卡死"**，不是"打得好"。允许玩得很菜。

## 磁盘纪律

60 旬 × 每旬 2.7MB 截图会把磁盘塞爆，所以：

- **不每旬截图**。只在 stall / blocked / dialog 这些**出问题的地方**截。
- `stall` 图只保留最近 `STALL_KEEP` 张，更早的自动删（问题早就不复现了）。
- `.S9` 快照 205KB × 60 ≈ 12MB，全留 —— 出问题能回看。
- `play.jsonl` 每旬一行，只放摘要，不放整帧。

## 为什么每旬都要写日志

"它到底卡在哪一旬、卡之前发生了什么"是唯一能复盘的东西。
没有逐旬日志，跑完只剩一句"断在第 37 旬"，等于没跑。
"""

from __future__ import annotations

import collections
import glob
import json
import os
import time
from datetime import datetime

from . import journal, ocr, paths, policy as policy_mod
from .session import GameSession

STALL_KEEP = 12
"""`stall` 截图只留最近这么多张。"""

DIALOG_EVENTS = ("dialog_unknown", "dialog_answer", "dialog_filed")


def _go_score(s: GameSession):
    try:
        return s._go_score(s.view())
    except Exception:
        return None


def _trim_stalls(keep: int = STALL_KEEP) -> int:
    pat = os.path.join(paths.SHOTS, "*_stall.png")
    files = sorted(glob.glob(pat), key=os.path.getmtime)
    n = 0
    for f in files[:-keep] if len(files) > keep else []:
        try:
            os.remove(f)
            n += 1
        except OSError:
            pass
    return n


def _install_input_log(log_path: str) -> None:
    """把**每一次**点击/按键都记下来。

    为什么必须做：策略层和菜单链的点击走的是 `winio`，不经过 driver，
    所以 `input.jsonl` 里只有过旬那几下。用户问"你在東阿港点了半天在干嘛"时，
    我只能看到 play.jsonl 里的**意图**（cmd=巡察），看不到**实际点了哪里**；
    而意图和实际不一致（切城失败还继续）时，日志完全误导人。

    注意必须在**动手之前**记（和 driver 的规矩一致）：这样才能区分
    "我点了"和"游戏自己动了"。
    """
    from san9 import winio
    if getattr(winio, "_auto_logged", False):
        return
    import json as _json

    fh = open(log_path, "a", encoding="utf-8")

    def rec(kind, xy, extra=None):
        try:
            from san9.session import _now  # noqa
        except Exception:
            pass
        row = {"kind": kind, "xy": list(xy) if xy else None,
               "t": time.strftime("%H:%M:%S")}
        if extra:
            row.update(extra)
        fh.write(_json.dumps(row, ensure_ascii=False) + "\n")
        fh.flush()

    _c, _p, _m = winio.click_client, winio.press, winio.move_cursor_input

    def click_client(hwnd, x, y, *a, **kw):
        rec("click", (x, y))
        return _c(hwnd, x, y, *a, **kw)

    def press(key, *a, **kw):
        rec("key", None, {"key": key})
        return _p(key, *a, **kw)

    def move_cursor_input(x, y, *a, **kw):
        rec("move", (x, y))
        return _m(x, y, *a, **kw)

    winio.click_client = click_client
    winio.press = press
    winio.move_cursor_input = move_cursor_input
    winio._auto_logged = True


def run(turns: int = 60, mode: str = "auto", want_war: bool = True,
        run_id: str | None = None, policy_name: str = "basic",
        max_seconds: float | None = None, quiet: bool = False,
        want_journal: bool = True) -> dict:
    """跑 N 旬，返回结果（同时把 play.jsonl / report.md / report.json 落到 run 目录）。"""
    s = GameSession(mode=mode, run_id=run_id)
    if not s.attach():
        return {"ok": False, "error": "游戏没在运行（先 python -m san9.cli launch）"}

    pol = policy_mod.make(policy_name, s, want_war=want_war)
    _install_input_log(os.path.join(s.run_dir, "input_auto.jsonl"))
    log_path = os.path.join(s.run_dir, "play.jsonl")
    logf = open(log_path, "a", encoding="utf-8")

    rows: list[dict] = []
    t_start = time.time()
    advanced = 0
    stop: dict | None = None

    def say(msg: str) -> None:
        if not quiet:
            print(msg, flush=True)

    say(f"[play] run_id={s.run_id} mode={mode} turns={turns} "
        f"policy={pol.name} war={want_war}")

    try:
        for i in range(max(1, turns)):
            if max_seconds and time.time() - t_start > max_seconds:
                stop = {"reason": "到达时间上限", "at_turn": s.turn_index}
                break

            t0 = time.time()
            ev0 = len(s.events)
            # 本旬的播报（左下角面板）—— "这一旬到底发生了什么"的可读摘要。
            # 用户指出的信息源：过旬期间它会一行行刷出战报/事件。
            try:
                report = ocr.read_report_panel(s.hwnd)
            except Exception as e:
                report = [f"<读播报失败: {e}>"]
            try:
                rec = pol.act_turn()
            except Exception as e:                       # 双保险：策略层说好不抛的
                rec = {"notes": [f"策略层炸了（已兜住）：{type(e).__name__}: {e}"],
                       "actions": [], "topbar": {}, "probe": {}, "seconds": 0}

            try:
                r = s.end_turn(1, auto_answer=(mode == "auto"))
            except Exception as e:
                r = {"advanced": 0, "stopped_at": "exception",
                     "reason": f"{type(e).__name__}: {e}"}

            new_ev = s.events[ev0:]

            # 本旬的「進行記錄」（情報菜单第 9 项）—— 用户指出的最有价值情报源。
            # 它把本旬所有事件（战报/计略/登庸/耐久变化）列成大字清单，OCR 很稳。
            jr: dict = {"ok": False, "lines": [], "note": "未读", "seconds": 0}
            if want_journal and r.get("advanced") and not r.get("stopped_at"):
                tj = time.time()
                try:
                    jr = journal.read(s.hwnd)
                except Exception as e:                   # 读不到不许拖垮长跑
                    jr = {"ok": False, "lines": [],
                          "note": f"读了但炸了：{type(e).__name__}: {e}"}
                jr["seconds"] = round(time.time() - tj, 1)

            row = {
                "turn": s.turn_index,
                "i": i,
                "topbar": rec.get("topbar"),
                "actions": rec.get("actions") or [],
                "notes": rec.get("notes") or [],
                "advanced": r.get("advanced"),
                "stopped_at": r.get("stopped_at"),
                "reason": (r.get("question") or {}).get("why") or r.get("reason"),
                "go": _go_score(s),
                "event_types": [e.get("type") for e in new_ev],
                "dialogs": [e for e in new_ev if e.get("type") in DIALOG_EVENTS],
                "report": report,
                "journal": jr.get("lines") or [],
                "journal_ok": jr.get("ok"),
                "journal_note": jr.get("note"),
                "journal_seconds": jr.get("seconds"),
                "plan_seconds": rec.get("seconds"),
                "seconds": round(time.time() - t0, 1),
            }
            rows.append(row)
            logf.write(json.dumps(row, ensure_ascii=False) + "\n")
            logf.flush()

            done = sum(1 for a in row["actions"] if a.get("ok"))
            say(f"  旬 {s.turn_index:>3}  {row['seconds']:>5.1f}s  "
                f"命令 {done}/{len(row['actions'])}  "
                f"進行記錄 {len(row['journal'])} 条"
                f"{'' if row['journal_ok'] else '(!)'}  "
                f"go={row['go'] if row['go'] is None else round(row['go'], 2)}  "
                f"{'→ 停: ' + str(row['reason'])[:40] if row['stopped_at'] else ''}")

            if r.get("stopped_at"):
                stop = {"at_turn": s.turn_index, "stopped_at": r["stopped_at"],
                        "reason": row["reason"],
                        "shot": (r.get("question") or {}).get("shot")}
                break
            advanced += 1
    finally:
        logf.close()
        _trim_stalls()

    elapsed = time.time() - t_start
    result = {
        "ok": True, "run_id": s.run_id, "run_dir": s.run_dir,
        "mode": mode, "policy": pol.name, "want_war": want_war,
        "asked_turns": turns, "advanced": advanced,
        "reached_turn": s.turn_index, "elapsed": round(elapsed, 1),
        "sec_per_turn": round(elapsed / advanced, 1) if advanced else None,
        "stopped": stop, "rows": rows,
        "war_note": pol.war_note,
        "snapshots": sorted(os.listdir(paths.SNAPS)) if os.path.isdir(paths.SNAPS) else [],
        "stall_shots": sorted(glob.glob(os.path.join(paths.SHOTS, "*_stall.png"))),
    }
    md = _write_report(result)
    result["report"] = md
    say(f"\n[play] 完成：推进 {advanced} 旬，用时 {elapsed/60:.1f} 分钟"
        f"（{result['sec_per_turn']}s/旬）")
    if stop:
        say(f"[play] 停在第 {stop.get('at_turn')} 旬：{stop.get('reason')}")
    say(f"[play] 报告 -> {md}")
    return result


def _write_report(res: dict) -> str:
    rows = res["rows"]
    acts = [a for r in rows for a in r["actions"]]
    ok = [a for a in acts if a.get("ok")]
    fail_hist = collections.Counter(a.get("failed_at") or "未知" for a in acts
                                    if not a.get("ok"))
    ev_hist = collections.Counter(e for r in rows for e in r["event_types"])
    dialogs = [d for r in rows for d in r["dialogs"]]
    turns_with_action = sum(1 for r in rows if r["actions"])
    L: list[str] = []
    A = L.append

    A(f"# 长跑报告 —— run {res['run_id']}")
    A("")
    A(f"> 模式 `{res['mode']}`　策略 `{res['policy']}`　出征 `{res['want_war']}`　"
      f"生成于 {datetime.now():%Y-%m-%d %H:%M}")
    A("")
    A("## 结论")
    A("")
    goal = res["asked_turns"]
    if res["advanced"] >= goal:
        A(f"✅ **连续推进 {res['advanced']} 旬不卡死（目标 {goal} 旬）** —— 达成。")
    else:
        A(f"❌ **只推进了 {res['advanced']} 旬（目标 {goal} 旬）**，"
          f"停在第 {res['stopped'].get('at_turn') if res['stopped'] else '?'} 旬。")
        if res["stopped"]:
            A(f"　 停止原因：`{res['stopped'].get('stopped_at')}` —— "
              f"{res['stopped'].get('reason')}")
            if res["stopped"].get("shot"):
                A(f"　 截图：`{res['stopped']['shot']}`")
    A("")
    A("## 数字")
    A("")
    A("| 指标 | 值 |")
    A("|---|---|")
    A(f"| 推进旬数 | {res['advanced']} |")
    A(f"| 总耗时 | {res['elapsed']}s（{res['elapsed']/60:.1f} 分钟） |")
    A(f"| 每旬耗时 | {res['sec_per_turn']}s |")
    A(f"| 下过命令的旬 | {turns_with_action} / {len(rows)} |")
    A(f"| 命令成功 | {len(ok)} / {len(acts)}"
      f"{f'（{len(ok)/len(acts)*100:.0f}%）' if acts else ''} |")
    A(f"| 快照数 | {len(res['snapshots'])} |")
    A("")
    if fail_hist:
        A("### 命令失败原因")
        A("")
        A("| 原因 | 次数 |")
        A("|---|---|")
        for k, v in fail_hist.most_common():
            A(f"| `{k}` | {v} |")
        A("")
    if ev_hist:
        A("### 事件类型")
        A("")
        A("| 类型 | 次数 |")
        A("|---|---|")
        for k, v in ev_hist.most_common():
            A(f"| `{k}` | {v} |")
        A("")
    A("### 弹窗应答（认得出才答，认不出就停下 + 沉淀）")
    A("")
    if not dialogs:
        A("本次没有碰到需要拍板的弹窗。")
    else:
        A("| 旬 | 类型 | 规则 | 选项 | 验证 | OCR 片段 |")
        A("|---|---|---|---|---|---|")
        for d in dialogs:
            A(f"| - | `{d.get('type')}` | {d.get('rule') or d.get('matched') or '-'} | "
              f"{d.get('choice', '-')} | {d.get('verified', '-')} | "
              f"{(d.get('ocr') or '')[:26]} |")
    A("")
    if res.get("war_note"):
        A("### 军事（出征）")
        A("")
        A("```json")
        A(json.dumps(res["war_note"], ensure_ascii=False, indent=1))
        A("```")
        A("")
    A("## 卡点")
    A("")
    A("**解掉的**：见本文件同级目录的 `play.jsonl`，以及 `san9ai/explore/` 里的对应发现。")
    A("**留下的**：")
    if res["stall_shots"]:
        A(f"- `stall` 截图 {len(res['stall_shots'])} 张（只保留最近 {STALL_KEEP} 张）：")
        for p in res["stall_shots"]:
            A(f"  - `{p}`")
    else:
        A("- 没有 stall（一次都没卡住）")
    A("")
    A("**绕开的东西与条件**：见每旬 `notes` 里的 `war_skip` / 菜单探测失败记录。")
    A("")
    A("## 逐旬记录")
    A("")
    A(f"完整数据在 `{os.path.join(res['run_dir'], 'play.jsonl')}`（每旬一行 JSON）。")
    A("下面只列**有异常或有动作**的旬：")
    A("")
    A("| 旬 | 秒 | 命令 | 备注 |")
    A("|---|---|---|---|")
    for r in rows:
        if not r["actions"] and not r["stopped_at"] and not r["notes"]:
            continue
        a = "；".join(f"{x.get('city')}/{x.get('cmd')}{'✅' if x.get('ok') else '❌'}"
                      for x in r["actions"]) or "—"
        note = "；".join(n for n in r["notes"] if "攒钱" not in n)[:70]
        A(f"| {r['turn']} | {r['seconds']} | {a} | {note}"
          f"{' **停:' + str(r['reason'])[:30] + '**' if r['stopped_at'] else ''} |")
    A("")

    path = os.path.join(res["run_dir"], "report.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    with open(os.path.join(res["run_dir"], "report.json"), "w",
              encoding="utf-8") as f:
        json.dump({k: v for k, v in res.items() if k != "rows"}, f,
                  ensure_ascii=False, indent=1)
    return path
