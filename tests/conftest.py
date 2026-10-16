"""Общие штуки для тестов: изолированная память, подставные HTTP-серверы нейросети."""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def isolated_memory(tmp_path, monkeypatch):
    """Каждый тест — со своей пустой памятью, макросами и именами."""
    import jarvis.brain as brain
    import jarvis.learning as learning
    import jarvis.macros as macros

    mem = tmp_path / "memory"
    monkeypatch.setattr(learning, "MEMORY_DIR", mem)
    monkeypatch.setattr(learning, "MEMORIES", mem / "memories")
    monkeypatch.setattr(learning, "WAKE_FILE", mem / "wake_words.json")
    monkeypatch.setattr(macros, "MACROS_FILE", mem / "macros.json")
    monkeypatch.setattr(brain, "MEMORY_DIR", mem, raising=False)
    return mem


class FakeServer:
    """HTTP-сервер, который отвечает тем, что вернёт handler(method, path, body)."""

    def __init__(self, handler):
        self.requests: list[tuple[str, str, object]] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def _do(self, method):
                raw = self.rfile.read(int(self.headers.get("content-length") or 0))
                try:
                    body = json.loads(raw) if raw else None
                except ValueError:
                    body = raw
                outer.requests.append((method, self.path, body))
                status, payload = handler(method, self.path, body)
                data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._do("GET")

            def do_POST(self):
                self._do("POST")

            def log_message(self, *a):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def fake_server():
    servers = []

    def make(handler):
        s = FakeServer(handler)
        servers.append(s)
        return s

    yield make
    for s in servers:
        s.close()


def openai_reply(content="", calls=()):
    return {"choices": [{"message": {"role": "assistant", "content": content or None, "tool_calls": [
        {"id": f"c{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a, ensure_ascii=False)}}
        for i, (n, a) in enumerate(calls)] or None}}]}


def ollama_reply(content="", calls=()):
    return {"message": {"role": "assistant", "content": content,
                        "tool_calls": [{"function": {"name": n, "arguments": a}} for n, a in calls]}, "done": True}


@pytest.fixture
def lmstudio(fake_server):
    """Подставной LM Studio: script — список ответов на POST по очереди."""

    def make(script, models=("test-model",)):
        script = list(script)

        def handler(method, path, body):
            if method == "GET":
                if path == "/v1/models":
                    return 200, {"data": [{"id": m} for m in models], "object": "list"}
                return 200, {"error": "Unexpected endpoint"}
            item = script.pop(0) if script else openai_reply("пусто")
            if isinstance(item, tuple):  # (status, payload)
                return item
            return 200, item

        return fake_server(handler)

    return make


@pytest.fixture
def make_brain(lmstudio):
    """Мозг, подключённый к подставному LM Studio. Возвращает (brain, said, server)."""
    from jarvis.brain import Brain
    from jarvis.config import Config

    def make(script=(), **cfg_over):
        server = lmstudio(script)
        cfg = Config()
        cfg.llm_backend, cfg.llm_url, cfg.model, cfg.vision_model = "openai", server.url, "test-model", ""
        cfg.learning = False
        for k, v in cfg_over.items():
            setattr(cfg, k, v)
        said: list[str] = []
        brain = Brain(cfg, say=said.append, confirm=lambda q: True, notify=said.append)
        return brain, said, server

    return make
