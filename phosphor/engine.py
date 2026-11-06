"""Background reader: grabs the configured screen regions and decodes them.

Results go to a queue the UI drains; the UI thread owns all the statistics.
Screen capture is a plain desktop grab (mss / GDI BitBlt) of a small rectangle,
the same thing screenshot tools do. The game process is never touched.
"""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass

import numpy as np

from . import winapi
from .reader import Hint, read_exp, read_meso

# Window classes that can carry "MapleStory" in their title but aren't the game
_NOT_GAME = ("Chrome_WidgetWin", "MozillaWindowClass", "CabinetWClass", "Notepad",
             "ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS", "TkTopLevel")


@dataclass
class Result:
    kind: str                  # "exp" | "meso" | "status"
    t: float                   # time.monotonic()
    value: object = None       # ExpReading | int | str
    ok: bool = False


class Engine(threading.Thread):
    def __init__(self, cfg, out: "queue.Queue[Result]") -> None:
        super().__init__(daemon=True, name="phosphor-engine")
        self.cfg = cfg
        self.out = out
        self._stop = threading.Event()
        self.hold = threading.Event()     # set while the region picker is open
        self._lock = threading.Lock()
        self._hints: dict[str, Hint | None] = {"exp": None, "meso": None}
        self._hwnd: int | None = None
        self._next_find = 0.0
        self.last_crop: dict[str, np.ndarray | None] = {"exp": None, "meso": None}
        self.last_text: dict[str, str] = {"exp": "", "meso": ""}
        self.game_rect: tuple[int, int, int, int] | None = None

    # ------------------------------------------------------------ control
    def stop(self) -> None:
        self._stop.set()

    def region_changed(self, kind: str) -> None:
        with self._lock:
            self._hints[kind] = None

    # ------------------------------------------------------------ helpers
    def locate_game(self) -> tuple[int, int, int, int] | None:
        now = time.monotonic()
        rect = winapi.client_rect(self._hwnd) if self._hwnd else None
        if rect is None and now >= self._next_find:
            self._next_find = now + 3.0
            self._hwnd = self._find()
            rect = winapi.client_rect(self._hwnd) if self._hwnd else None
        self.game_rect = rect
        return rect

    def _find(self) -> int | None:
        return winapi.find_window(self.cfg["window_titles"], exclude_titles=("Phosphor XP",),
                                  exclude_classes=_NOT_GAME)

    def abs_rect(self, region: dict | None) -> tuple[int, int, int, int] | None:
        if not region:
            return None
        if region.get("anchor") == "client":
            g = self.game_rect
            if g is None:
                return None
            return g[0] + region["x"], g[1] + region["y"], region["w"], region["h"]
        return region["x"], region["y"], region["w"], region["h"]

    # ---------------------------------------------------------------- loop
    def run(self) -> None:
        # a dead capture thread must never fail silently: log it and tell the overlay
        while not self._stop.is_set():
            try:
                self._loop()
                return
            except Exception as e:
                import traceback
                from .config import data_dir
                try:
                    with (data_dir() / "error.log").open("a", encoding="utf-8") as f:
                        f.write(time.strftime("%Y-%m-%d %H:%M:%S engine: ") + traceback.format_exc())
                except OSError:
                    pass
                self.out.put(Result("status", time.monotonic(), f"capture error: {e} (see error.log)"))
                self._stop.wait(5.0)   # retry rather than die

    def _loop(self) -> None:
        due = {"exp": 0.0, "meso": 0.0}
        with new_mss() as sct:   # one instance per thread
            while not self._stop.is_set():
                if self.hold.is_set():
                    self._stop.wait(0.2)
                    continue
                now = time.monotonic()
                game = self.locate_game()
                for kind in ("exp", "meso"):
                    if now < due[kind]:
                        continue
                    due[kind] = now + float(self.cfg[f"{kind}_interval"])
                    region = self.cfg[f"{kind}_region"]
                    rect = self.abs_rect(region)
                    if rect is None:
                        if region and region.get("anchor") == "client" and game is None:
                            self.out.put(Result("status", now, "game window not found"))
                        continue
                    try:
                        img = grab(sct, rect)
                    except Exception as e:  # off-screen, monitor change...
                        self.out.put(Result("status", now, f"capture failed: {e}"))
                        continue
                    self.last_crop[kind] = img
                    with self._lock:
                        hint = self._hints[kind]
                    if kind == "exp":
                        r, hint = read_exp(img, hint)
                        self.last_text[kind] = r.text if r else "(unreadable)"
                        self.out.put(Result("exp", now, r, r is not None))
                    else:
                        v, hint = read_meso(img, hint)
                        self.last_text[kind] = f"{v:,}" if v is not None else "(not visible)"
                        self.out.put(Result("meso", now, v, v is not None))
                    with self._lock:
                        self._hints[kind] = hint
                self._stop.wait(0.1)


def new_mss():
    import mss
    return (getattr(mss, "MSS", None) or mss.mss)()


def grab(sct, rect: tuple[int, int, int, int]) -> np.ndarray:
    x, y, w, h = rect
    shot = sct.grab({"left": int(x), "top": int(y), "width": int(w), "height": int(h)})
    a = np.frombuffer(shot.bgra, dtype=np.uint8).reshape(shot.height, shot.width, 4)
    return a[..., 2::-1].copy()  # BGRA -> RGB
