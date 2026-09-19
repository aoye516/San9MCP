"""三国志9 窗口 I/O：查找窗口 / 截图 / 鼠标 / 键盘。

设计原则：纯 ctypes + 标准库，不依赖 pywin32 / pyautogui，
这样在受管的隔离 Python 里也能跑。

关键点：
  * 游戏是 DirectX 应用，输入要走 SendInput + 扫描码（DirectInput 兼容），
    不要用 PostMessage 之类的伪输入。
  * 截图优先"按屏幕区域抓"（窗口化时最稳），失败再退回 PrintWindow。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import struct
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ---------------------------------------------------------------- DPI
#
# 必须在这里就把进程设成 DPI 感知，否则整个坐标系是烂的。
#
# 实测（本机 2560x1440 @125%）：
#   不感知时，GetClientRect / GetCursorPos / SetCursorPos 全都返回"虚拟坐标"
#   （1280x960 的窗口会被报成 1024x768）；
#   但低层鼠标钩子报的是**物理坐标**。两套坐标系一混，录下来的坐标就全错位。
#
# 设成 DPI 感知之后，所有 API 统一在物理坐标里说话，钩子、截图、点击三者一致。
try:
    # PER_MONITOR_AWARE_V2 = -4
    ctypes.WinDLL("user32").SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.WinDLL("user32").SetProcessDPIAware()
        except Exception:
            pass

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

# ---------------------------------------------------------------- 结构体


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wt.LONG),
        ("dy", wt.LONG),
        ("mouseData", wt.DWORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wt.WORD),
        ("wScan", wt.WORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD),
        ("biWidth", wt.LONG),
        ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD),
        ("biBitCount", wt.WORD),
        ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD),
        ("biXPelsPerMeter", wt.LONG),
        ("biYPelsPerMeter", wt.LONG),
        ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_MOVE, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0001, 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP, MOUSEEVENTF_ABSOLUTE = 0x0008, 0x0010, 0x8000
KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE, KEYEVENTF_EXTENDEDKEY = 0x0002, 0x0008, 0x0001
PW_CLIENTONLY, PW_RENDERFULLCONTENT = 0x1, 0x2
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0

# ---------------------------------------------------------------- 窗口


class WindowInfo:
    __slots__ = ("hwnd", "title", "cls", "pid", "exe", "visible", "rect")

    def __init__(self, hwnd, title, cls, pid, exe, visible, rect):
        self.hwnd, self.title, self.cls = hwnd, title, cls
        self.pid, self.exe = pid, exe
        self.visible, self.rect = visible, rect

    def __repr__(self):
        return f"<Window 0x{self.hwnd:08X} {self.exe!r} {self.title!r} {self.rect}>"

    @property
    def size(self):
        return (self.rect[2] - self.rect[0], self.rect[3] - self.rect[1])


_EXE_CACHE: dict[int, str] = {}


def _exe_of(pid: int) -> str:
    if pid in _EXE_CACHE:
        return _EXE_CACHE[pid]
    name = ""
    try:
        h = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if h:
            buf = ctypes.create_unicode_buffer(1024)
            size = wt.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                name = buf.value
            kernel32.CloseHandle(h)
    except Exception:
        pass
    _EXE_CACHE[pid] = os.path.basename(name) if name else ""
    return _EXE_CACHE[pid]


def list_windows(visible_only: bool = True) -> list[WindowInfo]:
    out: list[WindowInfo] = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def _cb(hwnd, _):
        if visible_only and not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        t = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, t, n + 1)
        c = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, c, 256)
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        r = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        out.append(
            WindowInfo(hwnd, t.value, c.value, pid.value, _exe_of(pid.value), True,
                       (r.left, r.top, r.right, r.bottom))
        )
        return True

    user32.EnumWindows(_cb, 0)
    return out


def find_game(exe_name: str = "San9WPK.exe") -> WindowInfo | None:
    """找到三国志9的主窗口。"""
    cands = [w for w in list_windows() if w.exe.lower() == exe_name.lower()]
    if not cands:
        return None
    # 取可见且最大的那个
    cands.sort(key=lambda w: (w.size[0] * w.size[1]), reverse=True)
    return cands[0]


def find_all_game_processes(exe_name: str = "San9WPK") -> list[tuple[int, str]]:
    """用 ToolHelp 快照列进程，游戏还没开窗口时也能发现。"""
    TH32CS_SNAPPROCESS = 0x2

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
            ("th32DefaultHeapID", ULONG_PTR), ("th32ModuleID", wt.DWORD),
            ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD),
            ("pcPriClassBase", wt.LONG), ("dwFlags", wt.DWORD),
            ("szExeFile", wt.WCHAR * 260),
        ]

    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    res = []
    if snap == -1:
        return res
    e = PROCESSENTRY32W()
    e.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    ok = kernel32.Process32FirstW(snap, ctypes.byref(e))
    while ok:
        if exe_name.lower() in e.szExeFile.lower():
            res.append((e.th32ProcessID, e.szExeFile))
        ok = kernel32.Process32NextW(snap, ctypes.byref(e))
    kernel32.CloseHandle(snap)
    return res


def dpi_scale(hwnd: int) -> float:
    """窗口的"逻辑 → 物理"缩放比。

    为什么需要它 —— 这是这个项目最坑的一个坑：
    《三国志9》是 DPI 不感知的老程序，在 125% 缩放的系统上，
    Windows 会把它的画面拉伸 1.25 倍显示。而我们的进程是 DPI 感知的，
    于是出现两套坐标：

      * `GetClientRect` / `ClientToScreen` 返回**逻辑**尺寸（1280x960）
      * 鼠标钩子、`SetCursorPos`、屏幕 DC 用**物理**尺寸（1600x1200）

    窗口截图拿到的也是逻辑尺寸。所以：
        物理屏幕坐标 = (逻辑客户区原点 + 逻辑客户区坐标) * scale

    实测验证：截图里 (975,696) 的「進行」→ 逻辑 (975,696) → 物理 (1224,911)，
    与用户实际点击位置 (1222,898) 吻合。
    """
    try:
        r = wt.RECT()
        dwm = ctypes.WinDLL("dwmapi")
        hr = dwm.DwmGetWindowAttribute(wt.HWND(hwnd), 9,  # DWMWA_EXTENDED_FRAME_BOUNDS
                                       ctypes.byref(r), ctypes.sizeof(r))
        if hr == 0:
            wr = wt.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(wr))
            logical_w = wr.right - wr.left
            physical_w = r.right - r.left
            if logical_w > 0 and physical_w > 0:
                return physical_w / logical_w
    except Exception:
        pass
    try:
        return user32.GetDpiForSystem() / 96.0
    except Exception:
        return 1.0


_scale_cache: dict[int, float] = {}


def scale_of(hwnd: int) -> float:
    if hwnd not in _scale_cache:
        _scale_cache[hwnd] = dpi_scale(hwnd)
    return _scale_cache[hwnd]


def client_rect_screen(hwnd: int) -> tuple[int, int, int, int]:
    """返回客户区的**逻辑** (left, top, width, height)。

    注意是逻辑坐标 —— 截图就是这个坐标系。要换成物理屏幕坐标请乘 scale_of()。
    """
    r = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    pt = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return (pt.x, pt.y, r.right - r.left, r.bottom - r.top)


def focus(hwnd: int) -> bool:
    """把窗口切到前台。游戏需要在最前面才能收输入。"""
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.15)
    return user32.GetForegroundWindow() == hwnd


def is_foreground(hwnd: int) -> bool:
    return user32.GetForegroundWindow() == hwnd


# ---------------------------------------------------------------- 截图


def grab(hwnd: int, use_window_dc: bool = False) -> tuple[int, int, bytes]:
    """抓窗口画面，返回 (w, h, BGRA 字节)。

    **必须用屏幕拷贝（默认），不要用 use_window_dc。**

    实测：这个游戏的 `GetDC(hwnd)` 返回的是**另一套坐标空间**，
    和真实屏幕不对应（同一矩形下两图平均色差 54）。而 `SetCursorPos`
    和鼠标钩子都走物理屏幕坐标 —— 用窗口 DC 截图会让所有点击错位。
    屏幕拷贝和点击天生同一个坐标系，这是唯一自洽的选择。

    代价：游戏必须在前台。调用方负责先 focus()。
    """
    if use_window_dc:
        l, t, w, h = 0, 0, 0, 0
        r = wt.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(r))
        w, h = r.right, r.bottom
        src_dc = user32.GetDC(hwnd)
    else:
        l, t, w, h = client_rect_screen(hwnd)
        src_dc = user32.GetDC(0)

    mem_dc = gdi32.CreateCompatibleDC(src_dc)
    bmp = gdi32.CreateCompatibleBitmap(src_dc, w, h)
    gdi32.SelectObject(mem_dc, bmp)
    gdi32.BitBlt(mem_dc, 0, 0, w, h, src_dc, l, t, SRCCOPY)

    bi = BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h  # 负数 = 自上而下
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bi.bmiHeader.biCompression = 0
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem_dc, bmp, 0, h, buf, ctypes.byref(bi), DIB_RGB_COLORS)

    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(hwnd if use_window_dc else 0, src_dc)
    return w, h, buf.raw


def save_bmp(path: str, w: int, h: int, bgra: bytes) -> str:
    """把 grab() 的结果写成 32 位 BMP（自带 alpha，无需 Pillow）。"""
    hdr = struct.pack("<2sIHHI", b"BM", 14 + 40 + len(bgra), 0, 0, 14 + 40)
    dib = struct.pack(
        "<IiiHHIIiiII", 40, w, -h, 1, 32, 0, len(bgra), 2835, 2835, 0, 0
    )
    with open(path, "wb") as f:
        f.write(hdr + dib + bgra)
    return path


def save_png(path: str, w: int, h: int, bgra: bytes) -> str:
    """有 Pillow 就存 PNG，没有就退回 BMP。"""
    try:
        from PIL import Image  # type: ignore

        img = Image.frombytes("RGBA", (w, h), bgra).convert("RGB")
        img.save(path)
        return path
    except Exception:
        return save_bmp(os.path.splitext(path)[0] + ".bmp", w, h, bgra)


# ---------------------------------------------------------------- 输入


def _send(*inputs: INPUT) -> None:
    n = len(inputs)
    arr = (INPUT * n)(*inputs)
    user32.SendInput(n, arr, ctypes.sizeof(INPUT))


def _key_input(vk: int, up: bool = False, scancode: bool = True) -> INPUT:
    scan = user32.MapVirtualKeyW(vk, 0)  # MAPVK_VK_TO_VSC
    flags = 0
    if scancode:
        flags |= KEYEVENTF_SCANCODE
    if up:
        flags |= KEYEVENTF_KEYUP
    if vk in (0x25, 0x26, 0x27, 0x28, 0x21, 0x22, 0x23, 0x24, 0x2D, 0x2E, 0x5B, 0x5C):
        flags |= KEYEVENTF_EXTENDEDKEY
    i = INPUT()
    i.type = INPUT_KEYBOARD
    i.ki = KEYBDINPUT(wVk=vk if not scancode else 0, wScan=scan, dwFlags=flags)
    return i


def key_down(vk: int, scancode: bool = True) -> None:
    _send(_key_input(vk, False, scancode))


def key_up(vk: int, scancode: bool = True) -> None:
    _send(_key_input(vk, True, scancode))


def key_tap(vk: int, hold: float = 0.04, scancode: bool = True) -> None:
    key_down(vk, scancode)
    time.sleep(hold)
    key_up(vk, scancode)


def move_cursor(x: int, y: int) -> None:
    """直接把光标放到屏幕坐标（不一定能唤醒游戏的鼠标模式，见 `move_cursor_input`）。"""
    user32.SetCursorPos(int(x), int(y))


def move_cursor_input(x: int, y: int) -> None:
    """用 SendInput 发一次**真正的鼠标移动**（绝对坐标）。

    为什么不能用 `SetCursorPos`
    --------------------------
    `SetCursorPos` 只改光标位置，**不产生原始输入事件（WM_INPUT）**。
    这个游戏是 DirectX 应用，判断"鼠标动过"靠的是原始输入；
    只 SetCursorPos 的话，它认为鼠标没动，于是**停留在键盘模式、忽略所有点击**。

    实测：在「巡察」命令对话框里，用 SetCursorPos 定位后点击，
    连续 6 次（含双击、含标签页）画面变化全是 **0.0** —— 点击被完全忽略；
    而同一时刻键盘（方向键/Enter/Esc）完全有效。
    改成发真正的移动事件后，鼠标恢复响应。

    输入事件顺序必须是：**先移动 → 再按下 → 按住 → 抬起**。
    真人也是这样，游戏的状态机就是按这个顺序切模式的。
    """
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    nx = int(round(x * 65535 / max(1, sw - 1)))
    ny = int(round(y * 65535 / max(1, sh - 1)))
    i = INPUT()
    i.type = INPUT_MOUSE
    i.mi = MOUSEINPUT(dx=nx, dy=ny, mouseData=0,
                      dwFlags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE,
                      time=0, dwExtraInfo=0)
    _send(i)


def _mouse(flags: int, data: int = 0) -> INPUT:
    i = INPUT()
    i.type = INPUT_MOUSE
    i.mi = MOUSEINPUT(dx=0, dy=0, mouseData=data, dwFlags=flags, time=0, dwExtraInfo=0)
    return i


def _mouse_move(dx: int, dy: int) -> INPUT:
    """相对移动（用来产生"鼠标动过"的原始输入事件）。"""
    i = INPUT()
    i.type = INPUT_MOUSE
    i.mi = MOUSEINPUT(dx=dx, dy=dy, mouseData=0, dwFlags=MOUSEEVENTF_MOVE,
                      time=0, dwExtraInfo=0)
    return i


def click(x: int, y: int, button: str = "left", settle: float = 0.12,
          hold: float = 0.08) -> None:
    """x, y 为屏幕绝对坐标。

    `hold` 是**按住时长**，默认 80ms —— 这一项不能省。

    早先的实现把 down / up 放进同一次 SendInput 调用，按键持续时间为 0。
    游戏是按帧轮询鼠标状态的，这种"零时长点击"会被整帧漏掉，
    表现就是"有时候点得到、有时候点不到"。真人手点至少按住 50ms。
    实测：分开发出并按住 80ms 后，点击稳定生效。
    """
    move_cursor_input(x, y)
    time.sleep(0.05)
    # 再补一次 1px 的**相对**移动：确保游戏收到"动过"的信号，切回鼠标模式
    _send(_mouse_move(1, 1))
    time.sleep(0.04)
    _send(_mouse_move(-1, -1))
    time.sleep(settle)
    down = MOUSEEVENTF_LEFTDOWN if button == "left" else MOUSEEVENTF_RIGHTDOWN
    up = MOUSEEVENTF_LEFTUP if button == "left" else MOUSEEVENTF_RIGHTUP
    _send(_mouse(down))
    time.sleep(hold)
    _send(_mouse(up))
    time.sleep(settle)


def click_client(hwnd: int, cx: int, cy: int, button: str = "left",
                 settle: float = 0.12, hold: float = 0.08) -> None:
    """按**客户区坐标**点击。

    实测（`scripts/diag_coord.py`）：本机客户区原点在屏幕 (4, 33)，尺寸 1280×960，
    `SetCursorPos(原点+客户区坐标)` 的往返误差为 0 —— 所以这里就是纯加法，
    **不要再乘任何缩放比**。

    客户区坐标的来历必须是可信的：截图（`grab`，屏幕拷贝）的像素坐标 1:1 对应客户区坐标。
    千万不要从"被显示端缩放过的图"上目测坐标 —— 见 `san9/annotate.py`。
    """
    l, t, _, _ = client_rect_screen(hwnd)
    click(l + cx, t + cy, button, settle, hold)


def to_client(sx: int, sy: int, hwnd: int) -> tuple[int, int]:
    """屏幕坐标 → 客户区坐标。当用的坐标系是自洽的，这里就是纯减法。"""
    l, t, _, _ = client_rect_screen(hwnd)
    return int(round(sx - l)), int(round(sy - t))


def drag(from_xy: tuple[int, int], to_xy: tuple[int, int], steps: int = 12) -> None:
    """按住左键拖拽（出征画面里调整武将布阵要用）。"""
    x0, y0 = from_xy
    x1, y1 = to_xy
    move_cursor(x0, y0)
    time.sleep(0.05)
    _send(_mouse(MOUSEEVENTF_LEFTDOWN))
    for i in range(1, steps + 1):
        move_cursor(x0 + (x1 - x0) * i // steps, y0 + (y1 - y0) * i // steps)
        time.sleep(0.02)
    _send(_mouse(MOUSEEVENTF_LEFTUP))
    time.sleep(0.1)


# ---------------------------------------------------------------- 常用键

VK = {
    "esc": 0x1B, "enter": 0x0D, "space": 0x20, "tab": 0x09, "back": 0x08,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73, "F5": 0x74, "F6": 0x75,
    "F7": 0x76, "F8": 0x77, "F9": 0x78, "F10": 0x79, "F11": 0x7A, "F12": 0x7B,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "ctrl": 0x11, "alt": 0x12, "shift": 0x10,
}


# 常见的别名 —— 不同人/不同库对同一个键叫法不一样，**必须显式映射，不能靠兜底**。
VK_ALIAS = {
    "escape": "esc", "return": "enter", "ret": "enter", "cr": "enter",
    "backspace": "back", "pgup": "pageup", "pgdn": "pagedown",
    "arrowup": "up", "arrowdown": "down", "arrowleft": "left", "arrowright": "right",
}


def press(name, times: int = 1, interval: float = 0.12) -> None:
    """按键。**键名认不出就抛异常，绝不静默跳过。**

    ⛔ 2026-09-19 血案：旧实现是
        `vk = VK[name] if name in VK else name`
    键名打错（`"escape"` / `"return"` 而表里是 `"esc"` / `"enter"`）时，
    它把**字符串本身**当虚拟键码一路传给 `MapVirtualKeyW`，
    **不报错、什么都不做**。于是我按了两次 Esc「没反应」，
    还把锅甩给了"SendInput 在这个屏没送到"、"游戏不稳定"——
    真相是这一行兜底。用户手按一次就退出了，当场打脸。

    规矩：名字必须在 `VK` 或 `VK_ALIAS` 里；数字 vk 直接放行；其余 `KeyError`。
    """
    if isinstance(name, str):
        key = name.strip()
        key = VK_ALIAS.get(key.lower(), key)
        if key not in VK:
            raise KeyError(
                "认不出的键名 %r —— 可用：%s（别名：%s）"
                % (name, ", ".join(sorted(VK)), ", ".join(sorted(VK_ALIAS)))
            )
        vk = VK[key]
    elif isinstance(name, int):
        vk = name
    else:
        raise TypeError("press() 只收键名字符串或 int 虚拟键码，收到 %r" % (name,))
    for _ in range(times):
        key_tap(vk)
        time.sleep(interval)


def press_esc(times: int = 1) -> None:
    """万能逃生键：连按 Esc 回主界面。"""
    press("esc", times, 0.2)
