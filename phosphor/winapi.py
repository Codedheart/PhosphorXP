"""Thin Windows helpers (ctypes, user32 only).

Everything here is the same kind of call any screen recorder, OBS or Discord
makes: list top-level window titles, read a window's position, change our OWN
window's styles, register global hotkeys. Nothing opens the game process,
reads its memory, injects code, hooks input, or sends keys/clicks.

On non-Windows platforms every function degrades to a harmless no-op so the
logic and tests can run anywhere.
"""
from __future__ import annotations

import sys
import threading
from typing import Callable

IS_WIN = sys.platform == "win32"

if IS_WIN:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)

    GWL_EXSTYLE = -20
    WS_EX_LAYERED = 0x00080000
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_NOACTIVATE = 0x08000000
    WS_EX_APPWINDOW = 0x00040000
    WDA_NONE = 0x0
    WDA_EXCLUDEFROMCAPTURE = 0x11
    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012
    MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x4000

    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    H, I, U, L = wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.LPARAM
    for _name, _args, _res in (
        ("IsWindowVisible", [H], wintypes.BOOL),
        ("IsWindow", [H], wintypes.BOOL),
        ("IsIconic", [H], wintypes.BOOL),
        ("GetWindowTextLengthW", [H], I),
        ("GetWindowTextW", [H, wintypes.LPWSTR, I], I),
        ("GetClassNameW", [H, wintypes.LPWSTR, I], I),
        ("GetClientRect", [H, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
        ("ClientToScreen", [H, ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
        ("GetAncestor", [H, U], H),
        ("SetWindowDisplayAffinity", [H, wintypes.DWORD], wintypes.BOOL),
        ("EnumWindows", [EnumWindowsProc, L], wintypes.BOOL),
        ("RegisterHotKey", [H, I, U, U], wintypes.BOOL),
        ("UnregisterHotKey", [H, I], wintypes.BOOL),
        ("PostThreadMessageW", [wintypes.DWORD, U, wintypes.WPARAM, L], wintypes.BOOL),
        ("SetForegroundWindow", [H], wintypes.BOOL),
        ("GetSystemMetrics", [I], I),
    ):
        _f = getattr(user32, _name)
        _f.argtypes, _f.restype = _args, _res


def set_dpi_aware() -> None:
    """Work in physical pixels so window rects, Tk and the screen grab agree."""
    if not IS_WIN:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


# ---------------------------------------------------------------- game window
def find_window(title_parts: list[str], exclude_titles: tuple[str, ...] = (),
                exclude_classes: tuple[str, ...] = ()) -> int | None:
    """First visible top-level window whose title contains ``title_parts`` (in priority order)
    and whose window class isn't excluded (browsers, Explorer...)."""
    if not IS_WIN:
        return None
    parts = [p.lower() for p in title_parts if p]
    hits: dict[int, int] = {}          # part index -> hwnd

    def cb(hwnd, _lp):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            n = user32.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            title = buf.value
            if title in exclude_titles:
                return True
            low = title.lower()
            for i, p in enumerate(parts):
                if p in low and i not in hits:
                    cls = ctypes.create_unicode_buffer(256)
                    user32.GetClassNameW(hwnd, cls, 256)
                    if not cls.value.startswith(exclude_classes):
                        hits[i] = hwnd
                    break
        except Exception:
            pass
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return hits[min(hits)] if hits else None


def virtual_screen() -> tuple[int, int, int, int] | None:
    """(left, top, width, height) spanning all monitors."""
    if not IS_WIN:
        return None
    g = user32.GetSystemMetrics
    return g(76), g(77), g(78), g(79)


def foreground(tk_hwnd: int) -> None:
    if IS_WIN:
        user32.SetForegroundWindow(_top(tk_hwnd))


def client_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """(left, top, width, height) of the window's client area in screen pixels."""
    if not IS_WIN or not hwnd or not user32.IsWindow(hwnd) or user32.IsIconic(hwnd):
        return None
    rc = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rc)):
        return None
    pt = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    w, h = rc.right - rc.left, rc.bottom - rc.top
    if w <= 0 or h <= 0:
        return None
    return pt.x, pt.y, w, h


# ------------------------------------------------------------ our own window
def _top(hwnd_from_tk: int) -> int:
    # Tk's winfo_id is a child; the real top-level is its root ancestor
    return user32.GetAncestor(hwnd_from_tk, 2) or hwnd_from_tk  # GA_ROOT


def set_overlay_styles(tk_hwnd: int, click_through: bool) -> None:
    """No taskbar button, never steals focus from the game, optional click-through."""
    if not IS_WIN:
        return
    h = _top(tk_hwnd)
    ex = user32.GetWindowLongPtrW(h, GWL_EXSTYLE)
    ex |= WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_LAYERED
    ex &= ~WS_EX_APPWINDOW
    if click_through:
        ex |= WS_EX_TRANSPARENT
    else:
        ex &= ~WS_EX_TRANSPARENT
    user32.SetWindowLongPtrW(h, GWL_EXSTYLE, ex)


def exclude_from_capture(tk_hwnd: int, on: bool) -> bool:
    """Hide our window from screen capture (Win10 2004+), so it can sit over the HUD."""
    if not IS_WIN:
        return False
    return bool(user32.SetWindowDisplayAffinity(_top(tk_hwnd), WDA_EXCLUDEFROMCAPTURE if on else WDA_NONE))


# -------------------------------------------------------------------- hotkeys
_VK = {**{chr(c): c for c in range(0x30, 0x3A)}, **{chr(c): c for c in range(0x41, 0x5B)},
       **{f"F{i}": 0x6F + i for i in range(1, 13)}}


def parse_hotkey(spec: str) -> tuple[int, int] | None:
    """'ctrl+alt+p' -> (mods, vk)."""
    if not IS_WIN or not spec:
        return None
    mods, vk = MOD_NOREPEAT, None
    for part in spec.lower().replace(" ", "").split("+"):
        if part in ("ctrl", "control"):
            mods |= MOD_CONTROL
        elif part == "alt":
            mods |= MOD_ALT
        elif part == "shift":
            mods |= MOD_SHIFT
        else:
            vk = _VK.get(part.upper())
    return (mods, vk) if vk else None


class Hotkeys:
    """RegisterHotKey on a background thread with its own message loop."""

    def __init__(self, bindings: dict[str, str], on_fire: Callable[[str], None]) -> None:
        self.bindings = bindings
        self.on_fire = on_fire
        self.failed: list[str] = []
        self.ready = threading.Event()
        self._tid = None
        self._thread = None
        if IS_WIN:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        else:
            self.failed = list(bindings)
            self.ready.set()

    def _run(self) -> None:
        self._tid = ctypes.windll.kernel32.GetCurrentThreadId()
        ids = {}
        for i, (name, spec) in enumerate(self.bindings.items(), start=1):
            hk = parse_hotkey(spec)
            if hk and user32.RegisterHotKey(None, i, hk[0], hk[1]):
                ids[i] = name
            elif spec:
                self.failed.append(name)
        self.ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam in ids:
                self.on_fire(ids[msg.wParam])
        for i in ids:
            user32.UnregisterHotKey(None, i)

    def stop(self) -> None:
        if IS_WIN and self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)


def add_private_font(path: str) -> bool:
    """Make a bundled .ttf available to this process only (no install)."""
    if not IS_WIN:
        return False
    try:
        return ctypes.windll.gdi32.AddFontResourceExW(path, 0x10, 0) > 0  # FR_PRIVATE
    except Exception:
        return False
