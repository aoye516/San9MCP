"""人机配合标定：我说点哪，你点哪，我记下来。

为什么这样做
------------
我（程序）目测坐标会偏，而**你点的是真实位置** —— 你点哪儿，哪儿就是标准答案。
所以分工是：我出题（该点哪些按钮），你执行，我记录。

配合方式
--------
1. 我跑这个脚本，屏幕上浮出一个**置顶提示框**（放在游戏窗口右侧，不遮挡游戏）
2. 提示框显示"第 1/5 项：点绿色的【進行】按钮"
3. 你在游戏里点它 —— 提示框自动跳到下一项
4. 全程自动记录：点击位置、点击前/后的截图、按钮图像模板
5. F11 跳过当前项，F12 提前结束

每轮最多 5 项，错了随时可以只重录某一轮。

用法
----
    python -m san9.pick --name in_game_1 --items scripts/pick_in_game_1.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from san9 import paths, vision, winio  # noqa: E402
from san9.explore import montage  # noqa: E402
from san9.learn import ClickCatcher, grab_rgb, save_jpg  # noqa: E402

VK_F11, VK_F12 = 0x7A, 0x7B
TPL_W, TPL_H = 110, 30  # 按钮模板尺寸（比单行文字大一点，便于匹配）


class Picker:
    def __init__(self, name: str, items: list[dict], book: vision.ScreenBook):
        self.name = name
        self.items = items
        self.idx = 0
        self.book = book
        self.dir = os.path.join(paths.DATA, "pick", name)
        os.makedirs(self.dir, exist_ok=True)
        self.win = winio.find_game("San9WPK.exe")
        if self.win is None:
            raise SystemExit("找不到游戏窗口，先启动游戏")
        self.queue: list[tuple[int, int]] = []
        self.done = False
        self.last_shot: np.ndarray | None = None
        self.results: list[dict] = []
        self._stop = False

    # ------------------------------------------------------------ 背景

    def _bg_capture(self) -> None:
        """常驻抓帧，保证点击瞬间手边就有一张"点击前"的图。"""
        while not self._stop:
            try:
                self.last_shot = grab_rgb(self.win.hwnd, True)
            except Exception:
                pass
            time.sleep(0.12)

    def _on_click(self, sx: int, sy: int) -> None:
        l, t, w, h = winio.client_rect_screen(self.win.hwnd)
        if not (l <= sx < l + w and t <= sy < t + h):
            return
        self.queue.append(winio.to_client(sx, sy, self.win.hwnd))

    # ------------------------------------------------------------ 记录

    def _record(self, item: dict, xy: tuple[int, int]) -> None:
        import cv2

        before = self.last_shot if self.last_shot is not None else grab_rgb(self.win.hwnd, True)
        time.sleep(1.6)
        after = grab_rgb(self.win.hwnd, True)

        x, y = xy
        h, w = before.shape[:2]
        x0 = max(0, min(w - TPL_W, x - TPL_W // 2))
        y0 = max(0, min(h - TPL_H, y - TPL_H // 2))
        tpl = before[y0:y0 + TPL_H, x0:x0 + TPL_W]
        n = len(self.results)
        tpl_name = f"tpl_{item['id']}.jpg"
        save_jpg(os.path.join(self.dir, tpl_name), tpl, 95)
        save_jpg(os.path.join(self.dir, f"{n:02d}_{item['id']}_before.jpg"), before, 82)
        save_jpg(os.path.join(self.dir, f"{n:02d}_{item['id']}_after.jpg"), after, 82)

        # 用界面指纹判断这一步有没有真的把画面推下去
        ga = cv2.cvtColor(before[:, :, ::-1], cv2.COLOR_BGR2BGRA).tobytes()
        gb = cv2.cvtColor(after[:, :, ::-1], cv2.COLOR_BGR2BGRA).tobytes()
        moved = vision.distance(vision.signature(ga, w, h), vision.signature(gb, w, h))
        before_name, _, _ = self.book.identify(ga, w, h)

        rec = {
            "order": n, "id": item["id"], "label": item.get("label", ""),
            "xy": [x, y], "template": os.path.relpath(
                os.path.join(self.dir, tpl_name), paths.DATA).replace("\\", "/"),
            "tpl_box": [int(x0), int(y0), TPL_W, TPL_H],
            "screen_before": before_name,
            "screen_changed": moved > 0.004,
            "change_amount": round(moved, 4),
            "shot_before": paths.store(os.path.join(self.dir, f"{n:02d}_{item['id']}_before.jpg")),
            "shot_after": paths.store(os.path.join(self.dir, f"{n:02d}_{item['id']}_after.jpg")),
        }
        self.results.append(rec)
        flag = "✓" if rec["screen_changed"] else "⚠ 画面没变化"
        print(f"  [{n+1}] {item['id']:<16} 记录 ({x},{y})  {flag}")

    # ------------------------------------------------------------ 主循环

    def run(self) -> None:
        from .overlay import Overlay

        winio.focus(self.win.hwnd)
        threading.Thread(target=self._bg_capture, daemon=True).start()
        catcher = ClickCatcher(self.win.hwnd, self._on_click)
        catcher.start()

        # 提示框摆在游戏窗口右边，不遮挡游戏；WS_EX_NOACTIVATE 保证不抢焦点
        wl, wt_, ww, wh = winio.client_rect_screen(self.win.hwnd)
        sw = winio.user32.GetSystemMetrics(0)
        ox = int(min(wl + ww + 24, max(0, sw - 660)))
        ov = Overlay(ox, wt_ + 60, 620, 320)

        def refresh() -> None:
            it = self.items[self.idx]
            ov.set(f"第 {self.idx + 1}/{len(self.items)} 项   请点击：",
                   f"【{it['label']}】\n\n{it.get('hint', '')}\n\n"
                   f"点完自动跳到下一项，不用按键盘。")

        print("提示框已弹出（在游戏窗口右侧）。开始点吧。")
        refresh()
        prev_f11 = False
        try:
            while not self.done:
                ov.pump()
                if winio.user32.GetAsyncKeyState(VK_F12) & 0x8000:
                    print("  F12：结束")
                    break
                f11 = bool(winio.user32.GetAsyncKeyState(VK_F11) & 0x8000)
                if f11 and not prev_f11:
                    print(f"  F11：跳过 {self.items[self.idx]['id']}")
                    self.idx += 1
                    if self.idx >= len(self.items):
                        break
                    refresh()
                prev_f11 = f11
                while self.queue:
                    xy = self.queue.pop(0)
                    self._record(self.items[self.idx], xy)
                    self.idx += 1
                    if self.idx >= len(self.items):
                        self.done = True
                        break
                    refresh()
                time.sleep(0.04)
        except KeyboardInterrupt:
            print("\n  中断")
        finally:
            self.done = True
            self._stop = True
            ov.destroy()
            catcher.stop()

    def report(self) -> None:
        if not self.results:
            print("没记录到任何点击。")
            return
        with open(os.path.join(self.dir, "steps.json"), "w", encoding="utf-8") as f:
            json.dump({"name": self.name, "created": datetime.now().isoformat(timespec="seconds"),
                       "steps": self.results}, f, ensure_ascii=False, indent=2)
        print()
        print("=== 本轮记录 ===")
        for r in self.results:
            flag = "✓" if r["screen_changed"] else "⚠ 画面没变化（可能点空了或这项本来就不切画面）"
            print(f"  {r['order']+1}. {r['id']:<16} ({r['xy'][0]:>4},{r['xy'][1]:>4})  {flag}")
        bad = [r for r in self.results if not r["screen_changed"]]
        if bad:
            print(f"\n  有 {len(bad)} 项点下去画面没变：{[r['id'] for r in bad]}")
            print("  如果本来就是点了不该有反应的情况（比如点到空地），可以忽略；"
                  "否则建议只重录这几项。")
        idx = [{
            "step": r["order"], "note": r["label"], "screen": r["screen_before"],
            "shot": r["shot_after"],
        } for r in self.results]
        montage(self.dir, idx, cols=3)
        print(f"\n明细：{self.dir}\\steps.json")


def main() -> int:
    ap = argparse.ArgumentParser(prog="san9.pick")
    ap.add_argument("--name", required=True)
    ap.add_argument("--items", required=True, help="本轮要点的按钮清单 JSON")
    a = ap.parse_args()
    items = json.load(open(a.items, encoding="utf-8"))
    if len(items) > 5:
        print(f"⚠️ 这一轮有 {len(items)} 项，建议不超过 5 项，失误概率会低很多。")
    print(f"准备好后，在游戏里按提示点击。清单：")
    for i, it in enumerate(items, 1):
        print(f"  {i}. {it['label']}")
    print()
    time.sleep(2)
    p = Picker(a.name, items, vision.ScreenBook())
    p.run()
    p.report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
