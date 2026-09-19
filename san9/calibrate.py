"""路径标定器：你把某个操作手动做一遍，工具把点击序列录下来。

用法
----
    python -m san9.calibrate --path facility.patrol --seconds 40

在这 40 秒里，你在游戏窗口里正常操作一遍（比如：点都市 → 点設施 → 点巡察
→ 勾两个武将 → 点執行）。工具会：

  * 在游戏窗口客户区内的每一次左键点击都记一条 step（客户区坐标）
  * 每次点击前后各存一张截图到 shots/calib_<path>_NN_*.png
  * 结束（或按 F12）时把路径写进 config/paths.json

然后你**必须**核对截图，确认录下来的顺序和坐标确实是你要的那条路径 ——
标定错了比不标定更危险。核对完了用 `--verify` 查看。
"""
from __future__ import annotations

import argparse
import ctypes
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from san9 import driver, vision, winio  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT_SHOTS = os.path.join(ROOT, "shots")
VK_F12 = 0x7B


def cursor_client(hwnd: int) -> tuple[int, int] | None:
    pt = winio.wt.POINT()
    winio.user32.GetCursorPos(ctypes.byref(pt))
    l, t, w, h = winio.client_rect_screen(hwnd)
    if l <= pt.x < l + w and t <= pt.y < t + h:
        return (pt.x - l, pt.y - t)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", required=True, help="路径键，例如 facility.patrol")
    ap.add_argument("--seconds", type=float, default=40.0)
    ap.add_argument("--note", default="")
    args = ap.parse_args()

    win = winio.find_game("San9WPK.exe")
    if win is None:
        print("找不到游戏窗口，先启动游戏。", file=sys.stderr)
        return 1
    winio.focus(win.hwnd)
    os.makedirs(ROOT_SHOTS, exist_ok=True)

    print(f"[calibrate] 目标路径：{args.path}")
    print(f"[calibrate] 现在开始，请在游戏里把这一套操作做一遍（{args.seconds:.0f} 秒内）")
    print("[calibrate] 按 F12 可提前结束。")
    print("-" * 56)

    steps: list[dict] = []
    down_prev = False
    idx = 0
    book = vision.ScreenBook()
    t0 = time.time()

    while time.time() - t0 < args.seconds:
        if winio.user32.GetAsyncKeyState(VK_F12) & 0x8000:
            print("[calibrate] F12 结束")
            break
        down = bool(winio.user32.GetAsyncKeyState(0x01) & 0x8000)
        if down and not down_prev:
            xy = cursor_client(win.hwnd)
            if xy is not None:
                w, h, buf = winio.grab(win.hwnd, use_window_dc=True)
                scr, dist, _ = book.identify(buf, w, h)
                pre = winio.save_png(
                    os.path.join(ROOT_SHOTS, f"calib_{args.path}_{idx:02d}_pre.png"), w, h, buf)
                steps.append({
                    "kind": "click",
                    "xy": [xy[0], xy[1]],
                    "wait": 0.5,
                    "note": f"{scr or '?'}",
                })
                print(f"  #{idx:02d} 点击客户区 {xy}  界面={scr or '未识别'}"
                      f"  (距离 {dist:.3f})  -> {os.path.basename(pre)}")
                idx += 1
                time.sleep(0.6)
        down_prev = down
        time.sleep(0.02)

    if not steps:
        print("[calibrate] 没录到任何点击。是不是没点到游戏窗口里？")
        return 1

    # 末尾补一条"等到回到某界面"，强制加校验
    print("-" * 56)
    print(f"[calibrate] 录到 {len(steps)} 步。")
    tail = input("收尾界面名（回车跳过，建议填 strategy 之类）> ").strip()
    if tail:
        steps.append({"kind": "wait_screen", "screen": tail, "timeout": 15})

    print("即将写入的路径：")
    for i, s in enumerate(steps):
        print(f"  {i:02d} {s}")
    if input("确认写入 config/paths.json？(y/N) > ").strip().lower() != "y":
        print("已放弃。")
        return 1

    t = driver.PathTable()
    t.record(tuple(args.path.split(".")), steps)
    print(f"已写入 {t.path}")
    print("请务必回头看 shots/calib_*.png，确认每一步点的地方是对的。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
