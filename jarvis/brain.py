"""Мозг помощника: диалог с Claude, цикл вызова инструментов и обучение на разговорах."""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Callable

import anthropic
from anthropic.tools.memory import BetaLocalFilesystemMemoryTool

from .config import Config
from .learning import MEMORY_DIR, memory_snapshot, transcript
from .tools import SYSTEM, TOOLS, ToolBox, ToolError

MAX_STEPS = 30          # максимум шагов (вызовов инструментов) на одну просьбу
MAX_MESSAGES = 60       # после этого начинаем разговор заново, чтобы не раздувать контекст
MAX_REFLECT_STEPS = 12

SYSTEM_PROMPT = """Ты — {name}, голосовой помощник в стиле Джарвиса из "Железного человека". \
Ты работаешь на компьютере пользователя ({os}) и можешь им управлять через инструменты: \
запускать программы, открывать сайты, смотреть на экран, кликать, печатать, нажимать клавиши, \
управлять звуком, выполнять команды в консоли. Обращайся к пользователю "{user}", если в памяти \
не записано, как он предпочитает.

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
- Когда задача выполнена — коротко доложи.

Память и подстройка под пользователя:
- У тебя есть долговременная память — папка /memories (инструмент memory). Её содержимое \
приходит в начале каждого разговора в теге <memory>. Следуй тому, что там записано: \
предпочтения, привычные программы и сайты, личные команды пользователя важнее твоих догадок.
- Сразу записывай в память, когда пользователь: просит что-то запомнить; называет \
предпочтение ("я слушаю музыку в Яндекс Музыке", "говори короче"); поправляет тебя \
("не тот браузер", "я имел в виду другое"); придумывает свою команду ("когда говорю \
рабочий режим — открой почту и VS Code"). Записывай и то, что выяснил сам с трудом: \
где лежит программа, какая команда сработала, где на экране нужная кнопка.
- Раскладывай по файлам: /memories/profile.md (кто пользователь, как обращаться, стиль ответов), \
/memories/preferences.md (любимые программы, сайты, привычки), /memories/commands.md (личные \
команды и что они значат), /memories/pc.md (что сработало на этом компьютере). Пиши кратко, \
по одному факту в строке, обновляй устаревшее, а не дублируй.
- Никогда не сохраняй пароли, ключи, номера карт и коды.
- Работу с памятью не озвучивай подробно — максимум "запомнил"."""

REFLECT_SYSTEM = """Ты — модуль самообучения голосового помощника {name}. Тебе показывают \
запись только что закончившегося разговора с пользователем и текущую память помощника. \
Твоя задача — улучшить память, чтобы в следующий раз помощник лучше подстроился под пользователя."""

REFLECT_PROMPT = """Разбери разговор и обнови память инструментом memory.

Что стоит записать:
- новые факты о пользователе и его предпочтениях (явные и очевидные из поведения);
- поправки: где помощник ошибся и как надо было сделать;
- способы, которые сработали на этом ПК, особенно если до них дошли не с первой попытки;
- частые просьбы, которые стоит выполнять быстрее (например, сразу нужной программой);
- личные команды и словечки пользователя;
- типичные ошибки распознавания речи (что слышится вместо чего).

Правила: не дублируй то, что уже есть в памяти; исправляй устаревшее; пиши кратко, по одному \
факту в строке; файлы /memories/profile.md, preferences.md, commands.md, pc.md. Не сохраняй \
пароли, ключи и прочие секреты. Не записывай разовые мелочи без пользы на будущее. \
Если записывать нечего — ничего не меняй. Закончив, ответь одним словом: готово.

<memory>
{memory}
</memory>

<conversation>
{conversation}
</conversation>"""


