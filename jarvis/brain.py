"""Мозг помощника: диалог с локальной нейросетью, вызов инструментов и обучение на разговорах."""

from __future__ import annotations

import re
import threading
import time
from datetime import datetime
from typing import Callable

from .config import Config
from .learning import MEMORY_TOOLS, forget, memory_snapshot, remember, transcript
from .llm import LLM, LLMError
from .tools import SYSTEM, TOOLS, VISION_TOOLS, ToolBox, ToolError

MAX_STEPS = 15          # максимум шагов (вызовов инструментов) на одну просьбу
MAX_MESSAGES = 40       # после этого начинаем разговор заново — у локальных моделей мало контекста
MAX_REFLECT_STEPS = 10
TOOL_RESULT_LIMIT = 3000

# Просьбы, которые точно требуют действия на компьютере, а не просто ответа.
ACTION_RE = re.compile(
    r"\b(открой|откро|запусти|включи|выключи|закрой|сверни|разверни|поставь|найди|напиши|набери|"
    r"нажми|громче|тише|перезагрузи|заблокируй|переключи|прокрути|скопируй|вставь|сохрани|"
    r"пауз|следующ|предыдущ|убавь|прибавь|сделай)",
    re.I,
)
NUDGE = ("[Система] Ты ответил, но не вызвал ни одного инструмента — на компьютере ничего не произошло. "
         "Вызови нужный инструмент прямо сейчас (например open_app или open_url). "
         "Не говори, что сделал, пока инструмент не ответил, что всё получилось.")

SYSTEM_PROMPT = """Ты — {name}, голосовой помощник в стиле Джарвиса из "Железного человека". \
Ты работаешь на компьютере пользователя ({os}) и управляешь им через инструменты. \
Обращайся к пользователю "{user}", если в памяти не записано иначе.

Как отвечать:
- Ответ зачитывается вслух. Всегда говори по-русски, коротко: одно-два предложения. \
Никакого markdown, списков, эмодзи и ссылок.
- Речь распознаётся автоматически и бывает с ошибками — догадывайся по смыслу. \
Если совсем непонятно, коротко переспроси.

Как действовать:
- Просят что-то сделать на компьютере — вызывай инструмент, а не объясняй, как это сделать.
- Никогда не говори "открыл", "запустил", "сделал", если не вызвал инструмент и не получил ответ, \
что всё получилось. Если инструмент вернул ошибку — честно скажи, что не вышло.
- Просят подсказать с тем, что на экране, — сначала look_at_screen.
- Чтобы кликнуть, сначала узнай координаты через look_at_screen.
- Вопросы о фактах, новостях, погоде, курсах — web_search.
- Не делай разрушительных действий, если о них не просили прямо. Опасные инструменты сами \
спросят подтверждение.
- Сделал — коротко доложи.

Память:
- В начале разговора в теге <memory> лежит то, что ты знаешь о пользователе. Следуй этому: \
его предпочтения и личные команды важнее твоих догадок.
- Вызывай remember, когда пользователь просит запомнить, называет предпочтение, поправляет тебя \
или придумывает свою команду. Вызывай forget, если факт устарел или неверен.
- Не сохраняй пароли, ключи и коды. Про память говори максимум "запомнил"."""

REFLECT_SYSTEM = """Ты — модуль самообучения голосового помощника {name}. Ты разбираешь \
законченный разговор и обновляешь память помощника инструментами remember и forget, \
чтобы в следующий раз он лучше подстроился под пользователя."""

REFLECT_PROMPT = """Вот память помощника и запись только что законченного разговора.

<memory>
{memory}
</memory>

<conversation>
{conversation}
</conversation>

Что стоит запомнить (вызывай remember для каждого факта):
- новые факты о пользователе и его предпочтениях;
- где помощник ошибся и как надо было сделать;
- что сработало на этом компьютере, особенно если получилось не с первой попытки;
- частые просьбы и личные команды пользователя;
- как распознавание речи коверкает слова пользователя.

Правила: не дублируй то, что уже есть в памяти; устаревшее удаляй через forget; один короткий \
факт за вызов; не сохраняй пароли и секреты; разовые мелочи без пользы не записывай. \
Если записывать нечего — ничего не вызывай. Закончив, ответь словом: готово."""


