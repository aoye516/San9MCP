"""画面探索器：按剧本点下去，把沿途看到的画面全部记下来。

为什么需要它
------------
标定要的不是"某一条路径录得多准"，而是**先把这个游戏有多少种画面摸清楚**。
所以这个工具是"广撒网"用的：

  * 按剧本执行点击/按键，每步截图
  * **每见到一个没登记过的画面，自动登记指纹并编号**（auto_01, auto_02…）并存样本
  * 跑完出一张总览图，一眼能看完这轮的旅程

跑几轮下来，界面目录就攒齐了，再回头挑出关键画面（战略面、出征界面、进行面…）
正式命名并标定路径。

剧本格式（JSON 数组）：
    [{"do": "click", "xy": [250, 300], "note": "载入游戏资料", "wait": 2.5},
     {"do": "key",   "name": "esc",  "note": "返回", "wait": 1.5}]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from san9 import paths, vision, winio  # noqa: E402
from san9.learn import grab_rgb, load_rgb, save_jpg  # noqa: E402


class Explorer:
    def __init__(self, name: str, auto_learn: bool = True):
        paths.ensure()
        self.name = name
        self.dir = os.path.join(paths.DATA, "explore", name)
        os.makedirs(self.dir, exist_ok=True)
        self.book = vision.ScreenBook()
        self.auto_learn = auto_learn
        self.index: list[dict] = []
        self._auto_n = len([k for k in self.book.entries if k.startswith("auto_")])
        self.win = winio.find_game("San9WPK.exe")

    # ------------------------------------------------------------ 内部

    def _see(self, note: str, tag: str) -> tuple[str, float, str]:
        """看一眼当前画面：识别；不认识就自动登记。返回 (画面名, 距离, 图片路径)。"""
        gw, gh, buf = winio.grab(self.win.hwnd, use_window_dc=False)
        name, dist, ranked = self.book.identify(buf, gw, gh)
        p = os.path.join(self.dir, f"{len(self.index):03d}_{tag}.jpg")
        winio.save_png(p, gw, gh, buf) if False else save_jpg(
            p, np.frombuffer(buf, dtype=np.uint8).reshape(gh, gw, 4)[:, :, :3][:, :, ::-1].copy(), 80)
        if name is None and self.auto_learn:
            self._auto_n += 1
            name = f"auto_{self._auto_n:02d}"
            self.book.learn(name, buf, gw, gh, note=note[:40], overwrite=True)
            self.book.save()
            dist = 0.0
            if name not in [e.get("screen") for e in self.index]:
                print(f"    [新画面] {name}  <- {note}")
        second = ranked[1][1] if len(ranked) > 1 else None
        self.index.append({
            "step": len(self.index), "tag": tag, "note": note,
            "screen": name, "distance": round(dist, 4),
            "runner_up": round(second, 4) if second is not None else None,
            "shot": paths.store(p),
        })
        return name, dist, p

    # ------------------------------------------------------------ 对外

    def step(self, do: str, **kw) -> None:
        note = kw.get("note", "")
        wait = kw.get("wait", 1.5)
        if do == "click":
            xy = kw["xy"]
            print(f"  click {xy}  {note}")
            winio.click_client(self.win.hwnd, xy[0], xy[1])
        elif do == "key":
            name = kw.get("name", "esc")
            times = kw.get("times", 1)
            print(f"  key   {name} x{times}  {note}")
            winio.press(name, times, 0.2)
        elif do == "wait":
            print(f"  wait  {wait}s  {note}")
        else:
            raise ValueError(f"未知动作 {do}")
        time.sleep(wait)
        self._see(note, do)

    def run(self, steps: list[dict]) -> None:
        if self.win is None:
            raise SystemExit("找不到游戏窗口")
        winio.focus(self.win.hwnd)
        time.sleep(0.8)
        print(f"=== 探索 {self.name}（{len(steps)} 步）===")
        self._see("起点", "start")
        for st in steps:
            st = dict(st)
            do = st.pop("do")
            self.step(do, **st)
        self.report()

    def report(self) -> None:
        with open(os.path.join(self.dir, "index.json"), "w", encoding="utf-8") as f:
            json.dump(self.index, f, ensure_ascii=False, indent=2)
        print()
        print("=== 画面清单 ===")
        seen: dict[str, int] = {}
        for e in self.index:
            seen[e["screen"]] = seen.get(e["screen"], 0) + 1
        for s, n in sorted(seen.items(), key=lambda x: -x[1]):
            note = self.book.entries.get(s, {}).get("note", "")
            print(f"  {s:<12} 出现 {n:>2} 次   {note}")
        print(f"\n明细：{self.dir}")
        montage(self.dir, self.index)


def montage(explore_dir: str, index: list[dict], cols: int = 4,
            cell: tuple[int, int] = (400, 300)) -> str:
    """把这次探索的截图拼成一张总览图。"""
    import cv2

    tiles = []
    for e in index:
        p = paths.resolve(e["shot"])
        if not os.path.exists(p):
            continue
        img = load_rgb(p)[:, :, ::-1].copy()
        img = cv2.resize(img, cell)
        cv2.putText(img, f"{e['step']:02d} {e['screen'] or '?'}", (6, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(img, e["note"][:26], (6, 46),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
        tiles.append(img)
    if not tiles:
        return ""
    while len(tiles) % cols:
        tiles.append(np.zeros_like(tiles[0]))
    rows = [np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)]
    sheet = np.vstack(rows)
    out = os.path.join(paths.SHOTS, f"explore_{os.path.basename(explore_dir)}.jpg")
    save_jpg(out, sheet[:, :, ::-1], 78)
    print(f"总览图：{out}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(prog="san9.explore")
    ap.add_argument("--name", required=True)
    ap.add_argument("--script", required=True, help="剧本 JSON 文件路径")
    ap.add_argument("--no-auto-learn", action="store_true")
    a = ap.parse_args()
    steps = json.load(open(a.script, encoding="utf-8"))
    Explorer(a.name, auto_learn=not a.no_auto_learn).run(steps)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
