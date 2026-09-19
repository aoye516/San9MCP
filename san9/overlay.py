"""置顶提示框：纯 Win32 (ctypes) 实现。

为什么不用 tkinter
------------------
受管的 Python 里没装 tkinter。而且就算有，普通窗口会**抢走游戏的焦点** ——
一抢焦点，游戏就收不到后面的点击了。

所以用 `CreateWindowExW` + `WS_EX_TOPMOST | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW`：
置顶、不抢焦点、不进任务栏。正好是标定提示框需要的行为。

注意：所有带字符串参数的 API 都**必须显式声明 argtypes**，
否则 ctypes 会把 Python str 当成窄字符串传，Windows 那边直接乱码或崩溃。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

from . import winio

u = winio.user32
g = winio.gdi32

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

WS_POPUP = 0x80000000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WM_PAINT, WM_DESTROY, WM_ERASEBKGND = 0x000F, 0x0002, 0x0014
SW_SHOWNOACTIVATE = 4
PM_REMOVE = 1
DT_LEFT, DT_WORDBREAK, DT_NOPREFIX = 0x0000, 0x0010, 0x0800
TRANSPARENT, DEFAULT_CHARSET = 1, 1
FONT = "Microsoft YaHei UI"


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.UINT), ("style", wt.UINT), ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON), ("hCursor", wt.HANDLE),
        ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR), ("hIconSm", wt.HICON),
    ]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [("hdc", wt.HDC), ("fErase", wt.BOOL), ("rcPaint", wt.RECT),
                ("fRestore", wt.BOOL), ("fIncUpdate", wt.BOOL),
                ("rgbReserved", ctypes.c_byte * 32)]


# --- 显式声明 argtypes（关键） ---
u.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
u.RegisterClassExW.restype = wt.WORD
u.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                              ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                              wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
u.CreateWindowExW.restype = wt.HWND
u.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
u.DefWindowProcW.restype = ctypes.c_ssize_t
u.DrawTextW.argtypes = [wt.HDC, wt.LPCWSTR, ctypes.c_int,
                        ctypes.POINTER(wt.RECT), wt.UINT]
u.DrawTextW.restype = ctypes.c_int
u.BeginPaint.argtypes = [wt.HWND, ctypes.POINTER(PAINTSTRUCT)]
u.BeginPaint.restype = wt.HDC
u.EndPaint.argtypes = [wt.HWND, ctypes.POINTER(PAINTSTRUCT)]
u.FillRect.argtypes = [wt.HDC, ctypes.POINTER(wt.RECT), wt.HBRUSH]
g.CreateFontW.argtypes = [ctypes.c_int] * 13 + [wt.LPCWSTR]
g.CreateFontW.restype = wt.HANDLE
g.CreateSolidBrush.argtypes = [wt.COLORREF]
g.CreateSolidBrush.restype = wt.HBRUSH
g.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
g.SelectObject.restype = wt.HGDIOBJ
g.DeleteObject.argtypes = [wt.HGDIOBJ]   # 不声明的话 64 位句柄会被当 c_int 传，报溢出
g.DeleteObject.restype = wt.BOOL
g.SetTextColor.argtypes = [wt.HDC, wt.COLORREF]
g.SetBkMode.argtypes = [wt.HDC, ctypes.c_int]

_BG = 0x001B1B1B        # COLORREF 是 BGR：深灰
_FG_TITLE = 0x0057CFFF  # 金色
_FG_BODY = 0x00F2F2F2
_FG_HINT = 0x008C8C8C

_instances: dict[int, "Overlay"] = {}


def _dispatcher(hwnd, msg, wparam, lparam):
    inst = _instances.get(hwnd)
    if inst is not None:
        return inst.handle(msg, wparam, lparam)
    return u.DefWindowProcW(hwnd, msg, wparam, lparam)


# 类只注册一次，WNDPROC 必须常驻，否则被回收后再收消息会崩
_DISPATCHER = WNDPROC(_dispatcher)
_CLASS_REGISTERED = False


def _ensure_class() -> None:
    global _CLASS_REGISTERED
    if _CLASS_REGISTERED:
        return
    wc = WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wc.lpfnWndProc = _DISPATCHER
    wc.hInstance = winio.kernel32.GetModuleHandleW(None)
    wc.hbrBackground = ctypes.c_void_p(0)
    wc.lpszClassName = "San9OverlayWnd"
    u.RegisterClassExW(ctypes.byref(wc))
    _CLASS_REGISTERED = True


class Overlay:
    """一个置顶、不抢焦点的信息板。"""

    def __init__(self, x: int, y: int, w: int, h: int):
        self.x, self.y, self.w, self.h = x, y, w, h
        self.title = ""
        self.body = ""
        self.hint = "F11 跳过本项        F12 结束"
        _ensure_class()
        hinst = winio.kernel32.GetModuleHandleW(None)
        self.hwnd = u.CreateWindowExW(
            WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            "San9OverlayWnd", "san9", WS_POPUP,
            x, y, w, h, None, None, hinst, None,
        )
        if not self.hwnd:
            raise RuntimeError(f"创建提示框失败，GetLastError={ctypes.get_last_error()}")
        _instances[self.hwnd] = self
        u.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
        u.UpdateWindow(self.hwnd)

    # ---------------------------------------------------------- 消息与绘制

    def handle(self, msg, wparam, lparam):
        if msg == WM_PAINT:
            self._paint()
            return 0
        if msg == WM_ERASEBKGND:
            return 1
        if msg == WM_DESTROY:
            _instances.pop(self.hwnd, None)
            return 0
        return u.DefWindowProcW(self.hwnd, msg, wparam, lparam)

    def _font(self, size: int, weight: int):
        return g.CreateFontW(-size, 0, 0, 0, weight, 0, 0, 0, DEFAULT_CHARSET,
                             0, 0, 0, 0, FONT)

    def _paint(self) -> None:
        ps = PAINTSTRUCT()
        hdc = u.BeginPaint(self.hwnd, ctypes.byref(ps))
        rc = wt.RECT(0, 0, self.w, self.h)
        brush = g.CreateSolidBrush(_BG)
        u.FillRect(hdc, ctypes.byref(rc), brush)
        g.DeleteObject(brush)
        g.SetBkMode(hdc, TRANSPARENT)

        def draw(text: str, font, color, top: int, height: int) -> None:
            r = wt.RECT(24, top, self.w - 24, top + height)
            g.SetTextColor(hdc, color)
            old = g.SelectObject(hdc, font)
            u.DrawTextW(hdc, text or "", -1, ctypes.byref(r),
                        DT_LEFT | DT_WORDBREAK | DT_NOPREFIX)
            g.SelectObject(hdc, old)

        f_t, f_b, f_h = self._font(25, 700), self._font(21, 400), self._font(16, 400)
        draw(self.title, f_t, _FG_TITLE, 20, 62)
        draw(self.body, f_b, _FG_BODY, 92, self.h - 156)
        draw(self.hint, f_h, _FG_HINT, self.h - 48, 30)
        for f in (f_t, f_b, f_h):
            g.DeleteObject(f)
        u.EndPaint(self.hwnd, ctypes.byref(ps))

    # ---------------------------------------------------------- 对外

    def set(self, title: str, body: str) -> None:
        self.title, self.body = title, body
        if self.hwnd:
            u.InvalidateRect(self.hwnd, None, True)
            u.UpdateWindow(self.hwnd)

    def pump(self) -> None:
        """非阻塞处理窗口消息 —— 主循环里每轮调一次。"""
        msg = wt.MSG()
        while u.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
            u.TranslateMessage(ctypes.byref(msg))
            u.DispatchMessageW(ctypes.byref(msg))

    def destroy(self) -> None:
        if self.hwnd:
            _instances.pop(self.hwnd, None)
            u.DestroyWindow(self.hwnd)
            self.hwnd = 0