class Brain:
    def __init__(self, cfg: Config, say: Callable[[str], None], confirm: Callable[[str], bool]):
        self.cfg = cfg
        self.say = say
        self.client = anthropic.Anthropic(api_key=cfg.api_key) if cfg.api_key else anthropic.Anthropic()
        self.toolbox = ToolBox(confirm=confirm, confirm_dangerous=cfg.confirm_dangerous)
        self.system = SYSTEM_PROMPT.format(name=cfg.assistant_name, os=SYSTEM, user=cfg.user_name)
        # Готовая реализация memory tool из SDK: файлы лежат в memory/memories.
        self.memory = BetaLocalFilesystemMemoryTool(base_path=str(MEMORY_DIR))
        self._memory_lock = threading.Lock()
        self.tools = TOOLS + [
            {"type": "web_search_20260209", "name": "web_search", "max_uses": 3},
            self.memory.to_dict(),
        ]
        self.messages: list = []
        self.last_activity = 0.0
        self._reflections: list[threading.Thread] = []

    def reset(self, learn: bool = True) -> None:
        """Начинает новый разговор; старый (если был) отдаётся на разбор в фоне."""
        if learn and self.cfg.learning and self._has_user_text(self.messages):
            old = self.messages
            thread = threading.Thread(target=self._reflect_safe, args=(old,), daemon=True)
            thread.start()
            self._reflections = [t for t in self._reflections if t.is_alive()] + [thread]
        self.messages = []

    def reset_if_idle(self) -> None:
        """Давно не общались — закрываем разговор (и учимся на нём).

        Начинаем с чистого листа, а не обрезаем старую историю: так кеш и
        блоки размышлений модели остаются валидными."""
        if self.messages and time.time() - self.last_activity > self.cfg.idle_reset_minutes * 60:
            self.reset()

    def shutdown(self, timeout: float = 60) -> None:
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

        # Время кладём в сообщение, а не в system prompt, чтобы не сбивать кеш.
        content = f"[{datetime.now():%d.%m.%Y %H:%M}] {text}"
        if not self.messages:
            # Память подкладываем в первое сообщение разговора — так не нужен
            # отдельный вызов memory view, и ответ приходит быстрее.
            with self._memory_lock:
                snapshot = memory_snapshot()
            content = f"<memory>\n{snapshot or 'Память пока пуста.'}\n</memory>\n{content}"
        self.messages.append({"role": "user", "content": content})
        try:
            self._loop()
        except anthropic.AuthenticationError:
            self.reset(learn=False)
            self.say("Не могу подключиться к Claude: неверный API-ключ.")
        except anthropic.RateLimitError:
            self.reset(learn=False)
            self.say("Слишком много запросов, подождите немного.")
        except anthropic.APIConnectionError:
            self.reset(learn=False)
            self.say("Нет связи с сервером. Проверьте интернет.")
        except anthropic.APIStatusError as e:
            self.reset(learn=False)
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
                self.reset(learn=False)
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
            if block.name == "memory":
                with self._memory_lock:
                    content = self.memory.call(block.input)
            else:
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

    # --- самообучение -------------------------------------------------------

    @staticmethod
    def _has_user_text(messages: list) -> bool:
        return any(m["role"] == "user" and isinstance(m["content"], str) for m in messages)

    def _reflect_safe(self, messages: list) -> None:
        try:
            self.reflect(messages)
        except Exception as e:  # обучение не должно ронять помощника
            print(f"[учусь] не получилось разобрать разговор: {e}")

    def reflect(self, messages: list) -> None:
        """Разбор законченного разговора: модель сама решает, что записать в память."""
        with self._memory_lock:
            snapshot = memory_snapshot()
        prompt = REFLECT_PROMPT.format(
            memory=snapshot or "Память пока пуста.",
            conversation=transcript(messages, self.cfg.assistant_name),
        )
        history: list = [{"role": "user", "content": prompt}]
        for _ in range(MAX_REFLECT_STEPS):
            response = self.client.beta.messages.create(
                model=self.cfg.model,
                max_tokens=16000,
                system=REFLECT_SYSTEM.format(name=self.cfg.assistant_name),
                tools=[self.memory.to_dict()],
                messages=history,
                output_config={"effort": "low"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            if response.stop_reason == "refusal":
                return
            history.append({"role": "assistant", "content": response.content})
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                break
            results = []
            for block in tool_uses:
                try:
                    with self._memory_lock:
                        out = self.memory.call(block.input)
                    print(f"[учусь] {block.input.get('command')} {block.input.get('path') or block.input.get('new_path', '')}")
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": out})
                except Exception as e:
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": str(e), "is_error": True})
            history.append({"role": "user", "content": results})
