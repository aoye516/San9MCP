# -*- coding: utf-8 -*-
"""A 组 · 会话与环境。"""
from __future__ import annotations

from san9 import boot, winio

from san9mcp import registry, runtime


@registry.tool(
    "san9_status", group="A", tier="play",
    summary="环境体检：游戏进程在不在 · 窗口对不对（class / 客户区尺寸 / 前台）· "
            "现在在哪个界面 · 哪个存档目录可用。**开局先调这个。**")
def san9_status():
    procs = winio.find_all_game_processes("San9")
    out = {"game_processes": [{"pid": p, "name": n} for p, n in procs]}
    w = winio.find_game(runtime.EXE)
    if w is None:
        return runtime.fail(
            "游戏没在运行（找不到 %s 窗口）" % runtime.EXE,
            hint="调 san9_launch",
            game_processes=out["game_processes"])
    l, t, cw, ch = winio.client_rect_screen(w.hwnd)
    fg = bool(winio.is_foreground(w.hwnd))
    out["window"] = {
        "title": w.title, "class": w.cls, "hwnd": "0x%08X" % w.hwnd,
        "client_origin": [l, t], "client_size": [cw, ch],
        "standard_1280x960": (cw, ch) == (1280, 960),
        "foreground": fg,
    }
    if fg:
        # 前台才读屏 —— 不在前台拍的图是别人的窗口，读数没有意义
        try:
            p = runtime.probe(w)
            out["face"] = {k: p[k] for k in ("on_strategy_face", "clear", "go_score")}
        except Exception as e:
            out["face_error"] = "%s: %s" % (type(e).__name__, e)
    else:
        out["note"] = "游戏不在前台，未读屏（任何读屏工具会自动切前台）"
    return runtime.ok(**out)


@registry.tool(
    "san9_focus", group="A", tier="both",
    summary="把游戏窗口切到前台。**截图的前提** —— 截图拍的是屏幕像素，"
            "游戏不在最前面就会拍到别的窗口，读数全部不可信。")
def san9_focus():
    w = runtime.window()
    fg = runtime.ensure_foreground(w)
    if not fg:
        return runtime.fail(
            "切前台失败（可能被别的窗口抢焦点，或游戏最小化了）",
            hint="手动点一下游戏窗口再试")
    return runtime.ok(focused=True,
                      client_origin=list(winio.client_rect_screen(w.hwnd)[:2]))


@registry.tool(
    "san9_launch", group="A", tier="play",
    summary="启动游戏。**必须走启动器**（San9WPK_Launcher.exe 里的 GAME 按钮）——"
            "直接跑 San9WPK.exe 会立刻退出。启动较慢，可能几十秒。")
def san9_launch():
    w = winio.find_game(runtime.EXE)
    if w is not None:
        return runtime.ok(already_running=True, hwnd="0x%08X" % w.hwnd)
    hwnd = boot.launch()
    if not hwnd:
        return runtime.fail("启动失败", hint="检查游戏是否已装在 " + boot.GAME_DIR)
    w = winio.find_game(runtime.EXE)
    return runtime.ok(hwnd="0x%08X" % hwnd if hwnd else None,
                      title=w.title if w else None,
                      note="启动器走的是 GAME 按钮；直接跑 exe 会秒退")
