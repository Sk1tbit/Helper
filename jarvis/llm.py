"""Подключение к локальной нейросети.

Два вида серверов:
- ollama — родной API Ollama (/api/chat). Умеет задавать размер контекста, поэтому основной вариант.
- openai — любой OpenAI-совместимый сервер: LM Studio, llama.cpp (llama-server), koboldcpp, vLLM...

Сообщения внутри помощника хранятся в одном формате:
  {"role": "system"|"user"|"assistant"|"tool", "content": str,
   "tool_calls": [{"id", "name", "arguments": dict}],   # у assistant
   "tool_call_id": str, "name": str}                     # у tool
и переводятся в формат конкретного сервера здесь.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

import requests

from .config import Config


class LLMError(Exception):
    def __init__(self, message: str, spoken: str = "Нейросеть вернула ошибку, попробуйте ещё раз.",
                 status: int | None = None):
        super().__init__(message)
        self.spoken = spoken  # что сказать вслух
        self.status = status  # HTTP-код ответа сервера, если был


THINK_RE = re.compile(r"<think>.*?</think>", re.S)


def _clean(text: str | None) -> str:
    # Модели-«размышлялки» (qwen3, deepseek-r1) пишут рассуждения в <think> — их не озвучиваем.
    return THINK_RE.sub("", text or "").strip()


def _parse_args(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


class LLM:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.backend = cfg.llm_backend
        self.url = cfg.llm_url.rstrip("/")
        self.session = requests.Session()
        if re.match(r"https?://(localhost|127\.0\.0\.1|\[::1\])", self.url):
            # Локальный сервер — напрямую, мимо системного прокси/VPN, который мог бы его не пропустить.
            self.session.trust_env = False
        self._no_think_ok: bool | None = None  # None — ещё не проверяли
        if cfg.llm_api_key:
            self.session.headers["Authorization"] = f"Bearer {cfg.llm_api_key}"

    # --- публичное ----------------------------------------------------------

    def chat(self, messages: list[dict], tools: list[dict] | None = None, model: str | None = None) -> dict:
        """Возвращает {"content": str, "tool_calls": [{"id", "name", "arguments"}]}."""
        model = model or self.cfg.model
        if self.backend == "ollama":
            reply = self._chat_ollama(messages, tools, model)
        else:
            reply = self._chat_openai(messages, tools, model)
        if tools and not reply["tool_calls"] and reply["content"]:
            # Сервер не распознал вызов, и модель написала его текстом — разберём сами.
            names = {t["name"] for t in tools}
            calls, rest = extract_text_tool_calls(reply["content"], names)
            if calls:
                print(f"[llm] вызов инструмента пришёл текстом, разобрал: {[c['name'] for c in calls]}")
                reply = {"content": rest, "tool_calls": calls}
        return reply

    def describe_image(self, image_b64: str, prompt: str) -> str:
        """Вопрос мультимодальной модели (vision_model) про картинку."""
        model = self.cfg.vision_model or self.cfg.model
        if self.backend == "ollama":
            msg = {"role": "user", "content": prompt, "images": [image_b64]}
            data = self._post("/api/chat", {"model": model, "messages": [msg], "stream": False,
                                            "options": self._options(), "keep_alive": self.cfg.keep_alive})
            return _clean(data.get("message", {}).get("content"))
        msg = {"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
        ]}
        data = self._post("/v1/chat/completions", {"model": model, "messages": [msg],
                                                   "temperature": self.cfg.temperature})
        return _clean(data["choices"][0]["message"].get("content"))

    def check(self) -> str | None:
        """Проверка, что сервер жив и модели есть. Возвращает текст проблемы или None.

        Если в конфиге перепутан тип сервера или адрес (например, указан порт Ollama, а модель
        загружена в LM Studio), ищет модель на стандартных адресах и переключается туда."""
        wanted = [m for m in (self.cfg.model, self.cfg.vision_model) if m]
        urls = [self.url] + [u for u in ("http://localhost:1234", "http://localhost:11434") if u != self.url]
        found_servers: list[tuple[str, str, list[str]]] = []
        for url in urls:
            # Сначала спрашиваем как Ollama: её /api/tags отличает Ollama от LM Studio
            # (Ollama тоже отвечает на /v1/models, и по нему их не различить).
            for kind in ("ollama", "openai"):
                models = self._list_models(kind, url)
                if models is None:
                    continue
                found_servers.append((kind, url, models))
                if all(self._has(kind, models, m) for m in wanted):
                    if (kind, url) != (self.backend, self.url):
                        name = "LM Studio" if kind == "openai" else "Ollama"
                        print(f"[i] Нужная модель нашлась в {name} по адресу {url} — подключаюсь туда. "
                              f"Чтобы не видеть это сообщение, поставь в config.yaml: "
                              f"llm_backend: {kind} и llm_url: {url}")
                        self.backend, self.url = kind, url
                    return None
                break  # сервер ответил, но модели нет — смотрим следующий адрес
        if not found_servers:
            return (f"Не могу подключиться к нейросети ({self.url}). Запусти LM Studio и включи сервер "
                    f"(вкладка Developer → Start Server) или запусти Ollama.")
        missing = ", ".join(wanted)
        listing = "; ".join(
            f"{'LM Studio' if k == 'openai' else 'Ollama'} ({u}): "
            + (", ".join(m for m in ms if "embed" not in m.lower()) or "моделей нет")
            for k, u, ms in found_servers
        )
        return f"Не нашёл модель {missing}. Что есть: {listing}. Впиши нужное имя в config.yaml (model и vision_model)."

    def _list_models(self, kind: str, url: str) -> list[str] | None:
        if kind == "ollama":
            saved, self.url = self.url, url
            try:
                names = self._ollama_models()
            finally:
                self.url = saved
            return sorted(names) if names is not None else None
        return self._openai_models(url)

    @staticmethod
    def _has(kind: str, models: list[str], model: str) -> bool:
        return model in models or (kind == "ollama" and f"{model}:latest" in models)

    def _ollama_models(self) -> set[str] | None:
        try:
            data = self.session.get(f"{self.url}/api/tags", timeout=5).json()
        except (requests.RequestException, ValueError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("models"), list):
            return None
        return {m.get("name", "") for m in data["models"]}

    def _openai_models(self, url: str) -> list[str] | None:
        try:
            r = self.session.get(f"{url.rstrip('/')}/v1/models", timeout=5)
            r.raise_for_status()
            data = r.json()
        except (requests.RequestException, ValueError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            return None
        return [m.get("id", "") for m in data["data"]]

    # --- Ollama -------------------------------------------------------------

    def _options(self) -> dict:
        return {"num_ctx": int(self.cfg.num_ctx), "temperature": self.cfg.temperature}

    def _chat_ollama(self, messages: list[dict], tools: list[dict] | None, model: str) -> dict:
        wire = []
        for m in messages:
            if m["role"] == "assistant":
                item = {"role": "assistant", "content": m.get("content", "")}
                if m.get("tool_calls"):
                    item["tool_calls"] = [
                        {"function": {"name": c["name"], "arguments": c["arguments"]}} for c in m["tool_calls"]
                    ]
                wire.append(item)
            elif m["role"] == "tool":
                wire.append({"role": "tool", "content": m["content"], "tool_name": m.get("name", "")})
            else:
                wire.append({"role": m["role"], "content": m["content"]})
        body = {"model": model, "messages": wire, "stream": False,
                "options": self._options(), "keep_alive": self.cfg.keep_alive}
        if tools:
            body["tools"] = [_function_schema(t) for t in tools]
        msg = self._post_no_think("/api/chat", body, {"think": False}).get("message", {})
        calls = [
            {"id": uuid.uuid4().hex[:12], "name": c["function"]["name"],
             "arguments": _parse_args(c["function"].get("arguments"))}
            for c in msg.get("tool_calls") or []
        ]
        return {"content": _clean(msg.get("content")), "tool_calls": calls}

    # --- OpenAI-совместимые (LM Studio, llama.cpp...) -----------------------

    def _chat_openai(self, messages: list[dict], tools: list[dict] | None, model: str) -> dict:
        wire = []
        for m in messages:
            if m["role"] == "assistant":
                item: dict[str, Any] = {"role": "assistant", "content": m.get("content", "")}
                if m.get("tool_calls"):
                    item["tool_calls"] = [
                        {"id": c["id"], "type": "function",
                         "function": {"name": c["name"], "arguments": json.dumps(c["arguments"], ensure_ascii=False)}}
                        for c in m["tool_calls"]
                    ]
                wire.append(item)
            elif m["role"] == "tool":
                wire.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
            else:
                wire.append({"role": m["role"], "content": m["content"]})
        body: dict[str, Any] = {"model": model, "messages": wire, "temperature": self.cfg.temperature}
        if tools:
            body["tools"] = [_function_schema(t) for t in tools]
        # Qwen3/3.5 и похожие: просим не «размышлять» перед ответом — так в разы быстрее.
        no_think = {"chat_template_kwargs": {"enable_thinking": False}, "reasoning_effort": "none"}
        msg = self._post_no_think("/v1/chat/completions", body, no_think)["choices"][0]["message"]
        calls = [
            {"id": c.get("id") or uuid.uuid4().hex[:12], "name": c["function"]["name"],
             "arguments": _parse_args(c["function"].get("arguments"))}
            for c in msg.get("tool_calls") or []
        ]
        return {"content": _clean(msg.get("content")), "tool_calls": calls}

    # --- общее --------------------------------------------------------------

    def _post_no_think(self, path: str, body: dict, extra: dict) -> dict:
        """POST с просьбой выключить размышления; если сервер такие поля не понимает — без них."""
        if not self.cfg.disable_thinking or self._no_think_ok is False:
            return self._post(path, body)
        try:
            data = self._post(path, {**body, **extra})
            self._no_think_ok = True
            return data
        except LLMError as e:
            if self._no_think_ok or e.status not in (400, 422):
                raise
            print(f"[llm] сервер не принял отключение размышлений ({e}); работаю без этого")
            self._no_think_ok = False
            return self._post(path, body)

    def _post(self, path: str, body: dict) -> dict:
        try:
            r = self.session.post(self.url + path, json=body, timeout=self.cfg.llm_timeout)
        except requests.ConnectionError as e:
            raise LLMError(f"Нет связи с нейросетью по адресу {self.url}",
                           "Нет связи с нейросетью. Проверьте, что она запущена.") from e
        except requests.Timeout as e:
            raise LLMError("Таймаут запроса к нейросети", "Нейросеть слишком долго думает.") from e
        if r.status_code >= 400:
            try:
                detail = r.json().get("error", r.text)
            except ValueError:
                detail = r.text
            if isinstance(detail, dict):
                detail = detail.get("message", detail)
            detail = str(detail)[:300]
            if "does not support tools" in detail:
                raise LLMError(detail, "Эта модель не умеет управлять компьютером. Выберите модель с поддержкой инструментов.", r.status_code)
            low = detail.lower()
            if "model" in low and ("not found" in low or "not loaded" in low or "no models" in low):
                if self.backend == "ollama":
                    raise LLMError(detail, "Модель не найдена. Скачайте её командой ollama pull.", r.status_code)
                print("[!] Модель не загружена на сервере. В LM Studio: вкладка Developer → загрузи модель "
                      "и скопируй её API identifier в config.yaml (строки model и vision_model).")
                raise LLMError(detail, "Модель не загружена в LM Studio. Загрузите её во вкладке Developer.", r.status_code)
            raise LLMError(f"Ошибка нейросети ({r.status_code}): {detail}", status=r.status_code)
        return r.json()


def _function_schema(tool: dict) -> dict:
    return {"type": "function", "function": {
        "name": tool["name"], "description": tool["description"], "parameters": tool["input_schema"]}}


_TOOL_TAG_RE = re.compile(r"<tool_call>\s*(.*?)\s*(?:</tool_call>|$)", re.S)
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_text_tool_calls(content: str, names: set[str]) -> tuple[list[dict], str]:
    """Ищет вызовы инструментов, которые модель написала текстом.

    Понимает форматы <tool_call>{...}</tool_call> (Qwen, Hermes), [TOOL_CALLS][...] (Mistral),
    JSON в ```блоке``` и просто JSON-объект {"name": ..., "arguments": {...}}.
    Принимает только инструменты из names. Возвращает (вызовы, текст без них)."""
    chunks: list[str] = []
    rest = content
    for regex in (_TOOL_TAG_RE, _FENCE_RE):
        found = regex.findall(rest)
        if found:
            chunks += found
            rest = regex.sub("", rest)
    if "[TOOL_CALLS]" in rest:
        head, _, tail = rest.partition("[TOOL_CALLS]")
        chunks.append(tail)
        rest = head
    stripped = rest.strip()
    if not chunks and stripped.startswith(("{", "[")):
        chunks.append(stripped)
        rest = ""

    calls: list[dict] = []
    for chunk in chunks:
        try:
            data = json.loads(chunk.strip())
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if not isinstance(item, dict):
                continue
            fn = item.get("function") if isinstance(item.get("function"), dict) else item
            name = fn.get("name")
            if name not in names:
                continue
            args = fn.get("arguments", fn.get("parameters", {}))
            calls.append({"id": uuid.uuid4().hex[:12], "name": name, "arguments": _parse_args(args)})
    return calls, (rest.strip() if calls else content)
