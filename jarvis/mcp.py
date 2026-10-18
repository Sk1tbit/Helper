"""Клиент MCP (Model Context Protocol): подключаем внешние серверы инструментов, как в Claude Desktop.

Серверы задаются в config.yaml:
    mcp_servers: ["npx -y @modelcontextprotocol/server-filesystem C:/Users/Я/Documents"]
или файлом в формате Claude Desktop (mcp_config: mcp.json):
    {"mcpServers": {"files": {"command": "npx", "args": ["-y", "..."], "env": {}}}}
Их инструменты отдаются нейросети под именами mcp_<сервер>_<инструмент>.
Работает по stdio (JSON-RPC, по сообщению на строку).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
from pathlib import Path

PROTOCOL = "2025-06-18"


class MCPError(Exception):
    pass


class MCPServer:
    def __init__(self, name: str, command: list[str], env: dict | None = None, timeout: float = 30):
        self.name = name
        self.command = command
        self.env = env or {}
        self.timeout = timeout
        self.tools: list[dict] = []
        self._proc: subprocess.Popen | None = None
        self._id = 0
        self._pending: dict[int, dict] = {}
        self._cond = threading.Condition()
        self._write_lock = threading.Lock()

    # --- транспорт -------------------------------------------------------------

    def start(self) -> None:
        exe = shutil.which(self.command[0]) or self.command[0]  # npx -> npx.cmd на Windows
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0  # type: ignore[attr-defined]
        self._proc = subprocess.Popen(
            [exe, *self.command[1:]], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={**os.environ, **{k: str(v) for k, v in self.env.items()}}, creationflags=flags,
        )
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                    "clientInfo": {"name": "jarvis", "version": "1.0"}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        tools, cursor = [], None
        while True:
            page = self.request("tools/list", {"cursor": cursor} if cursor else {})
            tools += page.get("tools", [])
            cursor = page.get("nextCursor")
            if not cursor:
                break
        self.tools = tools

    def _send(self, msg: dict) -> None:
        if not self._proc or self._proc.poll() is not None:
            raise MCPError(f"сервер {self.name} не запущен")
        with self._write_lock:
            self._proc.stdin.write((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
            self._proc.stdin.flush()

    def _read(self) -> None:
        for raw in self._proc.stdout:
            try:
                msg = json.loads(raw)
            except ValueError:
                continue  # сервер напечатал мусор в stdout
            if "id" in msg and ("result" in msg or "error" in msg):
                with self._cond:
                    self._pending[msg["id"]] = msg
                    self._cond.notify_all()
            elif "id" in msg and "method" in msg:  # запрос от сервера (ping и т.п.) — отвечаем пусто
                try:
                    reply = {"jsonrpc": "2.0", "id": msg["id"]}
                    reply.update({"result": {}} if msg["method"] == "ping" else
                                 {"error": {"code": -32601, "message": "не поддерживается"}})
                    self._send(reply)
                except (MCPError, OSError):
                    pass
        with self._cond:
            self._cond.notify_all()

    def _drain_stderr(self) -> None:
        for raw in self._proc.stderr:
            line = raw.decode("utf-8", "replace").rstrip()
            if line:
                print(f"[mcp:{self.name}] {line[:300]}")

    def request(self, method: str, params: dict) -> dict:
        with self._cond:
            self._id += 1
            rid = self._id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        with self._cond:
            ok = self._cond.wait_for(lambda: rid in self._pending or self._proc.poll() is not None, self.timeout)
            msg = self._pending.pop(rid, None)
        if msg is None:
            raise MCPError(f"{self.name}: нет ответа на {method}" if not ok else f"{self.name}: сервер упал")
        if "error" in msg:
            raise MCPError(f"{self.name}: {msg['error'].get('message', msg['error'])}")
        return msg.get("result") or {}

    def call(self, tool: str, args: dict) -> str:
        result = self.request("tools/call", {"name": tool, "arguments": args or {}})
        parts = []
        for item in result.get("content", []):
            if item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif item.get("type") == "resource":
                parts.append(str(item.get("resource", {}).get("text", "")))
            else:
                parts.append(f"[{item.get('type')}]")
        if not parts and result.get("structuredContent") is not None:
            parts.append(json.dumps(result["structuredContent"], ensure_ascii=False))
        text = "\n".join(parts).strip() or "Готово."
        return f"Ошибка: {text}" if result.get("isError") else text

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()


def _safe(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", text)


def load_specs(servers: list, config_file: str = "", root: Path | None = None) -> list[tuple[str, list[str], dict]]:
    """[(имя, команда, env)] из mcp_servers и файла формата Claude Desktop."""
    specs = []
    for i, line in enumerate(servers or []):
        if not str(line).strip():
            continue
        argv = shlex.split(str(line), posix=sys.platform != "win32")
        argv = [a.strip('"') for a in argv]
        runner = re.match(r"(npx|uvx|bunx|node|deno|python|py|pythonw)\b", Path(argv[0]).name.lower())
        base = Path(argv[-1]).stem if runner and len(argv) > 1 else Path(argv[0]).stem
        name = re.sub(r"^(@[\w-]+/)?(mcp[-_])?(server[-_])?", "", base).split("@")[0] or f"s{i + 1}"
        if any(n == name for n, _, _ in specs):
            name = f"{name}{i + 1}"
        specs.append((name, argv, {}))
    if config_file:
        path = Path(config_file)
        if not path.is_absolute() and root:
            path = root / path
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as e:
            print(f"[mcp] не прочитал {path}: {e}")
            data = {}
        for name, spec in (data.get("mcpServers") or {}).items():
            if spec.get("command") and not spec.get("disabled"):
                specs.append((name, [spec["command"], *spec.get("args", [])], spec.get("env") or {}))
    return specs


class MCPHub:
    """Все MCP-серверы разом: запуск в фоне, схемы для нейросети, вызов по имени."""

    def __init__(self, specs: list[tuple[str, list[str], dict]]):
        self.servers = [MCPServer(n, c, e) for n, c, e in specs]
        self.tools: list[dict] = []          # в формате TOOLS (name, description, input_schema)
        self._route: dict[str, tuple[MCPServer, str]] = {}
        self.ready = threading.Event()

    def start(self, on_ready=None) -> None:
        def boot():
            for server in self.servers:
                try:
                    server.start()
                except Exception as e:  # noqa: BLE001 — один кривой сервер не мешает остальным
                    print(f"[mcp] {server.name} не запустился: {e}")
                    continue
                for t in server.tools:
                    full = f"mcp_{_safe(server.name)}_{_safe(t['name'])}"[:64]
                    self._route[full] = (server, t["name"])
                    self.tools.append({
                        "name": full,
                        "description": f"[{server.name}] {t.get('description', '')}".strip()[:1000],
                        "input_schema": t.get("inputSchema") or {"type": "object", "properties": {}},
                    })
                print(f"[mcp] {server.name}: {len(server.tools)} инструментов")
            self.ready.set()
            if on_ready:
                on_ready(self.tools)
        threading.Thread(target=boot, daemon=True).start()

    def has(self, name: str) -> bool:
        return name in self._route

    def call(self, name: str, args: dict) -> str:
        server, tool = self._route[name]
        try:
            return server.call(tool, args)
        except MCPError as e:
            return f"Ошибка: {e}"

    def stop(self) -> None:
        for s in self.servers:
            s.stop()
