"""The always-on-top CRT overlay (Tkinter, stdlib only)."""
from __future__ import annotations

import queue
import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path

import numpy as np
from PIL import Image, ImageTk

from . import winapi
from .config import APP_NAME, Config, append_history
from .engine import Engine, Result, grab, new_mss
from .reader import read_exp, read_meso
from .celebrate import LevelUpFX
from .stats import ActiveClock, ExpTracker, MesoTracker, fmt_dur, fmt_int, fmt_short

from . import themes
W_FULL = 300
ASSETS = Path(__file__).parent / "assets"


class Overlay:
    def __init__(self) -> None:
        winapi.set_dpi_aware()
        self.cfg = Config()
        self.cfg["click_through"] = False   # never start locked out
        self.q: "queue.Queue[Result]" = queue.Queue()
        self.cmds: "queue.Queue[str]" = queue.Queue()
        self.engine = Engine(self.cfg, self.q)

        now = time.monotonic()
        self.clock = ActiveClock(now)
        self.exp = ExpTracker()
        self.meso = MesoTracker()
        self.paused = False
        self.auto_idle = False
        self.last_gain_at = now
        self.status = ""
        self.status_until = 0.0
        self.last_exp_ok_at = 0.0
        self.last_exp_text = ""
        self.meso_seen_at = 0.0
        self.blink = False

        self.root = tk.Tk()
        self.root.report_callback_exception = self._log_exc
        self.root.title(APP_NAME)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self._set_alpha()
        self.root.geometry(f"+{self._clamp_pos(int(self.cfg['x']), int(self.cfg['y']))}")
        self._fonts()

        self.canvas = tk.Canvas(self.root, highlightthickness=0, bd=0, cursor="fleur")
        self.canvas.pack(fill="both", expand=True)
        self.items: dict[str, list[int]] = {}
        self._layout()

        self.canvas.bind("<ButtonPress-1>", self._drag_start)
        self.canvas.bind("<B1-Motion>", self._drag_move)
        self.canvas.bind("<ButtonRelease-1>", self._drag_end)
        self.canvas.bind("<Double-Button-1>", lambda e: self.toggle_compact())
        self.canvas.bind("<Button-3>", self._menu)

        self.debug_win: tk.Toplevel | None = None
        self.fx: LevelUpFX | None = None
        self.hotkeys = winapi.Hotkeys(self.cfg["hotkeys"], self.cmds.put)
        self.root.after_idle(self._after_mapped)
        self.root.after(250, self._tick)
        self.root.after(500, self._blink)
        self.root.after(2000, self._keep_on_top)
        self.root.protocol("WM_DELETE_WINDOW", self.quit)

        if not self.cfg["exp_region"]:
            self.flash("RIGHT-CLICK > SELECT EXP REGION", 3600)

    def _after_mapped(self) -> None:
        # Tk creates the real top-level HWND on first map; style it only after that
        self.root.update_idletasks()
        self.root.wait_visibility()
        self._apply_win_styles()
        self.hotkeys.ready.wait(1.0)
        if self.hotkeys.failed and winapi.IS_WIN:
            self.flash("HOTKEY IN USE: " + ",".join(self.hotkeys.failed).upper(), 8)

    def _log_exc(self, exc, val, tb) -> None:
        import traceback
        from .config import data_dir
        try:
            with (data_dir() / "error.log").open("a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + "".join(traceback.format_exception(exc, val, tb)))
        except OSError:
            pass
        self.flash("INTERNAL ERROR - SEE error.log", 8)

    def _clamp_pos(self, x: int, y: int) -> str:
        vs = winapi.virtual_screen()
        if vs:
            l, t, w, h = vs
            x = min(max(x, l), l + w - 120)
            y = min(max(y, t), t + h - 60)
        return f"{x}+{y}"

    # ---------------------------------------------------------------- setup
    def _fonts(self) -> None:
        ttf = ASSETS / "VT323-Regular.ttf"
        if ttf.exists():
            winapi.add_private_font(str(ttf))
        fams = set(tkfont.families(self.root))
        if "VT323" in fams:
            fam, small, mid, big = "VT323", 15, 17, 30
        else:
            fam = next((f for f in ("Lucida Console", "Consolas", "Courier New", "DejaVu Sans Mono")
                        if f in fams), "TkFixedFont")
            small, mid, big = 9, 10, 19
        self.f_small = tkfont.Font(family=fam, size=small)
        self.f_mid = tkfont.Font(family=fam, size=mid)
        self.f_big = tkfont.Font(family=fam, size=big, weight="bold" if fam != "VT323" else "normal")

    @property
    def th(self) -> dict:
        return themes.get(self.cfg["theme"])

    def _set_alpha(self) -> None:
        # stay < 1.0 so the window remains layered (needed for click-through)
        self.root.attributes("-alpha", min(0.99, max(0.3, float(self.cfg["opacity"]))))

    def _apply_win_styles(self) -> None:
        hwnd = self.root.winfo_id()
        winapi.set_overlay_styles(hwnd, bool(self.cfg["click_through"]))
        self._set_alpha()
        ok = winapi.exclude_from_capture(hwnd, bool(self.cfg["hide_from_capture"]))
        if self.cfg["hide_from_capture"] and winapi.IS_WIN and not ok:
            self.flash("CAN'T HIDE FROM CAPTURE - DON'T COVER HUD", 8)

    def _keep_on_top(self) -> None:
        # some games re-assert their own z-order; nudge ourselves back up
        self.root.after(2000, self._keep_on_top)
        if self.root.state() != "withdrawn":
            self.root.attributes("-topmost", True)
            self.root.lift()

    # --------------------------------------------------------------- layout
    def _text(self, key: str, x: int, y: int, font, anchor="nw", color="fg", glow=True) -> None:
        ids = []
        if glow:
            for dx, dy in ((-1, 0), (1, 0), (0, 1)):
                ids.append(self.canvas.create_text(x + dx, y + dy, text="", font=font, anchor=anchor,
                                                   fill=self.th["glow"]))
        ids.append(self.canvas.create_text(x, y, text="", font=font, anchor=anchor, fill=self.th[color]))
        self.items[key] = ids

    def _set(self, key: str, text: str, color: str | None = None) -> None:
        ids = self.items.get(key)
        if not ids:
            return
        for i in ids:
            if self.canvas.itemcget(i, "text") != text:
                self.canvas.itemconfigure(i, text=text)
        if color:
            self.canvas.itemconfigure(ids[-1], fill=self.th[color])

    def _layout(self) -> None:
        if getattr(self, "fx", None):
            self.fx.stop()
        c, th = self.canvas, self.th
        c.delete("all")
        self.items.clear()
        compact = bool(self.cfg["compact"])
        # width follows the font so Windows display scaling (125%, 150%...) still fits
        W = max(W_FULL, int(self.f_mid.measure("M") * 30))
        L, R = 12, W - 12
        lh = self.f_mid.metrics("linespace")
        sh = self.f_small.metrics("linespace")
        bh = self.f_big.metrics("linespace")
        H = (8 + 2 * lh + 10) if compact else (7 + sh + 2 + sh - 3 + max(bh, 2 * sh) + 4 + 3 * lh + sh + 10)
        self.W, self.H = W, H
        self.root.geometry(f"{W}x{H}")
        c.configure(width=W, height=H, bg=th["bg"])
        c.create_rectangle(1, 1, W - 2, H - 2, outline=th["dim"])
        c.create_rectangle(3, 3, W - 4, H - 4, outline=th["glow"])
        if compact:
            self._text("c1", L, 8, self.f_mid)
            self._text("c1r", R, 8, self.f_mid, anchor="ne", color="mid")
            self._text("c2", L, 8 + lh + 2, self.f_mid, color="mid")
            self._text("c2r", R, 8 + lh + 2, self.f_mid, anchor="ne", color="mid")
        else:
            y = 7
            self._text("title", L, y, self.f_small, color="dim", glow=False)
            self._text("clock", R, y, self.f_small, anchor="ne", color="mid")
            y += sh + 2
            self._text("lbl_rate", L, y, self.f_small, color="dim", glow=False)
            self._text("pct_rate", R, y, self.f_small, anchor="ne", color="mid")
            y += sh - 3
            self._text("rate", L, y, self.f_big)
            # recent pace stacked in the empty space right of the big number
            ry = y + max(0, (bh - 2 * sh) // 2) + 1
            self._text("r5", R, ry, self.f_small, anchor="ne", color="mid")
            self._text("r10", R, ry + sh, self.f_small, anchor="ne", color="mid")
            y += max(bh, 2 * sh)
            c.create_line(L, y, R, y, fill=th["glow"])
            y += 4
            for left, right in (("gain", "lv"), ("eta", "pos"), ("meso", "mnet")):
                self._text(left, L, y, self.f_mid, color="mid")
                self._text(right, R, y, self.f_mid, anchor="ne", color="mid")
                y += lh
            self._text("status", L, H - 7, self.f_small, anchor="sw", color="dim", glow=False)
        # scanlines over everything
        for yy in range(0, H, 3):
            c.create_line(0, yy, W, yy, fill=th["scan"], stipple="" if winapi.IS_WIN else "gray50",
                          tags=("scan",))
        self._render()

    # ------------------------------------------------------------- drawing
    def _render(self) -> None:
        now = time.monotonic()
        el = self.clock.elapsed(now)
        t = el
        rate = self.exp.rate(t)
        r5 = self.exp.rate(t, 300)
        r10 = self.exp.rate(t, 600)
        eta = self.exp.eta_seconds(rate)   # session EXP/hr
        pct_h = self.exp.pct_of_level(rate)
        mr = self.meso.rates(t)

        if self.paused:
            state = "‖ PAUSED"
        elif self.auto_idle:
            state = "◌ IDLE"
        else:
            state = ("● " if self.blink else "○ ") + fmt_dur(el)
        if self.paused or self.auto_idle:
            state += " " + fmt_dur(el)

        if self.cfg["compact"]:
            self._set("c1", f"XP/H {fmt_short(rate)}")
            self._set("c1r", f"5M {fmt_short(r5)}  {state.split()[0]}")
            self._set("c2", f"LV IN {fmt_dur(eta)}")
            self._set("c2r", f"$ {fmt_short(mr[0]) if mr else '--'}/H")
            return

        cursor = "█" if self.blink else " "
        self._set("title", f"PHOSPHOR-XP {cursor}")
        self._set("clock", state)
        self._set("lbl_rate", "EXP / HOUR")
        self._set("pct_rate", f"{pct_h:.2f}% LV/H" if pct_h is not None else "")
        self._set("rate", fmt_int(rate))
        self._set("r5", f"5M {fmt_short(r5):>7}")
        self._set("r10", f"10M {fmt_short(r10):>7}")
        g = self.exp.gained if self.exp.started else None
        gp = self.exp.pct_of_level(g)
        self._set("gain", f"GAIN {fmt_int(g)}" + (f" ({gp:.1f}%)" if gp is not None else ""))
        self._set("lv", f"LV +{self.exp.levels}" if self.exp.levels else "")
        self._set("eta", f"NEXT LV {fmt_dur(eta)}")
        self._set("pos", f"@{self.exp.pct:.2f}%" if self.exp.started else "")
        if mr:
            self._set("meso", f"MESO/H {'+' if mr[0] >= 0 else ''}{fmt_short(mr[0])}")
            self._set("mnet", f"NET {fmt_short(self.meso.net)}")
        elif self.cfg["meso_region"]:
            self._set("meso", "MESO/H --" if self.meso.balance is not None else "MESO  OPEN INVENTORY")
            self._set("mnet", "")
        else:
            self._set("meso", "MESO  NO REGION")
            self._set("mnet", "")

        if now < self.status_until:
            self._set("status", self._fit(self.status), "warn")
        elif self.cfg["exp_region"] and now - self.last_exp_ok_at > 10:
            self._set("status", self._fit("> NO EXP READ - RIGHT-CLICK > WHAT IT SEES"), "warn")
        else:
            self._set("status", self._fit(f"> {self.last_exp_text}" if self.last_exp_text else "> READY"), "dim")

    def _fit(self, text: str) -> str:
        room = self.W - 24
        if self.f_small.measure(text) <= room:
            return text
        while text and self.f_small.measure(text + "…") > room:
            text = text[:-1]
        return text + "…"

    def flash(self, msg: str, seconds: float = 5.0) -> None:
        self.status = msg
        self.status_until = time.monotonic() + seconds

    def _blink(self) -> None:
        self.root.after(530, self._blink)
        self.blink = not self.blink

    # ----------------------------------------------------------- main loop
    def _tick(self) -> None:
        try:
            self._tick_body()
        except Exception:
            import sys
            self._log_exc(*sys.exc_info())
        finally:
            self.root.after(250, self._tick)

    def _tick_body(self) -> None:
        while True:
            try:
                cmd = self.cmds.get_nowait()
            except queue.Empty:
                break
            {"pause": self.toggle_pause, "reset": self.reset, "clickthrough": self.toggle_click_through,
             "compact": self.toggle_compact}.get(cmd, lambda: None)()
        while True:
            try:
                r = self.q.get_nowait()
            except queue.Empty:
                break
            self._handle(r)
        self._check_idle()
        self._render()

    def _handle(self, r: Result) -> None:
        if r.kind == "status":
            self.flash(str(r.value).upper(), 3)
            return
        if r.kind == "exp" and r.ok:
            self.last_exp_ok_at = r.t
            self.last_exp_text = f"EXP {r.value.exp:,} [{r.value.pct:.2f}%]"
            if self.paused:
                return
            t = self.clock.elapsed(r.t)
            levels_before = self.exp.levels
            res = self.exp.feed(t, r.value.exp, r.value.pct)
            if self.exp.levels > levels_before:
                self.celebrate()
            if res in ("gain", "level", "jump"):
                self.last_gain_at = r.t
                if self.auto_idle:
                    self.auto_idle = False
                    self.clock.start(r.t)
            if res == "rebased":
                self.flash("EXP JUMP: NEW BASELINE", 6)
        elif r.kind == "meso" and r.ok:
            self.meso_seen_at = r.t
            if not self.paused:
                self.meso.feed(self.clock.elapsed(r.t), int(r.value))

    def _check_idle(self) -> None:
        limit = float(self.cfg["idle_pause_sec"] or 0)
        if limit <= 0 or self.paused or self.auto_idle or not self.exp.started:
            return
        now = time.monotonic()
        idle = now - self.last_gain_at
        if idle > limit:
            self.clock.pause(now)
            self.clock.rewind(idle - limit)   # don't count time beyond the threshold
            self.auto_idle = True

    # -------------------------------------------------------------- actions
    def toggle_pause(self) -> None:
        now = time.monotonic()
        if self.paused:
            self.paused = False
            self.auto_idle = False
            self.exp.rebase()
            self.meso.rebase()
            self.clock.start(now)
            self.last_gain_at = now
            self.flash("RESUMED", 2)
        else:
            self.paused = True
            self.clock.pause(now)

    def reset(self) -> None:
        self._save_history()
        now = time.monotonic()
        self.exp.reset()
        self.meso.reset()
        self.clock.reset(now, running=not self.paused)
        self.auto_idle = False
        self.last_gain_at = now
        self.flash("SESSION RESET", 2)

    def _save_history(self) -> None:
        now = time.monotonic()
        el = self.clock.elapsed(now)
        if el < 60 or not self.exp.started:
            return
        mr = self.meso.rates(el)
        append_history({
            "ended": time.strftime("%Y-%m-%d %H:%M"),
            "duration_min": round(el / 60, 1),
            "exp_gained": int(self.exp.gained),
            "exp_per_hr": int(self.exp.rate(el) or 0),
            "levels": self.exp.levels,
            "meso_net": self.meso.net,
            "meso_per_hr": int(mr[0]) if mr else "",
        })

    def celebrate(self) -> None:
        self.flash(f"*** LEVEL UP *** (+{self.exp.levels} THIS SESSION)", 8)
        if self.fx:
            self.fx.stop()
        big = self.f_mid if self.cfg["compact"] else self.f_big
        self.fx = LevelUpFX(self.canvas, self.th, big, self.f_small, self.W, self.H,
                            max(1, self.exp.levels), bool(self.cfg["compact"]),
                            on_done=lambda: setattr(self, "fx", None))

    def toggle_compact(self) -> None:
        self.cfg["compact"] = not self.cfg["compact"]
        self.cfg.save()
        self._layout()

    def toggle_click_through(self) -> None:
        if not self.cfg["click_through"] and "clickthrough" in self.hotkeys.failed:
            self.flash("NO UNLOCK HOTKEY - CLICK-THROUGH DISABLED", 6)
            return
        self.cfg["click_through"] = not self.cfg["click_through"]
        self.cfg.save()
        self._apply_win_styles()
        hk = self.cfg["hotkeys"].get("clickthrough", "").upper()
        self.flash(f"CLICK-THROUGH {'ON: ' + hk + ' TO UNLOCK' if self.cfg['click_through'] else 'OFF'}", 5)

    def set_cfg(self, key: str, value, relayout: bool = False) -> None:
        self.cfg[key] = value
        self.cfg.save()
        if key == "opacity":
            self._set_alpha()
        if key == "hide_from_capture":
            self._apply_win_styles()
        if relayout:
            self._layout()

    def quit(self) -> None:
        self._save_history()
        if self.root.winfo_viewable():
            self.cfg["x"], self.cfg["y"] = self.root.winfo_rootx(), self.root.winfo_rooty()
        self.cfg.save()
        self.engine.stop()
        self.hotkeys.stop()
        self.root.destroy()

    # ------------------------------------------------------------ dragging
    def _drag_start(self, e) -> None:
        self._dx, self._dy = e.x, e.y

    def _drag_move(self, e) -> None:
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _drag_end(self, _e) -> None:
        if self.root.winfo_viewable():
            self.cfg["x"], self.cfg["y"] = self.root.winfo_rootx(), self.root.winfo_rooty()
        self.cfg.save()

    # ---------------------------------------------------------------- menu
    def _menu(self, e) -> None:
        th = self.th
        m = tk.Menu(self.root, tearoff=0, bg=th["bg"], fg=th["fg"], activebackground=th["glow"],
                    activeforeground=th["fg"], font=self.f_small)
        hk = self.cfg["hotkeys"]
        m.add_command(label=("Resume" if self.paused else "Pause") + f"    [{hk['pause']}]",
                      command=self.toggle_pause)
        m.add_command(label=f"Reset session    [{hk['reset']}]", command=self.reset)
        m.add_separator()
        m.add_command(label="Select EXP region...", command=lambda: self.pick_region("exp"))
        m.add_command(label="Select meso region...", command=lambda: self.pick_region("meso"))
        if self.cfg["meso_region"]:
            m.add_command(label="Clear meso region", command=self._clear_meso)
        m.add_command(label="What it sees...", command=self.show_debug)
        m.add_command(label="Test level-up animation", command=self.celebrate)
        m.add_separator()
        v = tk.BooleanVar(value=self.cfg["compact"])
        m.add_checkbutton(label=f"Compact    [{hk['compact']}]", variable=v, command=self.toggle_compact)
        v2 = tk.BooleanVar(value=self.cfg["click_through"])
        m.add_checkbutton(label=f"Click-through    [{hk['clickthrough']}]", variable=v2,
                          command=self.toggle_click_through)
        v3 = tk.BooleanVar(value=self.cfg["hide_from_capture"])
        m.add_checkbutton(label="Hide overlay from screen capture", variable=v3,
                          command=lambda: self.set_cfg("hide_from_capture", not self.cfg["hide_from_capture"]))

        sub = tk.Menu(m, tearoff=0, bg=th["bg"], fg=th["fg"], activebackground=th["glow"], font=self.f_small)
        ov = tk.DoubleVar(value=round(float(self.cfg["opacity"]), 2))
        for p in (0.5, 0.65, 0.8, 0.9, 0.99):
            sub.add_radiobutton(label=f"{int(round(p * 100))}%", value=p, variable=ov,
                                command=lambda p=p: self.set_cfg("opacity", p))
        m.add_cascade(label="Opacity", menu=sub)

        sub2 = tk.Menu(m, tearoff=0, bg=th["bg"], fg=th["fg"], activebackground=th["glow"], font=self.f_small)
        iv = tk.IntVar(value=int(self.cfg["idle_pause_sec"] or 0))
        for s, lbl in ((0, "Off"), (10, "10 s"), (30, "30 s"), (60, "1 min"), (120, "2 min"), (300, "5 min")):
            sub2.add_radiobutton(label=lbl, value=s, variable=iv,
                                 command=lambda s=s: self.set_cfg("idle_pause_sec", s))
        m.add_cascade(label="Auto-pause when no EXP", menu=sub2)

        sub3, tv = self._palette_menu(m)
        m.add_cascade(label="Color palette", menu=sub3)
        m.add_separator()
        m.add_command(label="Exit", command=self.quit)
        self._menu_vars = (v, v2, v3, ov, iv, tv)  # keep references alive
        winapi.foreground(self.root.winfo_id())
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def _palette_menu(self, parent):
        th = self.th
        sub3 = tk.Menu(parent, tearoff=0, bg=th["bg"], fg=th["fg"], activebackground=th["glow"],
                       activeforeground=th["fg"], font=self.f_small)
        tv = tk.StringVar(value=self.cfg["theme"])
        self._swatches = []
        group = None
        for key, name, grp, col in themes.PALETTES:
            if grp != group:
                group = grp
                sub3.add_command(label=f"-- {grp.upper()} --", state="disabled", font=self.f_small)
            sw = self._swatch(col)
            self._swatches.append(sw)
            # each entry is drawn in its own palette, so the list previews itself
            sub3.add_radiobutton(label=f" {name}  ", image=sw, compound="left", value=key, variable=tv,
                                 font=self.f_mid, background=col["bg"], foreground=col["fg"],
                                 activebackground=col["glow"], activeforeground=col["fg"],
                                 selectcolor=col["fg"],
                                 command=lambda k=key: self.set_cfg("theme", k, relayout=True))
        return sub3, tv

    def _swatch(self, col: dict) -> ImageTk.PhotoImage:
        """Small chip: palette background with its text colours as bars."""
        h = max(12, self.f_mid.metrics("linespace") - 6)
        w = h * 3
        im = Image.new("RGB", (w, h), col["bg"])
        px = im.load()
        bars = (col["fg"], col["mid"], col["warn"])
        bw = (w - 4 * 3) // 3
        for i, c in enumerate(bars):
            rgb = tuple(int(c[j:j + 2], 16) for j in (1, 3, 5))
            x0 = 3 + i * (bw + 3)
            for x in range(x0, x0 + bw):
                for y in range(3, h - 3):
                    px[x, y] = rgb
        edge = tuple(int(col["dim"][j:j + 2], 16) for j in (1, 3, 5))
        for x in range(w):
            px[x, 0] = px[x, h - 1] = edge
        for y in range(h):
            px[0, y] = px[w - 1, y] = edge
        return ImageTk.PhotoImage(im, master=self.root)

    def _clear_meso(self) -> None:
        self.cfg["meso_region"] = None
        self.cfg.save()
        self.meso.reset()

    # ------------------------------------------------------- region picker
    def pick_region(self, kind: str) -> None:
        self.engine.hold.set()
        self.root.withdraw()
        self.root.update_idletasks()
        # let the menu fade and our window disappear before freezing the screen
        self.root.after(300, lambda: self._pick_region(kind))

    def _pick_region(self, kind: str) -> None:
        game = self.engine.locate_game()
        try:
            with new_mss() as sct:
                if game:
                    area = game
                else:
                    mon = sct.monitors[1]
                    area = (mon["left"], mon["top"], mon["width"], mon["height"])
                shot = grab(sct, area)
        except Exception as e:
            self._region_done(kind, None, None, None, False)
            self.flash(f"SCREENSHOT FAILED: {e}", 6)
            return
        RegionPicker(self.root, shot, area, kind, self.th, self.f_mid,
                     on_done=lambda rect: self._region_done(kind, rect, shot, area, game is not None))

    def _region_done(self, kind: str, rect, shot, area, in_game: bool) -> None:
        self.engine.hold.clear()
        self.root.deiconify()
        self.root.attributes("-topmost", True)
        self._apply_win_styles()
        if rect is None:
            return
        x, y, w, h = rect                    # relative to the screenshot/area
        if in_game:
            region = {"anchor": "client", "x": x, "y": y, "w": w, "h": h}
        else:
            region = {"anchor": "screen", "x": area[0] + x, "y": area[1] + y, "w": w, "h": h}
        self.cfg[f"{kind}_region"] = region
        self.cfg.save()
        self.engine.region_changed(kind)
        crop = shot[y:y + h, x:x + w]
        if kind == "exp":
            r, _ = read_exp(crop)
            self.exp.reset() if not self.exp.started else self.exp.rebase()
            if r:
                self.flash(f"EXP REGION OK: {r.exp:,} [{r.pct:.2f}%]", 6)
            else:
                self.flash("CAN'T READ EXP - BOX JUST THE NUMBERS", 10)
        else:
            v, _ = read_meso(crop)
            self.meso.reset()
            self.flash(f"MESO REGION OK: {v:,}" if v is not None else
                       "CAN'T READ MESOS - BOX THE WHITE FIELD", 8)
        if not in_game:
            self.flash("NO GAME WINDOW - SAVED AS SCREEN POS", 8)

    # ---------------------------------------------------------- debug view
    def show_debug(self) -> None:
        if self.debug_win and self.debug_win.winfo_exists():
            self.debug_win.lift()
            return
        th = self.th
        win = tk.Toplevel(self.root, bg=th["bg"])
        win.title("Phosphor XP - what it sees")
        win.attributes("-topmost", True)
        self.debug_win = win
        win.after(200, lambda: win.winfo_exists() and winapi.exclude_from_capture(win.winfo_id(), True))
        labels = {}
        for kind in ("exp", "meso"):
            tk.Label(win, text=kind.upper(), fg=th["dim"], bg=th["bg"], font=self.f_small).pack(anchor="w", padx=8)
            img = tk.Label(win, bg=th["bg"])
            img.pack(anchor="w", padx=8)
            txt = tk.Label(win, fg=th["fg"], bg=th["bg"], font=self.f_mid, justify="left")
            txt.pack(anchor="w", padx=8, pady=(0, 8))
            labels[kind] = (img, txt)
        info = tk.Label(win, fg=th["dim"], bg=th["bg"], font=self.f_small, justify="left")
        info.pack(anchor="w", padx=8, pady=(0, 8))

        def refresh():
            if not win.winfo_exists():
                return
            for kind, (img_l, txt_l) in labels.items():
                crop = self.engine.last_crop.get(kind)
                if crop is not None and crop.size:
                    im = Image.fromarray(crop)
                    k = max(1, min(4, 520 // max(1, im.width)))
                    im = im.resize((im.width * k, im.height * k), Image.NEAREST)
                    ph = ImageTk.PhotoImage(im)
                    img_l.configure(image=ph)
                    img_l.image = ph
                region = self.cfg[f"{kind}_region"]
                txt_l.configure(text=("read: " + self.engine.last_text.get(kind, "")) if region else "no region set")
            g = self.engine.game_rect
            info.configure(text=f"game window: {'%dx%d at %d,%d' % (g[2], g[3], g[0], g[1]) if g else 'not found'}\n"
                                f"level needs ~{fmt_int(self.exp.req_mid)} EXP   rejected/rebased: {self.exp.rejected}")
            win.after(1000, refresh)

        refresh()

    def run(self) -> None:
        self.engine.start()
        self.root.mainloop()


class RegionPicker:
    """Frozen screenshot of the game; drag a box around the text to read."""

    HELP = {
        "exp": "DRAG A BOX AROUND THE EXP NUMBERS, e.g. 68,601 [71.68%]  -  ESC TO CANCEL",
        "meso": "OPEN INVENTORY, THEN DRAG A BOX AROUND THE WHITE MESO FIELD  -  ESC TO CANCEL",
    }

    def __init__(self, root, shot: np.ndarray, area, kind: str, th: dict, font, on_done) -> None:
        self.on_done = on_done
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        x, y, w, h = area
        self.win.geometry(f"{w}x{h}+{x}+{y}")
        dim = (shot.astype(np.float32) * 0.55).astype(np.uint8)
        self.photo = ImageTk.PhotoImage(Image.fromarray(dim))
        self.bright = shot
        self.c = tk.Canvas(self.win, width=w, height=h, highlightthickness=0, cursor="crosshair")
        self.c.pack()
        self.c.create_image(0, 0, image=self.photo, anchor="nw")
        self.c.create_rectangle(8, 8, min(w - 8, 8 + font.measure(self.HELP[kind]) + 16), 12 + font.metrics("linespace") + 6,
                                fill=th["bg"], outline=th["dim"])
        self.c.create_text(16, 12, text=self.HELP[kind], anchor="nw", fill=th["fg"], font=font)
        self.th = th
        self.rect = None
        self.crop_img = None
        self.start = None
        self.c.bind("<ButtonPress-1>", self._down)
        self.c.bind("<B1-Motion>", self._move)
        self.c.bind("<ButtonRelease-1>", self._up)
        self.win.bind("<Escape>", lambda e: self._finish(None))
        self.c.bind("<Button-3>", lambda e: self._finish(None))
        self.win.after(50, lambda: (winapi.foreground(self.win.winfo_id()), self.win.focus_force()))

    def _down(self, e) -> None:
        self.start = (e.x, e.y)

    def _clamp(self, e) -> tuple[int, int]:
        h, w = self.bright.shape[:2]
        return min(max(e.x, 0), w - 1), min(max(e.y, 0), h - 1)

    def _move(self, e) -> None:
        if not self.start:
            return
        e.x, e.y = self._clamp(e)
        x0, y0 = self.start
        if self.rect:
            self.c.delete(self.rect)
        if self.crop_img:
            self.c.delete(self.crop_img)
        l, t, r, b = min(x0, e.x), min(y0, e.y), max(x0, e.x), max(y0, e.y)
        if r - l > 2 and b - t > 2:
            ph = ImageTk.PhotoImage(Image.fromarray(self.bright[t:b, l:r]))
            self._ph = ph
            self.crop_img = self.c.create_image(l, t, image=ph, anchor="nw")
        self.rect = self.c.create_rectangle(l, t, r, b, outline=self.th["fg"], width=1)

    def _up(self, e) -> None:
        if not self.start:
            return
        e.x, e.y = self._clamp(e)
        x0, y0 = self.start
        l, t, r, b = min(x0, e.x), min(y0, e.y), max(x0, e.x), max(y0, e.y)
        if r - l < 8 or b - t < 5:
            self.start = None
            return
        self._finish((l, t, r - l, b - t))

    def _finish(self, rect) -> None:
        self.win.destroy()
        self.on_done(rect)
