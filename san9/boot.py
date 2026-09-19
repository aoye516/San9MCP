"""启动游戏、把窗口摆到固定位置、连拍几张截图。

窗口位置和尺寸必须固定 —— 所有点击坐标都建立在"客户区 1024x768、窗口在 (0,0)"
这个假设上。窗口一挪，所有路径表就全废了。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from san9 import paths, winio  # noqa: E402

GAME_DIR = r"C:\Program Files (x86)\Steam\steamapps\common\San9WPK"
EXE = os.path.join(GAME_DIR, "San9WPK.exe")
LAUNCHER = os.path.join(GAME_DIR, "San9WPK_Launcher.exe")
"""⛔ **必须走启动器**：直接跑 San9WPK.exe 会立刻退出（实测等不到窗口）。
正确链路 = 起 San9WPK_Launcher.exe → 给启动器里文字为 `GAME` 的标准按钮发 `BM_CLICK`
→ 主程序窗口才出现。原来的 `launch()` 少了前两步，所以永远起不来。"""
ROOT = paths.PROJECT
SHOTS = paths.SHOTS

# 我们约定的标准窗口摆放：客户区左上角固定在屏幕左上角附近
STD_ORIGIN = (0, 40)


def exe_running() -> list:
    import psutil

    out = []
    for p in psutil.process_iter(["name", "pid"]):
        try:
            n = (p.info["name"] or "").lower()
        except Exception:
            continue
        if n.startswith("san9") and "launcher" not in n:
            out.append((p.info["pid"], p.info["name"]))
    return out


def start_launcher(wait: float = 25.0) -> int | None:
    """启动**启动器**并返回它的窗口句柄。"""
    import psutil

    running = False
    for p in psutil.process_iter(["name"]):
        n = (p.info.get("name") or "").lower()
        if "launcher" in n and "san9" in n:
            running = True
            break
    if running:
        print("[boot] 启动器已在运行")
    else:
        print("[boot] 启动启动器", LAUNCHER)
        subprocess.Popen([LAUNCHER], cwd=GAME_DIR)
    t0 = time.time()
    while time.time() - t0 < wait:
        for w in winio.list_windows():
            if "LAUNCHER" in (w.cls or "").upper():
                time.sleep(1.0)
                return w.hwnd
        time.sleep(0.5)
    print("[boot] 等不到启动器窗口")
    return None


def click_launcher_game() -> bool:
    """给启动器里文字为 `GAME` 的标准按钮发 BM_CLICK（比点坐标稳）。"""
    import ctypes
    import ctypes.wintypes as wt

    u = winio.user32
    lp = None
    for w in winio.list_windows():
        if "LAUNCHER" in (w.cls or "").upper():
            lp = w.hwnd
            break
    if lp is None:
        print("[boot] 找不到启动器窗口")
        return False
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(h, _):
        n = u.GetWindowTextLengthW(h)
        t = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(h, t, n + 1)
        if (t.value or "").upper() == "GAME":
            found.append(h)
        return True

    u.EnumChildWindows(lp, cb, 0)
    if not found:
        print("[boot] 启动器里没找到 GAME 按钮")
        return False
    u.SendMessageW(found[0], 0x00F5, 0, 0)      # BM_CLICK
    print("[boot] 已点 GAME")
    return True


def wait_game(wait: float = 90.0) -> int | None:
    """等主程序窗口出现；出现后把客户区摆到标准位置。"""
    t0 = time.time()
    while time.time() - t0 < wait:
        win = winio.find_game("San9WPK.exe")
        if win and win.size[0] > 200:
            time.sleep(2.0)
            place(win.hwnd)
            return win.hwnd
        time.sleep(0.5)
    print("[boot] 等不到游戏窗口")
    return None


def launch(wait: float = 90.0) -> int | None:
    """完整启动链：启动器 → GAME 按钮 → 主程序窗口。返回 hwnd 或 None。"""
    if exe_running():
        print("[boot] 游戏已经在运行")
        win = winio.find_game("San9WPK.exe")
        return win.hwnd if win else None
    if start_launcher() is None:
        return None
    if not click_launcher_game():
        return None
    return wait_game(wait)


def place(hwnd: int) -> None:
    """把客户区左上角摆到 STD_ORIGIN。"""
    r = winio.wt.RECT()
    winio.user32.GetWindowRect(hwnd, winio.ctypes.byref(r))
    cr = winio.wt.RECT()
    winio.user32.GetClientRect(hwnd, winio.ctypes.byref(cr))
    bx = (r.right - r.left) - (cr.right - cr.left)
    by = (r.bottom - r.top) - (cr.bottom - cr.top)
    winio.user32.SetWindowPos(
        hwnd, 0,
        STD_ORIGIN[0] - bx // 2, STD_ORIGIN[1] - by // 2,
        0, 0, 0x0001 | 0x0004 | 0x0010,  # NOSIZE | NOZORDER | NOACTIVATE
    )
    time.sleep(0.4)
    print("[boot] 客户区:", winio.client_rect_screen(hwnd))


def burst(hwnd: int, n: int = 3, gap: float = 2.0, tag: str = "boot") -> list[str]:
    paths.ensure()
    paths = []
    for i in range(n):
        w, h, buf = winio.grab(hwnd)
        p = os.path.join(SHOTS, f"{tag}_{i:02d}.png")
        winio.save_png(p, w, h, buf)
        paths.append(p)
        print(f"[boot] 截图 {i}: {p} ({w}x{h})")
        if i < n - 1:
            time.sleep(gap)
    return paths


if __name__ == "__main__":
    hwnd = launch()
    if hwnd:
        print("[boot] 前台:", winio.is_foreground(hwnd))
        burst(hwnd, 3, 2.0, "boot")
