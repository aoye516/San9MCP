"""命令行入口：任何 agent（Claude Code / Codex / WorkBuddy）都能用。

约定：**stdout 永远是 JSON**，人看的提示走 stderr。
这样 agent 直接 `bash -c "python -m san9.cli obs"` 然后 parse 就行。

    python -m san9.cli status
    python -m san9.cli launch
    python -m san9.cli load --slot 1
    python -m san9.cli obs [--scope brief|full] [--shot]
    python -m san9.cli act --json '[{"op":"city.patrol",...}]'
    python -m san9.cli end-turn -n 1
    python -m san9.cli docs                      # 动作目录（塞进 prompt）
    python -m san9.cli screens                   # 已识别的界面
    python -m san9.cli learn-screen --name strategy
    python -m san9.cli shot
    python -m san9.cli snapshot / rollback
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from san9 import actions as A  # noqa: E402
from san9 import boot, driver, paths, savefile, vision, winio  # noqa: E402


def out(obj) -> None:
    json.dump(obj, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    sys.stdout.flush()


def err(msg: str) -> None:
    print(msg, file=sys.stderr)


def _session(dry: bool = False):
    from san9.session import GameSession

    s = GameSession(dry_run=dry)
    s.attach()
    return s


def cmd_status(a) -> int:
    running = winio.find_all_game_processes("San9")
    win = winio.find_game("San9WPK.exe")
    book = vision.ScreenBook()
    table = driver.PathTable()
    info = {
        "game_processes": [{"pid": p, "name": n} for p, n in running],
        "window": None,
        "screens_learned": sorted(book.entries),
        "paths_calibrated": sorted(table.data),
        "saves": {},
    }
    if win:
        l, t, w, h = winio.client_rect_screen(win.hwnd)
        info["window"] = {
            "hwnd": f"0x{win.hwnd:08X}", "title": win.title, "class": win.cls,
            "client_origin": [l, t], "client_size": [w, h],
            "foreground": winio.is_foreground(win.hwnd),
            "standard_1024x768": (w, h) == (1024, 768),
        }
        name, dist, ranked = None, 1.0, []
        try:
            gw, gh, buf = winio.grab(win.hwnd, use_window_dc=False)
            name, dist, ranked = book.identify(buf, gw, gh)
            info["current_screen"] = name
            info["screen_distance"] = round(dist, 4)
            info["screen_ranking"] = [{"name": n, "dist": round(d, 4)} for n, d in ranked]
        except Exception as e:
            info["screen_error"] = str(e)
    try:
        for k, v in savefile.list_saves().items():
            sf = savefile.SaveFile(v)
            info["saves"][k] = {"bytes": len(sf.data), "chunks": len(sf.chunks)}
        info["save_dir"] = savefile.find_save_dir()
    except Exception as e:
        info["save_error"] = str(e)
    out(info)
    return 0


def cmd_launch(a) -> int:
    s = _session()
    ok = s.launch()
    out({"ok": ok, "hwnd": f"0x{s.hwnd:08X}" if s.hwnd else None,
         "note": "走启动器 GAME 按钮，直接跑 San9WPK.exe 会立刻退出"})
    return 0 if ok else 1


def cmd_screens(a) -> int:
    book = vision.ScreenBook()
    out({"learned": {k: {"note": v.get("note", ""), "region": v.get("region")}
                     for k, v in book.entries.items()}})
    return 0


def cmd_learn_screen(a) -> int:
    win = winio.find_game("San9WPK.exe")
    if win is None:
        err("找不到游戏窗口")
        return 1
    winio.focus(win.hwnd)
    w, h, buf = winio.grab(win.hwnd, use_window_dc=False)
    book = vision.ScreenBook()
    book.learn(a.name, buf, w, h, note=a.note or "", overwrite=a.overwrite)
    book.save()
    paths.ensure()
    p = winio.save_png(os.path.join(paths.SHOTS, f"screen_{a.name}.png"), w, h, buf)
    out({"ok": True, "learned": a.name, "sample": p,
         "total_screens": len(book.entries)})
    return 0


def cmd_shot(a) -> int:
    win = winio.find_game("San9WPK.exe")
    if win is None:
        err("找不到游戏窗口")
        return 1
    if a.focus:
        winio.focus(win.hwnd)
    w, h, buf = winio.grab(win.hwnd, use_window_dc=not a.screen_region)
    paths.ensure()
    name = a.tag or "shot"
    p = winio.save_png(os.path.join(paths.SHOTS, f"{name}.png"), w, h, buf)
    book = vision.ScreenBook()
    sname, dist, ranked = book.identify(buf, w, h)
    out({"ok": True, "path": p, "size": [w, h],
         "screen": sname, "distance": round(dist, 4),
         "ranking": [{"name": n, "dist": round(d, 4)} for n, d in ranked]})
    return 0


def cmd_obs(a) -> int:
    s = _session(dry=a.dry_run)
    if not s.hwnd:
        out({"error": "游戏没在运行", "hint": "先跑 python -m san9.cli launch"})
        return 1
    out(s.observe(a.scope, a.shot))
    return 0


def cmd_act(a) -> int:
    try:
        acts = json.loads(a.json)
    except json.JSONDecodeError as e:
        out({"error": f"--json 不是合法 JSON: {e}"})
        return 1
    if isinstance(acts, dict):
        acts = [acts]
    s = _session(dry=a.dry_run)
    if not s.hwnd:
        out({"error": "游戏没在运行"})
        return 1
    out(s.act(acts))
    return 0


def cmd_end_turn(a) -> int:
    s = _session(dry=a.dry_run)
    if not s.hwnd:
        out({"error": "游戏没在运行"})
        return 1
    out(s.end_turn(a.n))
    return 0


def cmd_play(a) -> int:
    """长跑：无人介入连续推进 N 旬，每旬按策略下命令，跑完出报告。

    这条命令就是本轮的验收标准 —— **连续 N 旬不卡死**（不是打得好）。
    """
    from san9 import play as play_mod

    res = play_mod.run(turns=a.turns, mode=a.mode, want_war=not a.no_war,
                       run_id=a.run_id, policy_name=a.policy,
                       max_seconds=a.max_seconds,
                       want_journal=not a.no_journal)
    out({
        "ok": res.get("ok"),
        "error": res.get("error"),
        "run_id": res.get("run_id"),
        "advanced": res.get("advanced"),
        "asked_turns": res.get("asked_turns"),
        "reached_turn": res.get("reached_turn"),
        "sec_per_turn": res.get("sec_per_turn"),
        "stopped": res.get("stopped"),
        "report": res.get("report"),
        "war_note": res.get("war_note"),
    })
    return 0 if res.get("ok") else 1


def cmd_atom(a) -> int:
    """原子能力：**一步一个动作**，每步存图，供 agent 判断下一手。

    用法（一次只调一个）：
        python -m san9.cli atom --op state
        python -m san9.cli atom --op select --row 1
        python -m san9.cli atom --op menu   --row 1
        python -m san9.cli atom --op issue  --row 1 --cmd-index 0
        python -m san9.cli atom --op turn
        python -m san9.cli atom --op journal
    """
    import time as _t

    from san9 import atoms, winio

    w = winio.find_game("San9WPK.exe")
    if not w:
        out({"ok": False, "error": "游戏没在运行"})
        return 1
    winio.focus(w.hwnd)
    _t.sleep(0.4)

    op = a.op
    if op == "state":
        r = atoms.state(w.hwnd)
    elif op == "select":
        r = atoms.select(w.hwnd, a.facility)
    elif op == "menu":
        r = atoms.menu(w.hwnd, a.facility)
    elif op == "issue":
        cmds = atoms.FACILITY_CMDS
        if not (0 <= a.cmd_index < len(cmds)):
            out({"ok": False, "error": f"--cmd-index 越界（0..{len(cmds)-1}）",
                 "list": list(enumerate(cmds))})
            return 1
        r = atoms.issue(w.hwnd, a.facility, cmds[a.cmd_index])
        r["cmd_index_list"] = list(enumerate(cmds))
    elif op == "turn":
        r = atoms.end_turn(w.hwnd)
    elif op == "journal":
        r = atoms.read_journal(w.hwnd)
    else:
        r = {"ok": False, "error": f"未知 op {op}"}
    out(r)
    return 0 if r.get("ok") else 1


def cmd_docs(a) -> int:
    out(A.catalogue())
    return 0


def cmd_snapshot(a) -> int:
    s = _session(dry=a.dry_run)
    out({"ok": True, "path": s.snapshot("cli")})
    return 0


def cmd_rollback(a) -> int:
    s = _session(dry=a.dry_run)
    out(s.rollback(a.to))
    return 0


def cmd_dryrun(a) -> int:
    """不碰游戏，只校验动作 JSON 是否合法。写 benchmark 时很好用。"""
    try:
        acts = json.loads(a.json)
    except json.JSONDecodeError as e:
        out({"error": str(e)})
        return 1
    if isinstance(acts, dict):
        acts = [acts]
    res = []
    for x in acts:
        try:
            res.append({"ok": True, "normalized": A.validate(x)})
        except A.ActionError as e:
            res.append({"ok": False, "error": str(e)})
    out(res)
    return 0 if all(r["ok"] for r in res) else 1


def main() -> int:
    ap = argparse.ArgumentParser(prog="san9", description="三国志9 的 agent 接口")
    ap.add_argument("--dry-run", action="store_true", help="不真的操作游戏")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("launch").set_defaults(fn=cmd_launch)
    sub.add_parser("screens").set_defaults(fn=cmd_screens)

    p = sub.add_parser("learn-screen")
    p.add_argument("--name", required=True)
    p.add_argument("--note", default="")
    p.add_argument("--overwrite", action="store_true")
    p.set_defaults(fn=cmd_learn_screen)

    p = sub.add_parser("shot")
    p.add_argument("--tag", default="")
    p.add_argument("--focus", action="store_true")
    p.add_argument("--screen-region", action="store_true",
                   help="按屏幕区域抓（窗口被遮挡时用）")
    p.set_defaults(fn=cmd_shot)

    p = sub.add_parser("obs")
    p.add_argument("--scope", choices=["brief", "full"], default="brief")
    p.add_argument("--shot", action="store_true")
    p.set_defaults(fn=cmd_obs)

    p = sub.add_parser("act")
    p.add_argument("--json", required=True)
    p.set_defaults(fn=cmd_act)

    p = sub.add_parser("end-turn")
    p.add_argument("-n", type=int, default=1)
    p.set_defaults(fn=cmd_end_turn)

    # 长跑：无人介入连续推进 N 旬，每旬按策略下一两条命令，跑完出报告。
    # 验收标准是"连续 N 旬不卡死"，不是"打得好"。
    p = sub.add_parser("play")
    p.add_argument("--turns", type=int, default=60, help="目标旬数")
    p.add_argument("--mode", default="auto", choices=["auto", "fair"],
                   help="auto=认得出的弹窗自己答；fair=一律停下等人")
    p.add_argument("--no-war", action="store_true", dest="no_war",
                   help="不做军事探测（纯内政，最稳）")
    p.add_argument("--policy", default="basic", help="策略名")
    p.add_argument("--run-id", default=None, dest="run_id")
    p.add_argument("--max-seconds", type=float, default=None, dest="max_seconds",
                   help="时间上限（秒），到了就收工")
    p.add_argument("--no-journal", action="store_true", dest="no_journal",
                   help="每旬不读「情報→進行記錄」（省 ~23s/旬）")
    p.set_defaults(fn=cmd_play)

    sub.add_parser("docs").set_defaults(fn=cmd_docs)

    # ---- 原子能力：一步一个动作，每步存图。给 agent 手工玩用。
    p = sub.add_parser("atom", help="原子能力（一步一调）")
    p.add_argument("--op", required=True,
                   choices=["state", "select", "menu", "issue", "turn", "journal"])
    p.add_argument("--facility", type=int, default=0,
                   help="设施序号（**上一次 atom --op state 报出来的 i**）。"
                        "动作时会按名字现查行号 —— 行号会漂，不能直接用。")
    p.add_argument("--cmd-index", type=int, default=0, dest="cmd_index",
                   help="設施子菜单项序号 0..7（巡察=0 商業=1 開墾=2 修築=3 "
                        "徵兵=4 訓練=5 買進=6 賣出=7）。中文不能走命令行。")
    p.set_defaults(fn=cmd_atom)
    sub.add_parser("snapshot").set_defaults(fn=cmd_snapshot)

    p = sub.add_parser("rollback")
    p.add_argument("--to", type=int, default=None, help="目标旬号")
    p.set_defaults(fn=cmd_rollback)

    p = sub.add_parser("dry-run-json")
    p.add_argument("--json", required=True)
    p.set_defaults(fn=cmd_dryrun)

    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
