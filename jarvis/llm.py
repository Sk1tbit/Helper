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
    def __init__(self, message: str, spoken: str = "Нейросеть вернула ошибку, попробуйте ещё раз."):
        super().__init__(message)
        self.spoken = spoken  # что сказать вслух


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
        if cfg.llm_api_key:
            self.session.headers["Authorization"] = f"Bearer {cfg.llm_api_key}"

    # --- публичное ----------------------------------------------------------

    def chat(self, messages: list[dict], tools: list[dict] | None = None, model: str | None = None) -> dict:
        """Возвращает {"content": str, "tool_calls": [{"id", "name", "arguments"}]}."""
        model = model or self.cfg.model
        if self.backend == "ollama":
            return self._chat_ollama(messages, tools, model)
        return self._chat_openai(messages, tools, model)

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
        """Проверка, что сервер жив и модель есть. Возвращает текст проблемы или None."""
        try:
            if self.backend == "ollama":
                tags = self.session.get(f"{self.url}/api/tags", timeout=5).json()
                names = {m["name"] for m in tags.get("models", [])}
                for model in filter(None, [self.cfg.model, self.cfg.vision_model]):
                    if model not in names and f"{model}:latest" not in names:
                        return f"В Ollama нет модели {model}. Скачай её: ollama pull {model}"
            else:
                self.session.get(f"{self.url}/v1/models", timeout=5).raise_for_status()
        except requests.ConnectionError:
            return f"Не могу подключиться к {self.url}. Нейросеть запущена?"
        except (requests.RequestException, ValueError) as e:
            return f"Сервер нейросети ответил странно: {e}"
        return None

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
        msg = self._post("/api/chat", body).get("message", {})
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
        msg = self._post("/v1/chat/completions", body)["choices"][0]["message"]
        calls = [
            {"id": c.get("id") or uuid.uuid4().hex[:12], "name": c["function"]["name"],
             "arguments": _parse_args(c["function"].get("arguments"))}
            for c in msg.get("tool_calls") or []
        ]
        return {"content": _clean(msg.get("content")), "tool_calls": calls}

    # --- общее --------------------------------------------------------------

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
                raise LLMError(detail, "Эта модель не умеет управлять компьютером. Выберите модель с поддержкой инструментов.")
            if "not found" in detail and "model" in detail:
                raise LLMError(detail, "Модель не найдена. Скачайте её командой ollama pull.")
            raise LLMError(f"Ошибка нейросети ({r.status_code}): {detail}")
        return r.json()


def _function_schema(tool: dict) -> dict:
    return {"type": "function", "function": {
        "name": tool["name"], "description": tool["description"], "parameters": tool["input_schema"]}}
