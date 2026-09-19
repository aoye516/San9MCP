"""环境体检：确认"AI 能不能看见并操作三国志9"。

跑法（用受管 Python）：
    C:/Users/luj05/.workbuddy/binaries/python/envs/default/Scripts/python.exe san9/probe.py

它会依次检查：
  1. 游戏进程 / 窗口在不在
  2. 窗口位置与客户区尺寸（自动化坐标的基准）
  3. 截图能不能抓到画面（抓不到就没法做视觉识别）
  4. 注册表 FullScreen 是否已关（必须窗口化）
  5. 存档能不能解析（世界状态的来源）
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from san9 import savefile, winio  # noqa: E402

EXE = "San9WPK.exe"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOTS = os.path.join(ROOT, "shots")


def check_registry() -> None:
    print("\n[4] 显示设置 (HKCU\\Software\\KoeiTecmo\\35th\\San9WPK\\Configs)")
    try:
        import winreg

        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\KoeiTecmo\35th\San9WPK\Configs")
        vals = {}
        i = 0
        while True:
            try:
                n, v, _ = winreg.EnumValue(k, i)
                vals[n] = v
                i += 1
            except OSError:
                break
        fs = vals.get("FullScreen")
        print(f"    FullScreen = {fs}  -> " + ("窗口模式 ✓" if fs == 0 else "全屏 ✗（建议改成 0）"))
        print(f"    GameSpeed  = {vals.get('GameSpeed')}")
        print(f"    PlayMovie  = {vals.get('PlayMovie')}")
        print(f"    AppPosition= ({vals.get('AppPositionX')}, {vals.get('AppPositionY')})")
    except Exception as e:
        print("    读注册表失败:", e)


def check_save() -> None:
    print("\n[5] 存档解析")
    try:
        saves = savefile.list_saves()
        print("    存档目录:", savefile.find_save_dir())
        for name, path in saves.items():
            s = savefile.SaveFile(path)
            print("   ", name, f"{len(s.data)} 字节, {len(s.chunks)} 个块, "
                  f"form={s.form_type.decode('latin1')}, 尾巴={getattr(s,'trailing',0)}B")
        main = saves.get("D_Sav000.S9") or list(saves.values())[0]
        s = savefile.SaveFile(main)
        print("\n   —— 块表 ——")
        print("   " + s.dump_map().replace("\n", "\n   "))
    except Exception as e:
        print("    失败:", e)


def main() -> int:
    print("=" * 64)
    print("三国志9 AI 环境体检")
    print("=" * 64)

    print("\n[1] 进程")
    procs = winio.find_all_game_processes("San9")
    if procs:
        for pid, name in procs:
            print(f"    运行中: {name} (pid={pid})")
    else:
        print(f"    没找到 {EXE}，游戏尚未启动")

    print("\n[2] 窗口")
    win = winio.find_game(EXE)
    if win is None:
        print("    没有可见的游戏窗口。")
        print("    （先启动游戏再跑一次本脚本，才能验证截图与输入）")
    else:
        l, t, w, h = winio.client_rect_screen(win.hwnd)
        print(f"    hwnd      = 0x{win.hwnd:08X}")
        print(f"    title     = {win.title!r}")
        print(f"    class     = {win.cls!r}")
        print(f"    客户区    = 屏幕({l},{t})  尺寸 {w}x{h}")
        print(f"    前台      = {winio.is_foreground(win.hwnd)}")

        os.makedirs(SHOTS, exist_ok=True)
        winio.focus(win.hwnd)
        time.sleep(0.4)
        try:
            gw, gh, buf = winio.grab(win.hwnd)
            nonblack = sum(1 for i in range(0, len(buf), 4 * 97) if buf[i] or buf[i + 1] or buf[i + 2])
            total = len(range(0, len(buf), 4 * 97))
            ratio = nonblack / max(total, 1)
            path = winio.save_png(os.path.join(SHOTS, "probe_game.png"), gw, gh, buf)
            print(f"    截图      = {path}  ({gw}x{gh}, 非黑像素 {ratio:.1%})")
            if ratio < 0.02:
                print("    !! 整屏全黑：DirectX 独占模式下抓不到画面，必须先窗口化")
            else:
                print("    截图有效 ✓")
        except Exception as e:
            print("    截图失败:", e)

    check_registry()
    check_save()

    print("\n" + "=" * 64)
    print("结论：", "环境可用，可以进行下一步" if win else "需要先启动游戏")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