class Brain:
    def __init__(self, cfg: Config, say: Callable[[str], None], confirm: Callable[[str], bool]):
        self.cfg = cfg
        self.say = say
        self.llm = LLM(cfg)
        vision = self.llm.describe_image if cfg.vision_model else None
        self.toolbox = ToolBox(confirm=confirm, confirm_dangerous=cfg.confirm_dangerous, vision=vision)
        self.system = SYSTEM_PROMPT.format(name=cfg.assistant_name, os=SYSTEM, user=cfg.user_name)
        tools = TOOLS if vision else [t for t in TOOLS if t["name"] not in VISION_TOOLS]
        self.tools = tools + MEMORY_TOOLS
        self.messages: list[dict] = []
        self.last_activity = 0.0
        self._reflections: list[threading.Thread] = []

    def check(self) -> str | None:
        return self.llm.check()

    def reset(self, learn: bool = True) -> None:
        """Начинает новый разговор; старый (если был) отдаётся на разбор в фоне."""
        if learn and self.cfg.learning and any(m["role"] == "user" for m in self.messages):
            thread = threading.Thread(target=self._reflect_safe, args=(self.messages,), daemon=True)
            thread.start()
            self._reflections = [t for t in self._reflections if t.is_alive()] + [thread]
        self.messages = []

    def reset_if_idle(self) -> None:
        """Давно не общались — закрываем разговор (и учимся на нём)."""
        if self.messages and time.time() - self.last_activity > self.cfg.idle_reset_minutes * 60:
            self.reset()

    def shutdown(self, timeout: float = 120) -> None:
        """Перед выходом: разобрать текущий разговор и дождаться фоновых разборов."""
        self.reset()
        for thread in self._reflections:
            thread.join(timeout)

    def handle(self, text: str) -> None:
        """Обрабатывает одну просьбу пользователя; всё, что надо сказать, уходит в self.say."""
        self.reset_if_idle()
        if len(self.messages) > MAX_MESSAGES:
            self.reset()
        self.last_activity = time.time()

        content = f"[{datetime.now():%d.%m.%Y %H:%M}] {text}"
        if not self.messages:
            snapshot = memory_snapshot()
            content = f"<memory>\n{snapshot or 'Память пока пуста.'}\n</memory>\n{content}"
        self.messages.append({"role": "user", "content": content})
        try:
            self._loop(text)
        except LLMError as e:
            print(f"[brain] {e}")
            self.reset(learn=False)
            self.say(e.spoken)
        self.last_activity = time.time()

    def _loop(self, request: str) -> None:
        used_tools = False
        nudged = False
        for _ in range(MAX_STEPS):
            reply = self.llm.chat([{"role": "system", "content": self.system}] + self.messages, self.tools)
            self.messages.append({"role": "assistant", "content": reply["content"], "tool_calls": reply["tool_calls"]})
            if not reply["tool_calls"]:
                if not used_tools and not nudged and ACTION_RE.search(request):
                    # Маленькие модели любят сказать «запустил», ничего не запустив. Ловим и
                    # заставляем вызвать инструмент; этот ответ не озвучиваем.
                    print(f"[brain] модель ответила без действия: {reply['content']!r} — прошу вызвать инструмент")
                    nudged = True
                    self.messages.append({"role": "user", "content": NUDGE})
                    continue
                if nudged and not used_tools:
                    print("[brain] модель так и не вызвала инструмент. Если так часто — возьми модель "
                          "получше с поддержкой tools (например qwen2.5 7b/14b instruct).")
                self.say(reply["content"] or "Готово.")
                return
            used_tools = True
            if reply["content"]:
                self.say(reply["content"])
            for call in reply["tool_calls"]:
                result = self._run_tool(call["name"], call["arguments"])
                self.messages.append({"role": "tool", "tool_call_id": call["id"], "name": call["name"], "content": result})
        self.reset()
        self.say("Задача получилась слишком длинной, я остановился.")

    def _run_tool(self, name: str, args: dict) -> str:
        print(f"[tool] {name} {args}")
        try:
            if name == "remember":
                result = remember(**args)
            elif name == "forget":
                result = forget(**args)
            else:
                result = self.toolbox.run(name, args)
        except (ToolError, ValueError) as e:
            result = f"Ошибка: {e}"
        except TypeError as e:  # модель передала не те аргументы
            result = f"Ошибка: неверные аргументы ({e})"
        except Exception as e:
            result = f"Ошибка: {type(e).__name__}: {e}"
        if result.startswith("Ошибка"):
            print(f"[tool] {result}")
        if len(result) > TOOL_RESULT_LIMIT:
            result = result[:TOOL_RESULT_LIMIT] + "\n...[обрезано]"
        return result

    # --- самообучение -------------------------------------------------------

    def _reflect_safe(self, messages: list[dict]) -> None:
        try:
            self.reflect(messages)
        except Exception as e:  # обучение не должно ронять помощника
            print(f"[учусь] не получилось разобрать разговор: {e}")

    def reflect(self, messages: list[dict]) -> None:
        """Разбор законченного разговора: модель сама решает, что записать в память."""
        prompt = REFLECT_PROMPT.format(
            memory=memory_snapshot() or "Память пока пуста.",
            conversation=transcript(messages, self.cfg.assistant_name),
        )
        history: list[dict] = [
            {"role": "system", "content": REFLECT_SYSTEM.format(name=self.cfg.assistant_name)},
            {"role": "user", "content": prompt},
        ]
        for _ in range(MAX_REFLECT_STEPS):
            reply = self.llm.chat(history, MEMORY_TOOLS)
            history.append({"role": "assistant", "content": reply["content"], "tool_calls": reply["tool_calls"]})
            if not reply["tool_calls"]:
                return
            for call in reply["tool_calls"]:
                try:
                    fn = remember if call["name"] == "remember" else forget
                    result = fn(**call["arguments"])
                    print(f"[учусь] {call['name']}: {call['arguments']}")
                except Exception as e:
                    result = f"Ошибка: {e}"
                history.append({"role": "tool", "tool_call_id": call["id"], "name": call["name"], "content": result})
