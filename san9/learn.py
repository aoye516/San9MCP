"""容错标定：录像代替记坐标。

为什么推翻上一版
----------------
上一版是"记录你点击的坐标"，然后回放时照着坐标点。问题是：
**人不可能每次点得一样准，还可能点错。** 照坐标回放等于把人的手抖固化下来。

这一版改成：

    录  →  你正常做一遍操作，工具连续截图（3 fps），顺便记下点击当线索
    析  →  自动把这些画面**去重成若干个"界面"**，并识别出"从界面 A 点某处 → 界面 B"
    校  →  生成一张标注图，红框圈出工具认出来的按钮位置，你**用眼睛核对**
    放  →  自动重放一遍，这次**用模板匹配找按钮**，不是用死坐标

这样你的担心就化解了：

  * 点歪了 → 模板匹配会在 ±60px 范围内找"长得最像"的那块，不依赖精确坐标
  * 点错了 → 分析阶段会发现"点了画面没变"或"界面跳到了意料之外的地方"并标出来
  * 没录全 → 重放会告诉你**具体第几步**失败，你只补录那一段
  * 核对累 → 你不用报坐标，只需要看红框对不对

用法
----
    python -m san9.learn record  --task patrol --seconds 60
    python -m san9.learn analyze --rec state/rec_20260916_221500
    python -m san9.learn name    --rec state/rec_xxx --screen S3 --name city_menu
    python -m san9.learn replay  --rec state/rec_xxx
    python -m san9.learn commit  --rec state/rec_xxx --path facility.patrol
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from san9 import paths, vision, winio  # noqa: E402

ROOT = paths.PROJECT
STATE = paths.REC
VK_F12 = 0x7B

TPL_W, TPL_H = 96, 26  # 按钮模板尺寸（客户区像素）
SEARCH_R = 60  # 模板匹配的搜索半径
MATCH_MIN = 0.62  # 低于这个分数认为没找到


def resolve_rec(arg: str) -> str:
    """把命令行给的 --rec 解析成绝对路径。

    三种写法都认：绝对路径 / 相对数据目录 / 相对当前工作目录。
    """
    if os.path.isabs(arg) and os.path.isdir(arg):
        return arg
    cand = paths.resolve(arg)
    if os.path.isdir(cand):
        return cand
    cand2 = os.path.abspath(arg)
    if os.path.isdir(cand2):
        return cand2
    # 只给了名字（rec_2026...），去数据目录里找
    named = os.path.join(paths.REC, os.path.basename(arg.rstrip("/\\")))
    if os.path.isdir(named):
        return named
    return cand


# ---------------------------------------------------------------- 工具


def _numpy_from_bgra(bgra: bytes, w: int, h: int) -> np.ndarray:
    a = np.frombuffer(bgra, dtype=np.uint8).reshape(h, w, 4)
    return a[:, :, :3][:, :, ::-1].copy()  # BGRA -> RGB


def grab_rgb(hwnd: int, screen_region: bool = True) -> np.ndarray:
    w, h, buf = winio.grab(hwnd, use_window_dc=not screen_region)
    return _numpy_from_bgra(buf, w, h)


def save_jpg(path: str, rgb: np.ndarray, q: int = 82) -> None:
    """写 JPEG。

    注意：**绝对不能用 cv2.imwrite**。路径里只要含中文（本项目的目录叫「三国志9」），
    cv2.imwrite 会静默返回 False，什么都不写 —— 而且不报错。
    统一走 imencode 拿字节，再用 Python 自己写文件。
    """
    import cv2

    ok, buf = cv2.imencode(".jpg", rgb[:, :, ::-1], [cv2.IMWRITE_JPEG_QUALITY, q])
    if not ok:
        raise RuntimeError(f"JPEG 编码失败：{path}")
    with open(path, "wb") as f:
        f.write(buf.tobytes())


def load_rgb(path: str) -> np.ndarray:
    """读图片。同样不能用 cv2.imread（中文路径会返回 None）。"""
    import cv2

    with open(path, "rb") as f:
        raw = np.frombuffer(f.read(), dtype=np.uint8)
    bgr = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    if bgr is None:
        raise RuntimeError(f"读图失败：{path}")
    return bgr[:, :, ::-1].copy()


def load_sig(path: str) -> bytes:
    """从磁盘上的截图算界面签名 —— 必须和 vision.signature 用同一套算法。"""
    import cv2

    rgb = load_rgb(path)
    h, w = rgb.shape[:2]
    bgra = cv2.cvtColor(rgb[:, :, ::-1], cv2.COLOR_BGR2BGRA).tobytes()
    return vision.signature(bgra, w, h)


def cursor_client(hwnd: int) -> tuple[int, int] | None:
    pt = winio.wt.POINT()
    winio.user32.GetCursorPos(winio.ctypes.byref(pt))
    l, t, w, h = winio.client_rect_screen(hwnd)
    if l <= pt.x < l + w and t <= pt.y < t + h:
        return winio.to_client(pt.x, pt.y, hwnd)
    return None


# ---------------------------------------------------------------- 鼠标钩子

WH_MOUSE_LL = 14
WM_LBUTTONDOWN = 0x0201
WM_QUIT = 0x0012


class _MSLLHOOKSTRUCT(winio.ctypes.Structure):
    _fields_ = [("pt", winio.wt.POINT), ("mouseData", winio.wt.DWORD),
                ("flags", winio.wt.DWORD), ("time", winio.wt.DWORD),
                ("dwExtraInfo", winio.ULONG_PTR)]


class ClickCatcher:
    """低层鼠标钩子。

    为什么不用轮询 GetAsyncKeyState：鼠标在"按下"状态可能只存在几毫秒，
    50Hz 的轮询会整个漏掉（实测就漏了 0/4）。钩子是事件驱动的，
    哪怕按下抬起发生在同一微秒，也能抓到。
    """

    def __init__(self, hwnd: int, on_click):
        import threading

        self.hwnd = hwnd
        self.on_click = on_click
        self.thread: threading.Thread | None = None
        self.proc = None
        self.tid = 0
        self._threading = threading

    def _run(self) -> None:
        import ctypes
        import ctypes.wintypes as wt

        u = winio.user32
        self.tid = winio.kernel32.GetCurrentThreadId()
        # 必须声明 argtypes，否则 64 位的 lParam 会被当成 c_int 传，溢出报错
        u.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                     winio.wt.WPARAM, winio.wt.LPARAM]
        u.CallNextHookEx.restype = ctypes.c_ssize_t
        next_hook = u.CallNextHookEx

        def _inner(n_code, w_param, l_param):
            try:
                if n_code == 0 and w_param == WM_LBUTTONDOWN:
                    data = ctypes.cast(l_param, ctypes.POINTER(_MSLLHOOKSTRUCT)).contents
                    self.on_click(data.pt.x, data.pt.y)
            except Exception:
                pass
            return next_hook(None, n_code, w_param, l_param)

        self.proc = winio.ctypes.WINFUNCTYPE(
            winio.ctypes.c_ssize_t, winio.ctypes.c_int,
            winio.wt.WPARAM, winio.wt.LPARAM)(_inner)
        u.SetWindowsHookExW(WH_MOUSE_LL, self.proc, None, 0)
        msg = wt.MSG()
        while u.GetMessageW(winio.ctypes.byref(msg), None, 0, 0) > 0:
            pass

    def start(self) -> None:
        self.thread = self._threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        time.sleep(0.3)

    def stop(self) -> None:
        if self.tid:
            winio.user32.PostThreadMessageW(self.tid, WM_QUIT, 0, 0)


# ---------------------------------------------------------------- record


def cmd_record(a) -> int:
    paths.ensure()
    win = winio.find_game("San9WPK.exe")
    if win is None:
        print("找不到游戏窗口。", file=sys.stderr)
        return 1

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    rec = os.path.join(STATE, f"rec_{stamp}")
    os.makedirs(os.path.join(rec, "frames"), exist_ok=True)

    print("=" * 62)
    print(f"  录制任务：{a.task}")
    print(f"  时长：{a.seconds:.0f} 秒（随时按 F12 提前结束）")
    print(f"  存放：{rec}")
    print("=" * 62)
    print()
    print("  接下来请：")
    print("    1. 马上切到游戏窗口（别再来回切，切回来会录到终端画面）")
    print("    2. 把要录的操作正常做一遍 —— 慢一点没关系，点歪也没关系")
    print("    3. 做完按 F12")
    print()
    for i in (3, 2, 1):
        print(f"  {i}...")
        time.sleep(1)

    winio.focus(win.hwnd)
    time.sleep(0.4)

    meta = {"task": a.task, "started": stamp, "seconds": a.seconds, "fps": a.fps,
            "client": list(winio.client_rect_screen(win.hwnd)),
            "capture": "screen" if a.screen_region else "window"}
    with open(os.path.join(rec, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    clicks_f = open(os.path.join(rec, "clicks.jsonl"), "w", encoding="utf-8")

    interval = 1.0 / max(1.0, a.fps)
    t0 = time.time()
    next_frame = t0
    fidx = 0
    cidx = 0
    last_click_t = -9
    pending: list[dict] = []

    def on_click(sx: int, sy: int) -> None:
        now = time.time()
        if now - last_click_t_ref[0] < 0.2:
            return
        l, t, w, h = winio.client_rect_screen(win.hwnd)
        if not (l <= sx < l + w and t <= sy < t + h):
            return  # 点到窗口外面了，不算
        cx, cy = winio.to_client(sx, sy, win.hwnd)
        last_click_t_ref[0] = now
        pending.append({"t": round(now - t0, 2), "xy": [cx, cy], "frame": fidx})

    last_click_t_ref = [-9.0]
    catcher = ClickCatcher(win.hwnd, on_click)
    catcher.start()

    while time.time() - t0 < a.seconds:
        if winio.user32.GetAsyncKeyState(VK_F12) & 0x8000:
            print("\n  F12：提前结束")
            break
        now = time.time()
        if now >= next_frame:
            try:
                rgb = grab_rgb(win.hwnd, a.screen_region)
                save_jpg(os.path.join(rec, "frames", f"{fidx:05d}.jpg"), rgb)
                fidx += 1
            except Exception as e:
                print("  截图失败:", e)
            next_frame = now + interval
        while pending:
            c = pending.pop(0)
            c["i"] = cidx
            clicks_f.write(json.dumps(c) + "\n")
            clicks_f.flush()
            cidx += 1
            print(f"  记录点击 #{cidx-1:02d}  客户区 {c['xy']}")
        time.sleep(0.02)

    catcher.stop()
    while pending:
        c = pending.pop(0)
        c["i"] = cidx
        clicks_f.write(json.dumps(c) + "\n")
        cidx += 1
        print(f"  记录点击 #{cidx-1:02d}  客户区 {c['xy']}")
    clicks_f.close()
    meta["frames"] = fidx
    meta["clicks"] = cidx
    meta["ended"] = datetime.now().strftime("%Y%m%d_%H%M%S")
    with open(os.path.join(rec, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print()
    written = len([f for f in os.listdir(os.path.join(rec, "frames"))
                   if f.endswith(".jpg")])
    if written != fidx:
        print(f"  ⚠️  只写成功 {written}/{fidx} 帧 —— 截图保存有问题，检查磁盘权限")
    print(f"  录到 {fidx} 帧、{cidx} 次点击")
    print(f"  位置：{rec}")
    print(f"  下一步：python -m san9.learn analyze --rec \"{rec}\"")
    return 0


# ---------------------------------------------------------------- analyze


def _cluster(sigs: list[bytes], thresh: float = 0.05) -> list[int]:
    """把帧序列聚成"界面"。返回每一帧所属的界面编号。"""
    reps: list[bytes] = []
    label: list[int] = []
    for s in sigs:
        best, bd = -1, 9.9
        for i, r in enumerate(reps):
            d = vision.distance(s, r)
            if d < bd:
                best, bd = i, d
        if best >= 0 and bd <= thresh:
            label.append(best)
        else:
            reps.append(s)
            label.append(len(reps) - 1)
    return label


def cmd_analyze(a) -> int:
    import cv2

    rec = resolve_rec(a.rec)
    meta = json.load(open(os.path.join(rec, "meta.json"), encoding="utf-8"))
    frames = sorted(f for f in os.listdir(os.path.join(rec, "frames")) if f.endswith(".jpg"))
    if not frames:
        print("没有帧。", file=sys.stderr)
        return 1
    fpath = [os.path.join(rec, "frames", f) for f in frames]

    print(f"扫描 {len(frames)} 帧…")
    sigs = [load_sig(p) for p in fpath]
    labels = _cluster(sigs)
    nunique = len(set(labels))
    print(f"识别出 {nunique} 个不同界面")

    clicks = []
    cpath = os.path.join(rec, "clicks.jsonl")
    if os.path.exists(cpath):
        for line in open(cpath, encoding="utf-8"):
            if line.strip():
                clicks.append(json.loads(line))

    tpl_dir = os.path.join(rec, "tpl")
    os.makedirs(tpl_dir, exist_ok=True)

    steps = []
    suspicious = []
    LOOKAHEAD = 10  # 点击后往后看多少帧找"界面确实变了"
    fps = float(meta.get("fps") or 3.0)
    for k, c in enumerate(clicks):
        fi = min(c["frame"], len(frames) - 1)
        bi = max(0, fi - 1)
        before = load_rgb(fpath[bi])

        # 界面切换有快有慢（读档、进战斗要好几秒），所以往后再多看几帧，
        # 取第一个明显不同的帧当作"点击后的界面"。只看 +2 帧会误报。
        ai = None
        for j in range(fi, min(len(frames), fi + LOOKAHEAD)):
            if vision.distance(sigs[bi], sigs[j]) > 0.05:
                ai = j
                break
        changed = ai is not None
        if ai is None:
            ai = min(len(frames) - 1, fi + 2)

        x, y = c["xy"]
        h, w = before.shape[:2]
        x0 = max(0, min(w - TPL_W, x - TPL_W // 2))
        y0 = max(0, min(h - TPL_H, y - TPL_H // 2))
        tpl = before[y0:y0 + TPL_H, x0:x0 + TPL_W]
        tpl_name = f"step_{k:02d}.jpg"
        save_jpg(os.path.join(tpl_dir, tpl_name), tpl, q=95)

        screen_before = labels[bi]
        screen_after = labels[ai]
        step = {
            "i": k, "t": c["t"], "click_xy": [x, y],
            "template": f"tpl/{tpl_name}",
            "tpl_box": [int(x0), int(y0), TPL_W, TPL_H],
            "screen_before": f"S{screen_before}",
            "screen_after": f"S{screen_after}",
            "screen_changed": changed,
            "frames_to_change": (ai - fi) if changed else None,
        }
        if not changed:
            step["warn"] = f"点下去 {LOOKAHEAD} 帧（约 {LOOKAHEAD / fps:.1f} 秒）内画面没变化 —— 可能点空了"
            suspicious.append(step)
        steps.append(step)

    # 界面样本
    screens = {}
    for i, lab in enumerate(labels):
        sid = f"S{lab}"
        if sid not in screens:
            screens[sid] = {"first_frame": i, "sample": paths.store(fpath[i]), "count": 0}
        screens[sid]["count"] += 1
    # 每个界面用中间那一帧当样本，更稳
    for sid in screens:
        idxs = [i for i, l in enumerate(labels) if f"S{l}" == sid]
        mid = idxs[len(idxs) // 2]
        screens[sid]["sample"] = paths.store(fpath[mid])
        screens[sid]["frames"] = [idxs[0], idxs[-1]]

    # 推断按键步骤。
    # 录制只抓鼠标，但菜单返回全靠 Esc / 右键。好消息是：如果画面在两次点击之间
    # 自己变回去了，那一定有人按了返回键 —— 从帧序列就能反推出来，不用去钩键盘。
    events: list[dict] = []
    prev_after = None
    for st in steps:
        fi = min(clicks[st["i"]]["frame"], len(frames) - 1)
        if prev_after is not None and prev_after < fi:
            gap_labels = labels[prev_after:fi + 1]
            changed = [j for j in range(1, len(gap_labels))
                       if gap_labels[j] != gap_labels[j - 1]]
            if changed:
                events.append({"kind": "key", "name": "esc", "times": len(changed),
                               "note": f"帧 {prev_after}~{fi} 之间画面自行变化 {len(changed)} 次"
                                       f"（推断为返回键）"})
        events.append({"kind": "click", **st})
        prev_after = min(len(frames) - 1,
                         fi + (st.get("frames_to_change") or 2) + 1)

    out = {"rec": paths.store(rec), "task": meta["task"],
           "frames": len(frames), "screens": screens, "steps": steps,
           "events": events,
           "suspicious": [s["i"] for s in suspicious]}
    with open(os.path.join(rec, "analysis.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    # 生成核对图
    report = render_report(rec, screens, steps, np)
    print()
    print(f"界面 {nunique} 个，步骤 {len(steps)} 条")
    if suspicious:
        print(f"⚠️  有 {len(suspicious)} 步可疑（点下去画面没变）："
              f"{[s['i'] for s in suspicious]}")
    else:
        print("没有发现可疑步骤")
    print()
    print("界面清单：")
    for sid, s in sorted(screens.items()):
        print(f"  {sid:<5} 出现 {s['count']:>3} 帧   样本 {s['sample']}")
    print()
    print("步骤清单：")
    for s in steps:
        flag = "  ⚠️" if s.get("warn") else ""
        print(f"  #{s['i']:02d}  {s['screen_before']} -> {s['screen_after']}"
              f"  点击{s['click_xy']}{flag}")
    print()
    print(f"核对图：{report}")
    print("请打开它，确认每个红框圈住的确实是你想点的按钮。")
    print("然后给界面起名、再重放验证：")
    print(f"  python -m san9.learn name --rec \"{rec}\" --screen S1 --name strategy")
    print(f"  python -m san9.learn replay --rec \"{rec}\"")
    return 0


def render_report(rec: str, screens: dict, steps: list, np) -> str:
    """每个界面出一张标注图：红框圈出录到的按钮位置。"""
    import cv2

    paths.ensure()
    tiles = []
    for sid in sorted(screens):
        sample = paths.resolve(screens[sid]["sample"])
        if not os.path.exists(sample):
            continue
        img = load_rgb(sample)[:, :, ::-1].copy()  # RGB -> BGR
        for s in steps:
            if s["screen_before"] != sid:
                continue
            x, y = s["click_xy"]
            cv2.rectangle(img, (x - TPL_W // 2, y - TPL_H // 2),
                          (x + TPL_W // 2, y + TPL_H // 2), (0, 0, 255), 2)
            cv2.putText(img, f"#{s['i']}", (x - TPL_W // 2, max(14, y - TPL_H // 2 - 4)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1, cv2.LINE_AA)
        cv2.putText(img, sid, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 200, 0), 2,
                    cv2.LINE_AA)
        small = cv2.resize(img, (512, 384))
        tiles.append(small)
    if not tiles:
        return ""
    while len(tiles) % 3:
        tiles.append(np.zeros_like(tiles[0]))
    rows = [np.hstack(tiles[i:i + 3]) for i in range(0, len(tiles), 3)]
    sheet = np.vstack(rows)
    out = os.path.join(paths.SHOTS, f"verify_{os.path.basename(rec)}.jpg")
    save_jpg(out, sheet[:, :, ::-1], q=92)  # 同样绕开 cv2.imwrite
    return out


# ---------------------------------------------------------------- name


def cmd_name(a) -> int:
    p = os.path.join(resolve_rec(a.rec), "analysis.json")
    data = json.load(open(p, encoding="utf-8"))
    old = a.screen
    if old not in data["screens"]:
        print(f"没有 {old}。现有：{list(data['screens'])}", file=sys.stderr)
        return 1
    data["screens"][old]["name"] = a.name
    names = data.setdefault("names", {})
    names[old] = a.name
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"{old} -> {a.name}")
    return 0


# ---------------------------------------------------------------- replay


def locate(frame: np.ndarray, tpl: np.ndarray, hint: tuple[int, int],
           radius: int = SEARCH_R):
    """在 hint 附近找 tpl。返回 (x, y, score)；score 是归一化相关。"""
    import cv2

    fh, fw = frame.shape[:2]
    th, tw = tpl.shape[:2]
    cx, cy = hint
    x0 = max(0, cx - radius - tw // 2)
    y0 = max(0, cy - radius - th // 2)
    x1 = min(fw, cx + radius + tw // 2)
    y1 = min(fh, cy + radius + th // 2)
    if x1 - x0 < tw or y1 - y0 < th:
        return None, None, 0.0
    win = frame[y0:y1, x0:x1]
    g1 = cv2.cvtColor(win, cv2.COLOR_RGB2GRAY)
    g2 = cv2.cvtColor(tpl, cv2.COLOR_RGB2GRAY)
    res = cv2.matchTemplate(g1, g2, cv2.TM_CCOEFF_NORMED)
    _, maxv, _, maxloc = cv2.minMaxLoc(res)
    return x0 + maxloc[0] + tw // 2, y0 + maxloc[1] + th // 2, float(maxv)


def cmd_replay(a) -> int:
    import cv2

    win = winio.find_game("San9WPK.exe")
    if win is None:
        print("找不到游戏窗口。", file=sys.stderr)
        return 1
    rec = resolve_rec(a.rec)
    data = json.load(open(os.path.join(rec, "analysis.json"), encoding="utf-8"))
    events = data.get("events") or [{"kind": "click", **s} for s in data["steps"]]
    if a.limit:
        events = events[: a.limit]

    print("=" * 62)
    print(f"  重放校验：{rec}（{len(events)} 步）")
    print("  这一步会用模板匹配重新找按钮，不用死坐标。")
    print("=" * 62)
    print("  3 秒后开始，请把游戏窗口留在前台")
    time.sleep(3)
    winio.focus(win.hwnd)
    time.sleep(0.5)

    ok_n = 0
    for s in events:
        if s["kind"] == "key":
            print(f"  按键 {s['name']} x{s.get('times', 1)}"
                  f"  ({s.get('note', '')})", end="")
            winio.press(s["name"], s.get("times", 1), 0.3)
            time.sleep(0.5)
            ok_n += 1
            print("  ✓")
            continue

        tpl = load_rgb(os.path.join(rec, s["template"]))
        frame = grab_rgb(win.hwnd, a.screen_region)
        x, y, score = locate(frame, tpl, tuple(s["click_xy"]))
        if x is None or score < MATCH_MIN:
            print(f"\n  ✗ 第 {s['i']} 步失败：在点击位置附近找不到这个按钮")
            print(f"    期望位置 {s['click_xy']}，最佳匹配分数 {score:.3f}"
                  f"（阈值 {MATCH_MIN}）")
            print(f"    模板：{s['template']}")
            print("    → 大概率是这一步录的时候点在了别的地方，或者当前不在正确的界面。")
            print("      解决：只重录这一段，或者用 --limit 跳过前几步再试。")
            return 1
        drift = ((x - s["click_xy"][0]) ** 2 + (y - s["click_xy"][1]) ** 2) ** 0.5
        before = data["screens"][s["screen_before"]].get("name", s["screen_before"])
        print(f"  #{s['i']:02d} {before:<14} 匹配 {score:.3f} "
              f"点击({x},{y}) 偏移{drift:.0f}px", end="")
        winio.click_client(win.hwnd, x, y)
        time.sleep(0.7)
        ok_n += 1
        print("  ✓")

    print()
    print(f"  全部 {ok_n} 步重放成功 ✓")
    print(f"  提交成路径：python -m san9.learn commit --rec \"{rec}\" --path <路径键>")
    return 0


# ---------------------------------------------------------------- commit


def cmd_commit(a) -> int:
    from san9 import driver

    rec = resolve_rec(a.rec)
    data = json.load(open(os.path.join(rec, "analysis.json"), encoding="utf-8"))
    names = data.get("names", {})
    events = data.get("events") or [{"kind": "click", **s} for s in data["steps"]]
    out: list[dict] = []
    last_screen = None
    for ev in events:
        if ev["kind"] == "key":
            out.append({"kind": "key", "name": ev["name"],
                        "times": ev.get("times", 1), "wait": 0.5,
                        "note": ev.get("note", "")})
            continue
        sb = names.get(ev["screen_before"], ev["screen_before"])
        sa = names.get(ev["screen_after"], ev["screen_after"])
        out.append({
            "kind": "click",
            "xy": ev["click_xy"],
            "wait": 0.6,
            "note": f"{sb} -> {sa}",
            # 存成相对数据目录的形式，整个数据目录可以整体搬走
            "template": paths.store(os.path.join(rec, ev["template"])),
            "hint": ev["click_xy"],
        })
        last_screen = sa
    if last_screen:
        out.append({"kind": "wait_screen", "screen": last_screen, "timeout": 20})
    t = driver.PathTable()
    t.record(tuple(a.path.split(".")), out)
    print(f"已写入 {t.path}：{a.path}（{len(out)} 步，含 "
          f"{sum(1 for x in out if x['kind'] == 'key')} 个按键）")
    print("带 template 的 click 会优先用模板匹配执行，不依赖死坐标。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="san9.learn", description="容错标定器")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("record", help="录一段操作（录像，不记死坐标）")
    p.add_argument("--task", required=True)
    p.add_argument("--seconds", type=float, default=60)
    p.add_argument("--fps", type=float, default=3)
    p.add_argument("--screen-region", action="store_true", default=True,
                   help="按屏幕区域抓（默认开，游戏在前台时最准）")
    p.set_defaults(fn=cmd_record)

    p = sub.add_parser("analyze", help="分析录像：切界面、提取按钮模板、标可疑步骤")
    p.add_argument("--rec", required=True)
    p.set_defaults(fn=cmd_analyze)

    p = sub.add_parser("name", help="给界面起个有意义的名字")
    p.add_argument("--rec", required=True)
    p.add_argument("--screen", required=True)
    p.add_argument("--name", required=True)
    p.set_defaults(fn=cmd_name)

    p = sub.add_parser("replay", help="自动重放校验（模板匹配）")
    p.add_argument("--rec", required=True)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--screen-region", action="store_true", default=True)
    p.set_defaults(fn=cmd_replay)

    p = sub.add_parser("commit", help="把录好的路径写进 config/paths.json")
    p.add_argument("--rec", required=True)
    p.add_argument("--path", required=True)
    p.set_defaults(fn=cmd_commit)

    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
