"""Мозг помощника: диалог с Claude и цикл вызова инструментов."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Callable

import anthropic

from .config import Config
from .tools import SYSTEM, TOOLS, ToolBox, ToolError

MAX_STEPS = 30          # максимум шагов (вызовов инструментов) на одну просьбу
MAX_MESSAGES = 60       # после этого начинаем разговор заново, чтобы не раздувать контекст

SYSTEM_PROMPT = """Ты — {name}, голосовой помощник в стиле Джарвиса из "Железного человека". \
Ты работаешь на компьютере пользователя ({os}) и можешь им управлять через инструменты: \
запускать программы, открывать сайты, смотреть на экран, кликать, печатать, нажимать клавиши, \
управлять звуком, выполнять команды в консоли. Обращайся к пользователю "{user}".

Как отвечать:
- Твои ответы зачитываются вслух синтезатором речи. Говори по-русски, коротко и по делу — \
обычно одно-два предложения. Никакого markdown, списков, эмодзи, ссылок и кода в ответе.
- Перед долгим делом из нескольких шагов скажи одной фразой, что собираешься сделать.
- Речь распознаётся автоматически и может быть с ошибками — догадывайся по смыслу. Если просьба \
совсем непонятна, коротко переспроси.

Как действовать:
- Если просят что-то сделать на компьютере — делай сам, а не объясняй, как это сделать.
- Если просят подсказать или помочь с тем, что на экране, сначала сделай скриншот.
- Прежде чем кликать, сделай скриншот, чтобы знать координаты. После важных действий \
проверяй результат новым скриншотом.
- Для вопросов о фактах, новостях, погоде, курсах используй веб-поиск.
- Не выполняй разрушительных действий (удаление файлов, форматирование и т.п.), если о них \
не попросили прямо. Опасные инструменты сами спросят подтверждение у пользователя.
- Когда задача выполнена — коротко доложи."""


class Brain:
    def __init__(self, cfg: Config, say: Callable[[str], None], confirm: Callable[[str], bool]):
        self.cfg = cfg
        self.say = say
        self.client = anthropic.Anthropic(api_key=cfg.api_key) if cfg.api_key else anthropic.Anthropic()
        self.toolbox = ToolBox(confirm=confirm, confirm_dangerous=cfg.confirm_dangerous)
        self.system = SYSTEM_PROMPT.format(name=cfg.assistant_name, os=SYSTEM, user=cfg.user_name)
        self.tools = TOOLS + [{"type": "web_search_20260209", "name": "web_search", "max_uses": 3}]
        self.messages: list = []
        self.last_activity = 0.0

    def reset(self) -> None:
        self.messages = []

    def handle(self, text: str) -> None:
        """Обрабатывает одну просьбу пользователя; всё, что надо сказать, уходит в self.say."""
        idle = time.time() - self.last_activity > self.cfg.idle_reset_minutes * 60
        if idle or len(self.messages) > MAX_MESSAGES:
            # Начинаем с чистого листа, а не обрезаем старую историю: так кеш и
            # блоки размышлений модели остаются валидными.
            self.reset()
        self.last_activity = time.time()

        # Время кладём в сообщение, а не в system prompt, чтобы не сбивать кеш.
        self.messages.append({"role": "user", "content": f"[{datetime.now():%d.%m.%Y %H:%M}] {text}"})
        try:
            self._loop()
        except anthropic.AuthenticationError:
            self.reset()
            self.say("Не могу подключиться к Claude: неверный API-ключ.")
        except anthropic.RateLimitError:
            self.reset()
            self.say("Слишком много запросов, подождите немного.")
        except anthropic.APIConnectionError:
            self.reset()
            self.say("Нет связи с сервером. Проверьте интернет.")
        except anthropic.APIStatusError as e:
            self.reset()
            print(f"[brain] ошибка API {e.status_code}: {e.message}")
            self.say("Сервер вернул ошибку, попробуйте ещё раз.")
        self.last_activity = time.time()

    def _loop(self) -> None:
        for _ in range(MAX_STEPS):
            response = self.client.beta.messages.create(
                model=self.cfg.model,
                max_tokens=16000,
                system=self.system,
                tools=self.tools,
                messages=self.messages,
                output_config={"effort": self.cfg.effort},
                cache_control={"type": "ephemeral"},
                # Если классификатор безопасности ошибочно откажет, сервер сам повторит
                # запрос на рекомендованной запасной модели.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )

            if response.stop_reason == "refusal":
                self.reset()
                self.say("Извините, с этим я помочь не могу.")
                return

            self.messages.append({"role": "assistant", "content": response.content})
            spoken = " ".join(b.text for b in response.content if b.type == "text").strip()
            if spoken:
                self.say(spoken)

            if response.stop_reason == "pause_turn":
                continue  # серверный веб-поиск не успел закончить — просто продолжаем
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                if response.stop_reason == "max_tokens" and not spoken:
                    self.say("Ответ получился слишком длинным, давайте попробуем проще.")
                return

            results = [self._run_tool(b) for b in tool_uses]
            self.messages.append({"role": "user", "content": results})

        self.reset()
        self.say("Задача получилась слишком длинной, я остановился.")

    def _run_tool(self, block) -> dict:
        print(f"[tool] {block.name} {block.input}")
        try:
            content = self.toolbox.run(block.name, dict(block.input))
            return {"type": "tool_result", "tool_use_id": block.id, "content": content}
        except ToolError as e:
            error = str(e)
        except TypeError as e:  # неполные/лишние аргументы
            error = f"Неверные аргументы: {e}"
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
        print(f"[tool] ошибка: {error}")
        return {"type": "tool_result", "tool_use_id": block.id, "content": error, "is_error": True}
