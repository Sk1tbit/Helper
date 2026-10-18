"""Пульт Джарвиса — веб-панель на http://127.0.0.1:8765 (только для этого компьютера).

Виджеты: часы, погода, курсы, задачи, календарь, заметки, состояние ПК, шаги агента с кнопкой «Стоп»,
чат, быстрый запуск программ, музыка, умный дом, РР4, аватар. Столы (вкладки) собираются из шаблонов
и хранятся в memory/desks.json. Всё на стандартной библиотеке, без фреймворков.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections import deque
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import learning

WIDGETS = {
    "clock": "Часы", "weather": "Погода", "rates": "Курсы валют", "tasks": "Задачи", "calendar": "Календарь",
    "notes": "Заметки", "system": "Компьютер", "agent": "Агент", "chat": "Чат", "apps": "Быстрый запуск",
    "media": "Музыка", "home": "Умный дом", "fishing": "РР4: точки", "avatar": "Аватар",
}
TEMPLATES = {
    "Пустой": [],
    "Утро": ["clock", "weather", "rates", "calendar", "tasks"],
    "Работа": ["tasks", "calendar", "notes", "agent", "chat"],
    "Учёба": ["notes", "tasks", "clock", "chat"],
    "Время и заметки": ["clock", "notes", "calendar"],
    "Финансы": ["rates", "notes", "chat"],
    "Медиа": ["media", "apps", "avatar"],
    "Игры": ["fishing", "system", "media", "chat"],
    "Компьютер": ["system", "apps", "agent", "chat"],
    "Умный дом": ["home", "chat"],
    "Питомец": ["avatar", "clock", "tasks"],
    "Только аватар": ["avatar"],
}
DEFAULT_DESKS = [{"id": "main", "name": "Главный",
                  "widgets": ["clock", "weather", "rates", "tasks", "calendar", "agent", "chat", "apps"]}]
DEFAULT_APPS = ["Telegram", "Яндекс Браузер", "Steam", "Discord", "Проводник", "Калькулятор"]
MEDIA = {"play": "play_pause", "next": "next", "prev": "previous", "up": "volume_up", "down": "volume_down",
         "mute": "mute"}


def _desks_path() -> Path:
    return learning.MEMORY_DIR / "desks.json"


def load_desks() -> list[dict]:
    try:
        desks = json.loads(_desks_path().read_text(encoding="utf-8"))
        if isinstance(desks, list) and all(isinstance(d, dict) and "name" in d for d in desks):
            return desks
    except (OSError, ValueError):
        pass
    return json.loads(json.dumps(DEFAULT_DESKS))


def save_desks(desks: list[dict]) -> list[dict]:
    clean = []
    for d in desks[:30]:
        widgets = [w for w in d.get("widgets", []) if w in WIDGETS][:24]
        clean.append({"id": str(d.get("id") or uuid.uuid4().hex[:8])[:20], "name": str(d.get("name", "Стол"))[:40],
                      "widgets": widgets})
    path = _desks_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    return clean


class Panel:
    """run(text) — выполнить команду как сказанную голосом; stop() — прервать агента."""

    def __init__(self, cfg, brain, run, stop, port: int | None = None):
        self.cfg = cfg
        self.brain = brain
        self.run_command = run
        self.stop_command = stop
        self.port = cfg.panel_port if port is None else port
        self.chat: deque = deque(maxlen=200)
        self._seq = 0
        self._lock = threading.Lock()
        self.agent = {"active": False, "request": "", "steps": [], "status": "idle"}
        self.state = "wake"
        self.server: ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    # --- события из помощника ----------------------------------------------------

    def log(self, who: str, text: str) -> None:
        with self._lock:
            self._seq += 1
            self.chat.append({"n": self._seq, "who": who, "text": text, "t": time.strftime("%H:%M")})

    def on_event(self, kind: str, data: dict) -> None:
        with self._lock:
            if kind == "start":
                self.agent = {"active": True, "request": data.get("request", ""), "steps": [], "status": "работаю"}
            elif kind == "step":
                self.agent["steps"].append({"n": data.get("n"), "text": data.get("text", ""), "error": False})
            elif kind == "step_error" and self.agent["steps"]:
                self.agent["steps"][-1]["error"] = True
            elif kind in ("done", "cancelled"):
                self.agent["active"] = False
                self.agent["status"] = "остановлен" if kind == "cancelled" else "готово"

    # --- данные виджетов ---------------------------------------------------------

    def widgets(self) -> dict:
        from .rates import rates, system_data, weather_data

        org = self.brain.organizer
        today = date.today()
        out: dict = {
            "tasks": org.list_tasks(include_done=True)[-50:],
            "notes": list(reversed(org.data["notes"][-30:])),
            "events": [dict(e, human=_human(e["date"], today)) for e in org.upcoming(30)],
            "apps": list(getattr(self.cfg, "panel_apps", None) or DEFAULT_APPS),
            "home": None,
        }
        for key, fn in (("weather", lambda: weather_data(self.cfg.city)), ("rates", rates), ("system", system_data)):
            try:
                out[key] = fn()
            except Exception as e:  # noqa: BLE001 — нет сети/модуля — виджет просто пустой
                out[key] = None
                print(f"[panel] {key}: {e}")
        home = self.brain.toolbox.home
        if home is not None:
            try:
                out["home"] = home.devices()[:40]
            except Exception as e:  # noqa: BLE001
                out["home"] = {"error": str(e)}
        return out

    def snapshot(self, since: int) -> dict:
        with self._lock:
            return {"chat": [m for m in self.chat if m["n"] > since], "agent": json.loads(json.dumps(self.agent)),
                    "state": self.state, "name": self.cfg.assistant_name,
                    "recording": bool(getattr(self.brain, "recorder", None) and self.brain.recorder.active)}

    # --- действия ----------------------------------------------------------------

    def action(self, path: str, body: dict) -> dict:
        org = self.brain.organizer
        text = str(body.get("text", "")).strip()
        if path == "/api/say":
            if not text:
                raise ValueError("пусто")
            self.log("you", text)
            threading.Thread(target=self.run_command, args=(text,), daemon=True).start()
            return {"ok": True}
        if path == "/api/stop":
            self.stop_command()
            return {"ok": True}
        if path == "/api/task":
            return {"ok": True, "msg": org.add_task(text)}
        if path == "/api/task/done":
            return {"ok": True, "msg": org.set_done(str(body.get("id")), bool(body.get("done", True)))}
        if path == "/api/note":
            return {"ok": True, "msg": org.add_note(text)}
        if path == "/api/event":
            when = body.get("date") or None
            return {"ok": True, "msg": org.add_event(text, when, body.get("time") or None)}
        if path == "/api/delete":
            return {"ok": True, "msg": org.delete(str(body.get("kind")), str(body.get("id")))}
        if path == "/api/open":
            return {"ok": True, "msg": self.brain.toolbox.run("open_app", {"name": text})}
        if path == "/api/media":
            action = MEDIA.get(str(body.get("action")))
            if not action:
                raise ValueError("нет такой кнопки")
            return {"ok": True, "msg": self.brain.toolbox.run("media", {"action": action})}
        if path == "/api/home":
            return {"ok": True, "msg": self.brain.toolbox.run("smart_home", {"action": "toggle", "name": text})}
        if path == "/api/desks":
            return {"ok": True, "desks": save_desks(body.get("desks") or [])}
        raise KeyError(path)

    # --- сервер ------------------------------------------------------------------

    def start(self) -> bool:
        panel = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # не засоряем консоль
                pass

            def _host_ok(self) -> bool:
                # Защита от DNS rebinding и чужих сайтов: только наш адрес и только JSON с нашей страницы.
                host = self.headers.get("Host", "")
                if host not in (f"127.0.0.1:{panel.port}", f"localhost:{panel.port}"):
                    return False
                origin = self.headers.get("Origin")
                return origin in (None, f"http://{host}")

            def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8") -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, code: int, data) -> None:
                self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"))

            def do_GET(self):
                if not self._host_ok():
                    return self._send(403, b"forbidden", "text/plain")
                url = urlparse(self.path)
                if url.path in ("/", "/index.html"):
                    return self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
                if url.path == "/api/state":
                    since = int((parse_qs(url.query).get("since") or ["0"])[0] or 0)
                    return self._json(200, panel.snapshot(since))
                if url.path == "/api/widgets":
                    return self._json(200, panel.widgets())
                if url.path == "/api/desks":
                    return self._json(200, {"desks": load_desks(), "templates": TEMPLATES, "widgets": WIDGETS})
                self._send(404, b"not found", "text/plain")

            def do_POST(self):
                if not self._host_ok() or "application/json" not in self.headers.get("Content-Type", ""):
                    return self._send(403, b"forbidden", "text/plain")
                try:
                    length = min(int(self.headers.get("Content-Length") or 0), 1 << 20)
                    body = json.loads(self.rfile.read(length) or b"{}")
                    if not isinstance(body, dict):
                        raise ValueError("ждал объект")
                    return self._json(200, panel.action(urlparse(self.path).path, body))
                except KeyError:
                    return self._json(404, {"ok": False, "error": "нет такого действия"})
                except Exception as e:  # noqa: BLE001 — ошибку показываем в панели
                    return self._json(400, {"ok": False, "error": str(e)})

        try:
            self.server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        except OSError as e:
            print(f"[panel] порт {self.port} занят ({e}) — пульт не запущен. Поменяй panel_port.")
            return False
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        print(f"[panel] пульт: {self.url}")
        return True

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()

    def open(self) -> None:
        import webbrowser

        webbrowser.open(self.url)


def _human(iso: str, today: date) -> str:
    d = date.fromisoformat(iso)
    if d == today:
        return "Сегодня"
    if d == today + timedelta(days=1):
        return "Завтра"
    from .organizer import MONTH_NAMES

    return f"{d.day} {MONTH_NAMES[d.month - 1]}"


PAGE = (Path(__file__).with_name("panel.html")).read_text(encoding="utf-8") if (
    Path(__file__).with_name("panel.html")).exists() else "<h1>panel.html не найден</h1>"
