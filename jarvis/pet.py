"""Пиксельный питомец на рабочем столе: медуза, которая плавает над панелью задач.

Цвет показывает состояние Джарвиса (ждёт / слушает / думает / говорит), во время ответа над ней
всплывает облачко с текстом. Перетаскивается мышкой, двойной клик — открыть пульт, правый клик — спрятать.
Живёт в потоке HUD (одно окно tkinter на всё приложение).
"""

from __future__ import annotations

import random
import sys

# 0 — прозрачно, 1 — тело, 2 — блик, 3 — глаза, 4 — щупальца
SPRITE = [
    "....111111....",
    "..1111111111..",
    ".111221111111.",
    "11122111111111",
    "11111111111111",
    "11133111133111",
    "11133111133111",
    "11111111111111",
    ".111111111111.",
    "..4..4..4..4..",
    ".4..4..4..4...",
    "..4..4..4..4..",
    "...4..4..4..4.",
]
COLORS = {"wake": "#4fb3d9", "listen": "#36d6ff", "think": "#ffc94d", "speak": "#6dff9e", "game": "#b48cff"}
PIXEL = 6
KEY = "#ff00fe"  # этот цвет Windows делает прозрачным


def _shade(color: str, k: float) -> str:
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(c * k))) for c in (r, g, b))


class Pet:
    def __init__(self, root, on_open=None):
        import tkinter as tk

        self.tk = tk
        self.on_open = on_open
        self.state = "wake"
        self.frame = 0
        self.dx = random.choice((-1, 1))
        self.hidden = False
        w, h = len(SPRITE[0]) * PIXEL, len(SPRITE) * PIXEL + 6
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        bg = KEY if sys.platform == "win32" else "#0a1622"
        if sys.platform == "win32":
            self.win.attributes("-transparentcolor", KEY)
        self.canvas = tk.Canvas(self.win, width=w, height=h, bg=bg, highlightthickness=0)
        self.canvas.pack()
        self.sw, self.sh = root.winfo_screenwidth(), root.winfo_screenheight()
        self.x, self.y = self.sw // 2, self.sh - h - 60
        self.w, self.h = w, h
        self.win.geometry(f"{w}x{h}+{self.x}+{self.y}")
        self.bubble = None
        self._drag = None
        self.canvas.bind("<Button-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._move)
        self.canvas.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag", None))
        self.canvas.bind("<Double-Button-1>", lambda e: self.on_open and self.on_open())
        self.canvas.bind("<Button-3>", lambda e: self.toggle())
        self._tick()

    # --- из потока HUD ----------------------------------------------------------

    def set_state(self, name: str) -> None:
        self.state = name if name in COLORS else "wake"

    def say(self, text: str) -> None:
        tk = self.tk
        if self.bubble:
            self.bubble.destroy()
        text = " ".join(text.split())
        text = text if len(text) <= 120 else text[:119] + "…"
        self.bubble = tk.Toplevel(self.win)
        self.bubble.overrideredirect(True)
        self.bubble.attributes("-topmost", True)
        tk.Label(self.bubble, text=text, bg="#0a1622", fg="#d8f3ff", wraplength=220, justify="left",
                 font=("Segoe UI", 9), padx=8, pady=5, highlightthickness=1, highlightbackground="#36d6ff").pack()
        self._place_bubble()
        bubble = self.bubble
        self.win.after(max(3000, len(text) * 70), lambda: bubble.winfo_exists() and bubble.destroy())

    def toggle(self) -> None:
        self.hidden = not self.hidden
        (self.win.withdraw if self.hidden else self.win.deiconify)()

    # --- внутреннее -------------------------------------------------------------

    def _place_bubble(self) -> None:
        if self.bubble and self.bubble.winfo_exists():
            self.bubble.update_idletasks()
            bw, bh = self.bubble.winfo_reqwidth(), self.bubble.winfo_reqheight()
            bx = max(0, min(self.sw - bw, self.x + self.w // 2 - bw // 2))
            self.bubble.geometry(f"+{bx}+{max(0, self.y - bh - 4)}")

    def _press(self, e) -> None:
        self._drag = (e.x, e.y)

    def _move(self, e) -> None:
        if self._drag:
            self.x = self.win.winfo_x() + e.x - self._drag[0]
            self.y = self.win.winfo_y() + e.y - self._drag[1]
            self.win.geometry(f"+{self.x}+{self.y}")
            self._place_bubble()

    def _draw(self) -> None:
        c = self.canvas
        c.delete("all")
        body = COLORS[self.state]
        bob = [0, 1, 2, 1][self.frame % 4] if self.state != "speak" else [0, 3][self.frame % 2]
        wave = self.frame % 2
        for row, line in enumerate(SPRITE):
            for col, ch in enumerate(line):
                if ch == ".":
                    continue
                if ch == "4":
                    col += wave if row % 2 else -wave  # щупальца шевелятся
                    color = _shade(body, 0.7)
                else:
                    color = {"1": body, "2": "#ffffff", "3": "#08141e"}[ch]
                if ch == "3" and self.state == "think" and row == 5:
                    color = body  # «думает» — прищуривается
                x0, y0 = col * PIXEL, row * PIXEL + bob
                c.create_rectangle(x0, y0, x0 + PIXEL, y0 + PIXEL, fill=color, outline=color)

    def _tick(self) -> None:
        self.frame += 1
        if not self._drag and self.state in ("wake", "game") and self.frame % 3 == 0:
            self.x += self.dx * 2  # тихонько плавает туда-сюда
            if self.x < 10 or self.x > self.sw - self.w - 10 or random.random() < 0.01:
                self.dx = -self.dx
            self.win.geometry(f"+{self.x}+{self.y}")
            self._place_bubble()
        self._draw()
        self.win.after(160 if self.state == "speak" else 260, self._tick)
