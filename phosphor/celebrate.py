"""A little phosphor-terminal level-up celebration, drawn on the overlay canvas."""
from __future__ import annotations

import random

FPS_MS = 33          # ~30 fps
FRAMES = 105         # ~3.5 s
TITLE = "LEVEL UP!"
CHEERS = ("GG!", "NICE!", "DING!", "LET'S GO!", "POG", "+1 LV")


class LevelUpFX:
    def __init__(self, canvas, theme: dict, big_font, small_font, W: int, H: int,
                 levels: int, compact: bool, on_done=None) -> None:
        self.c, self.th = canvas, theme
        self.fb, self.fs = big_font, small_font
        self.W, self.H = W, H
        self.levels = levels
        self.compact = compact
        self.on_done = on_done
        self.frame = 0
        self.parts: list[list] = []     # [item, x, y, vx, vy, age]
        self.cheer = random.choice(CHEERS)
        self._job = None
        self.alive = True
        self._build()
        self._step()

    # ---------------------------------------------------------------- setup
    def _build(self) -> None:
        c, th, W, H = self.c, self.th, self.W, self.H
        self.flash = c.create_rectangle(0, 0, W, H, fill=th["mid"], outline="", tags=("fx",))
        th_h = self.fb.metrics("linespace")
        sub_h = 0 if self.compact else self.fs.metrics("linespace")
        bw = min(W - 24, self.fb.measure(TITLE + "█") + 40)
        bh = th_h + sub_h + 14
        self.box = (W / 2 - bw / 2, H / 2 - bh / 2, W / 2 + bw / 2, H / 2 + bh / 2)
        x0, y0, x1, y1 = self.box
        self.banner = c.create_rectangle(*self.box, fill=th["bg"], outline=th["fg"], width=1,
                                         tags=("fx", "banner"))
        self.banner2 = c.create_rectangle(x0 + 3, y0 + 3, x1 - 3, y1 - 3, outline=th["dim"],
                                          tags=("fx", "banner"))
        ty = y0 + 6
        self.title_glow = [c.create_text(W / 2 + dx, ty + dy, text="", font=self.fb, anchor="n",
                                         fill=th["glow"], tags=("fx", "banner"))
                           for dx, dy in ((-1, 0), (1, 0), (0, 1))]
        self.title = c.create_text(W / 2, ty, text="", font=self.fb, anchor="n", fill=th["fg"],
                                   tags=("fx", "banner"))
        self.sub = None
        if not self.compact:
            lv = f"LV +{self.levels}" if self.levels > 1 else "LEVEL GET"
            self.sub = c.create_text(W / 2, ty + th_h, text="", font=self.fs, anchor="n",
                                     fill=th["mid"], tags=("fx", "banner"))
            self.sub_text = f"* {lv}  ·  {self.cheer} *"
        self._lift()

    def _lift(self) -> None:
        self.c.tag_raise("banner")
        self.c.tag_raise("scan")

    # ------------------------------------------------------------ particles
    def _burst(self, n: int) -> None:
        x0, y0, x1, y1 = self.box
        for _ in range(n):
            side = random.choice((x0 + 6, x1 - 6, self.W / 2))
            x, y = side + random.uniform(-10, 10), y0 + random.uniform(0, 6)
            vx = random.uniform(-3.2, 3.2) + (-1.2 if side == x0 + 6 else 1.2 if side == x1 - 6 else 0)
            vy = random.uniform(-5.0, -1.5)
            ch = random.choice("*+·*o+·.")
            item = self.c.create_text(x, y, text=ch, font=self.fs, fill=self.th["fg"], tags=("fx",))
            self.parts.append([item, x, y, vx, vy, 0])

    def _move_parts(self) -> None:
        fade = (self.th["fg"], self.th["warn"], self.th["mid"], self.th["dim"], self.th["glow"])
        keep = []
        for p in self.parts:
            item, x, y, vx, vy, age = p
            vy += 0.22
            x, y, age = x + vx, y + vy, age + 1
            if age > 38 or y > self.H + 6 or x < -6 or x > self.W + 6:
                self.c.delete(item)
                continue
            self.c.coords(item, x, y)
            self.c.itemconfigure(item, fill=fade[min(len(fade) - 1, age // 8)])
            p[1:] = [x, y, vx, vy, age]
            keep.append(p)
        self.parts = keep

    # ---------------------------------------------------------------- frames
    def _step(self) -> None:
        if not self.alive:
            return
        f, c, th = self.frame, self.c, self.th

        # 1. CRT flash
        if f < 6:
            c.itemconfigure(self.flash, fill=(th["fg"], th["mid"], th["dim"], th["glow"], th["glow"], th["bg"])[f])
        elif f == 6:
            c.delete(self.flash)

        # 2. typewriter title, then blink
        n = min(len(TITLE), max(0, (f - 4) // 2))
        if f < 4 + 2 * len(TITLE) + 2:
            txt = TITLE[:n] + ("█" if f % 4 < 2 else " ")
            col = th["fg"]
        else:
            txt = TITLE
            col = th["warn"] if (f // 5) % 2 else th["fg"]
        c.itemconfigure(self.title, text=txt, fill=col)
        for g in self.title_glow:
            c.itemconfigure(g, text=txt)

        # 3. subtitle types in after the title
        if self.sub is not None:
            k = max(0, f - (6 + 2 * len(TITLE)))
            c.itemconfigure(self.sub, text=self.sub_text[:k])

        # 4. confetti bursts + twinkles
        if f in (8, 22, 36, 52):
            self._burst(14 if not self.compact else 8)
        if 20 < f < 85 and f % 7 == 0:
            x, y = random.uniform(10, self.W - 10), random.uniform(8, self.H - 8)
            tw = c.create_text(x, y, text="+", font=self.fs, fill=th["mid"], tags=("fx",))
            self.parts.append([tw, x, y, 0.0, -0.22, 30])   # short-lived twinkle
        self._move_parts()

        # 5. CRT power-off collapse of the banner
        if f >= FRAMES - 12:
            k = (FRAMES - f) / 12
            x0, y0, x1, y1 = self.box
            cy, cx = (y0 + y1) / 2, (x0 + x1) / 2
            hh = max(1, (y1 - y0) / 2 * k)
            hw = (x1 - x0) / 2 * (1 if k > 0.35 else k / 0.35)
            c.coords(self.banner, cx - hw, cy - hh, cx + hw, cy + hh)
            c.coords(self.banner2, cx - hw, cy - hh, cx + hw, cy + hh)
            if k < 0.6:
                c.itemconfigure(self.title, text="")
                for g in self.title_glow:
                    c.itemconfigure(g, text="")
                if self.sub is not None:
                    c.itemconfigure(self.sub, text="")
                c.itemconfigure(self.banner, fill=th["fg"])

        self._lift()
        self.frame += 1
        if self.frame > FRAMES:
            self.stop()
            return
        self._job = c.after(FPS_MS, self._step)

    def stop(self) -> None:
        if not self.alive:
            return
        self.alive = False
        if self._job:
            try:
                self.c.after_cancel(self._job)
            except Exception:
                pass
        self.c.delete("fx")
        if self.on_done:
            self.on_done()
