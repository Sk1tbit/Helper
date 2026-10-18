"""HUD в стиле Джарвиса: полупрозрачная панель поверх окон.

Показывает состояние (ждёт имя / слушает / думает / говорит), что услышал и что ответил.
Работает в своём потоке на tkinter (входит в Python для Windows). Если tkinter нет — молча
ничего не делает. Панель можно перетащить мышкой, двойной клик — свернуть/развернуть.
Поверх игр в полноэкранном режиме Windows её не покажет — только в оконном/«без рамки».
Пока агент работает, показывает «Шаг N: …» и кнопку «■ стоп». Здесь же живёт питомец (pet: true).
"""

from __future__ import annotations

import queue
import threading

from .config import Config

COLORS = {"bg": "#0a1622", "fg": "#9fe8ff", "dim": "#4f7a8c", "accent": "#36d6ff", "user": "#e8f6ff"}
STATES = {
    "wake": ("●", "#4f7a8c", "жду имя"),
    "listen": ("●", "#36d6ff", "слушаю"),
    "think": ("◌", "#ffc94d", "думаю"),
    "speak": ("◉", "#6dff9e", "говорю"),
    "game": ("●", "#b48cff", "игровой режим"),
}


class Hud:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.enabled = cfg.hud or getattr(cfg, "pet", False)
        self.on_stop = None   # кнопка «стоп» на HUD
        self.on_open = None   # двойной клик по питомцу — открыть пульт
        self._q: queue.Queue = queue.Queue()
        if self.enabled:
            threading.Thread(target=self._run, daemon=True).start()

    # --- из любого потока ---------------------------------------------------

    def state(self, name: str) -> None:
        if self.enabled:
            self._q.put(("state", name))

    def user(self, text: str) -> None:
        if self.enabled:
            self._q.put(("user", text))

    def jarvis(self, text: str) -> None:
        if self.enabled:
            self._q.put(("jarvis", text))

    def step(self, text: str | None) -> None:
        """Шаг агента («Шаг 2: Нажимаю …») или None — задача закончена."""
        if self.enabled:
            self._q.put(("step", text))

    # --- поток tkinter ------------------------------------------------------

    def _run(self) -> None:
        try:
            import tkinter as tk
        except ImportError:
            self.enabled = False
            return
        try:
            root = tk.Tk()
        except Exception:  # noqa: BLE001 — нет дисплея и т.п.
            self.enabled = False
            return
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        try:
            root.attributes("-alpha", float(self.cfg.hud_opacity))
        except tk.TclError:
            pass
        root.configure(bg=COLORS["bg"], highlightthickness=1, highlightbackground=COLORS["accent"])
        width = 380
        head = tk.Frame(root, bg=COLORS["bg"])
        head.pack(fill="x", padx=10, pady=(8, 2))
        dot = tk.Label(head, text="●", fg=STATES["wake"][1], bg=COLORS["bg"], font=("Segoe UI", 12))
        dot.pack(side="left")
        title = tk.Label(head, text=f"{self.cfg.assistant_name.upper()}  ·  жду имя", fg=COLORS["accent"],
                         bg=COLORS["bg"], font=("Consolas", 11, "bold"))
        title.pack(side="left", padx=6)
        body = tk.Frame(root, bg=COLORS["bg"])
        body.pack(fill="x", padx=10, pady=(0, 8))
        you = tk.Label(body, text="", fg=COLORS["user"], bg=COLORS["bg"], font=("Segoe UI", 10),
                       wraplength=width - 20, justify="left", anchor="w")
        you.pack(fill="x")
        him = tk.Label(body, text="", fg=COLORS["fg"], bg=COLORS["bg"], font=("Segoe UI", 10),
                       wraplength=width - 20, justify="left", anchor="w")
        him.pack(fill="x")
        steps = tk.Frame(body, bg=COLORS["bg"])
        step_label = tk.Label(steps, text="", fg="#ffc94d", bg=COLORS["bg"], font=("Segoe UI", 9),
                              wraplength=width - 90, justify="left", anchor="w")
        step_label.pack(side="left", fill="x", expand=True)
        stop_btn = tk.Label(steps, text="■ стоп", fg="#ffffff", bg="#c8394a", font=("Segoe UI", 9, "bold"),
                            padx=8, pady=1, cursor="hand2")
        stop_btn.pack(side="right")
        stop_btn.bind("<Button-1>", lambda e: (self.on_stop and self.on_stop(), "break")[1])
        pet = None
        if getattr(self.cfg, "pet", False):
            try:
                from .pet import Pet

                pet = Pet(root, on_open=lambda: self.on_open and self.on_open())
            except Exception as e:  # noqa: BLE001
                print(f"[pet] не запустился: {e}")
        if not self.cfg.hud:
            root.withdraw()  # только питомец

        root.update_idletasks()
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        pos = self.cfg.hud_position
        x = 20 if "left" in pos else sw - width - 20
        y = 20 if "top" in pos else sh - 220
        root.geometry(f"{width}x{root.winfo_reqheight()}+{x}+{y}")

        moved = {"by_user": False}

        def fit() -> None:
            """Подогнать высоту под текст. Внизу экрана растём вверх, чтобы не уехать за край."""
            root.update_idletasks()
            h = root.winfo_reqheight()
            y_now = root.winfo_y()
            if "bottom" in pos and not moved["by_user"]:
                y_now = sh - h - 60
            root.geometry(f"{width}x{h}+{root.winfo_x()}+{y_now}")

        drag = {"x": 0, "y": 0}
        root.bind("<Button-1>", lambda e: drag.update(x=e.x, y=e.y))
        def on_drag(e) -> None:
            moved["by_user"] = True
            root.geometry(f"+{root.winfo_x() + e.x - drag['x']}+{root.winfo_y() + e.y - drag['y']}")

        root.bind("<B1-Motion>", on_drag)
        collapsed = {"on": False}

        def toggle(_e=None):
            collapsed["on"] = not collapsed["on"]
            if collapsed["on"]:
                body.pack_forget()
            else:
                body.pack(fill="x", padx=10, pady=(0, 8))
            fit()

        root.bind("<Double-Button-1>", toggle)

        def short(text: str, n: int = 160) -> str:
            text = " ".join(text.split())
            return text if len(text) <= n else text[: n - 1] + "…"

        def poll() -> None:
            try:
                while True:
                    kind, value = self._q.get_nowait()
                    if kind == "state":
                        mark, color, label = STATES.get(value, STATES["wake"])
                        dot.configure(text=mark, fg=color)
                        title.configure(text=f"{self.cfg.assistant_name.upper()}  ·  {label}")
                        if pet:
                            pet.set_state(value)
                    elif kind == "user":
                        you.configure(text=f"Вы: {short(value)}")
                    elif kind == "jarvis":
                        him.configure(text=short(value, 240))
                        if pet:
                            pet.say(value)
                    elif kind == "step":
                        if value:
                            step_label.configure(text=short(value, 90))
                            steps.pack(fill="x", pady=(4, 0))
                        else:
                            steps.pack_forget()
                    fit()
            except queue.Empty:
                pass
            root.after(80, poll)

        poll()
        root.mainloop()
