"""Инструменты, через которые нейросеть управляет компьютером.

Каждый инструмент — метод ToolBox с описанием-схемой в TOOLS. Тяжёлые библиотеки
(pyautogui, mss) импортируются лениво, чтобы текстовый режим работал где угодно.
"""

from __future__ import annotations

import base64
import io
import os
import platform
import re
import subprocess
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote_plus, urlparse

SYSTEM = platform.system()  # "Windows" / "Darwin" / "Linux"
SCREENSHOT_MAX_WIDTH = 1366

if SYSTEM == "Windows":
    # Иначе при масштабе экрана 125%+ скриншот в физических пикселях, а клики
    # pyautogui — в логических, и мышь промахивается.
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

# Частые программы: что сказал человек -> что запускать.
APP_ALIASES = {
    "Windows": {
        "блокнот": "notepad",
        "калькулятор": "calc",
        "проводник": "explorer",
        "диспетчер задач": "taskmgr",
        "панель управления": "control",
        "параметры": "ms-settings:",
        "настройки": "ms-settings:",
        "paint": "mspaint",
        "пейнт": "mspaint",
        "командная строка": "cmd",
        "терминал": "wt",
        "powershell": "powershell",
        "браузер": "https://www.google.com",
        "яндекс": r"%LOCALAPPDATA%\Yandex\YandexBrowser\Application\browser.exe",
        "яндекс браузер": r"%LOCALAPPDATA%\Yandex\YandexBrowser\Application\browser.exe",
        "yandex": r"%LOCALAPPDATA%\Yandex\YandexBrowser\Application\browser.exe",
        "хром": "chrome",
        "chrome": "chrome",
        "edge": "msedge",
        "эдж": "msedge",
        "firefox": "firefox",
        "ворд": "winword",
        "word": "winword",
        "эксель": "excel",
        "excel": "excel",
        "телеграм": "tg://",
        "telegram": "tg://",
        "дискорд": "discord://",
        "discord": "discord://",
        "стим": "steam://open/main",
        "steam": "steam://open/main",
        "спотифай": "spotify:",
        "spotify": "spotify:",
    },
    "Darwin": {"браузер": "Safari", "калькулятор": "Calculator", "терминал": "Terminal"},
    "Linux": {"браузер": "xdg-open https://www.google.com", "терминал": "x-terminal-emulator"},
}

# Сервисы, которыми часто пользуются в браузере: если программы нет — открываем сайт.
WEB_APPS = {
    "яндекс музыка": "https://music.yandex.ru",
    "yandex music": "https://music.yandex.ru",
    "ютуб": "https://www.youtube.com",
    "youtube": "https://www.youtube.com",
    "вк": "https://vk.com",
    "вконтакте": "https://vk.com",
    "кинопоиск": "https://www.kinopoisk.ru",
    "твич": "https://www.twitch.tv",
    "twitch": "https://www.twitch.tv",
    "яндекс почта": "https://mail.yandex.ru",
    "гмейл": "https://mail.google.com",
    "gmail": "https://mail.google.com",
    "яндекс карты": "https://yandex.ru/maps",
    "переводчик": "https://translate.yandex.ru",
    "чат гпт": "https://chatgpt.com",
    "chatgpt": "https://chatgpt.com",
    "гугл диск": "https://drive.google.com",
    "яндекс диск": "https://disk.yandex.ru",
    "вотсап": "https://web.whatsapp.com",
    "ватсап": "https://web.whatsapp.com",
    "озон": "https://www.ozon.ru",
    "вайлдберриз": "https://www.wildberries.ru",
    "авито": "https://www.avito.ru",
}


def _stem(text: str) -> str:
    """Грубо отрезает окончания, чтобы «яндекс музыку» совпало с «яндекс музыка»."""
    words = re.sub(r"[^\w]+", " ", text.lower().replace("ё", "е")).split()
    return " ".join(re.sub(r"[аяуюеиыйоь]+$", "", w) if len(w) > 4 else w for w in words)


# --- безопасность ------------------------------------------------------------------
# Ссылки приходят не только от пользователя, но и из выдачи поиска, постов и MCP. На Windows
# webbrowser.open/os.startfile открывают ЛЮБОЙ протокол (ms-msdt:, search-ms:, file://…exe) —
# через такие ссылки делали реальные атаки. Поэтому — только белый список.
WEB_SCHEMES = ("http", "https")
APP_SCHEMES = ("steam", "tg", "discord", "spotify", "ms-settings", "mailto", "yandexmusic", "zoommtg", "roblox",
               "com.epicgames.launcher", "obsidian", "vk")
# Файлы, которые при открытии что-то выполняют. Если такой файл лежит не среди установленных
# программ (например, в «Загрузках» или на сетевой шаре) — запускаем только после «да».
RISKY_EXT = {".exe", ".bat", ".cmd", ".com", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".msi",
             ".msp", ".scr", ".hta", ".cpl", ".jar", ".lnk", ".url", ".appref-ms", ".reg", ".pif", ".application",
             ".iso", ".img", ".vhd", ".vhdx", ".xll", ".msc", ".library-ms", ".settingcontent-ms", ".search-ms"}
SHELL_WINDOW_RE = re.compile(r"powershell|командная строка|command prompt|cmd\.exe|терминал|terminal|^выполнить$|^run$|"
                             r"\bbash\b|\bwsl\b|администратор:|administrator:", re.I)
COMMANDISH_RE = re.compile(
    r"(powershell|pwsh|cmd(\.exe)?\s*/[ck]|\biex\b|invoke-|\biwr\b|\birm\b|\bcurl\b|\bwget\b|certutil|bitsadmin|"
    r"mshta|rundll32|regsvr32|\breg\s+(add|delete)|schtasks|\bdel\s+/|\brm\s+-|\bformat\s+[a-z]:|\bsc\s+(create|config)|"
    r"net\s+(user|localgroup)|start-process|-enc(odedcommand)?\b|set-executionpolicy|add-mppreference|https?://)", re.I)


def scheme_of(url: str) -> str:
    m = re.match(r"^([a-z][\w+.-]*):", url.strip(), re.I)
    if not m or re.match(r"^[a-z]:[\\/]", url.strip(), re.I):  # C:\... — это путь, а не протокол
        return ""
    return m.group(1).lower()


def safe_open(url: str, allowed: tuple[str, ...] = WEB_SCHEMES) -> None:
    """Открывает ссылку, только если её протокол в белом списке."""
    scheme = scheme_of(url)
    if scheme not in allowed:
        raise ToolError(f"Не открываю ссылки вида «{scheme or url[:20]}:» — так открывают вредоносные программы. "
                        f"Можно только {', '.join(allowed)}.")
    webbrowser.open(url)


def _trusted_dirs() -> list[Path]:
    env = os.environ.get
    dirs = [env("ProgramFiles"), env("ProgramFiles(x86)"), env("ProgramW6432"), env("SystemRoot") or env("WINDIR"),
            env("LOCALAPPDATA") and os.path.join(env("LOCALAPPDATA"), "Programs"),
            env("APPDATA") and os.path.join(env("APPDATA"), "Microsoft", "Windows", "Start Menu"),
            env("PROGRAMDATA") and os.path.join(env("PROGRAMDATA"), "Microsoft", "Windows", "Start Menu"),
            env("LOCALAPPDATA") and os.path.join(env("LOCALAPPDATA"), "Yandex"),
            env("LOCALAPPDATA") and os.path.join(env("LOCALAPPDATA"), "Discord")]
    return [Path(d).resolve() for d in dirs if d]


def risky_target(target: str) -> str | None:
    """Что именно опасно в запуске target (или None, если это обычная программа/папка/документ)."""
    t = os.path.expandvars(target.strip().strip('"'))
    if t.startswith(("\\\\", "//")):
        return f"файл с сетевого ресурса {t}"
    scheme = scheme_of(t)
    if scheme:
        return None if scheme in WEB_SCHEMES + APP_SCHEMES else f"ссылку с протоколом {scheme}:"
    path = Path(t)
    try:
        if not path.is_file():
            return None  # папка или просто название программы — его ищем в меню «Пуск»
        real = path.resolve()
    except OSError:
        return None
    if real.suffix.lower() not in RISKY_EXT:
        return None
    for d in _trusted_dirs():
        if real == d or d in real.parents:
            return None
    return f"файл {real.name} из {real.parent}"


def _active_window_title() -> str:
    if SYSTEM != "Windows":
        return ""
    try:
        import pygetwindow

        win = pygetwindow.getActiveWindow()
        return (win.title if win else "") or ""
    except Exception:  # noqa: BLE001
        return ""


def _web_app(name: str) -> str | None:
    stem = _stem(name)
    for key, url in WEB_APPS.items():
        if _stem(key) == stem:
            return url
    return None


TOOLS: list[dict[str, Any]] = [
    {
        "name": "open_app",
        "description": "Запускает программу или открывает файл/папку. Передавай название так, как его "
        "сказал пользователь (например «яндекс браузер», «телеграм», «фотошоп») — программа сама найдёт "
        "её в меню Пуск и на рабочем столе. Если не нашлось, вернёт похожие программы — выбери из них.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Название программы, путь к файлу или папке"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "open_url",
        "description": "Открывает ссылку в браузере по умолчанию.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
            "additionalProperties": False,
        },
    },
    {
        "name": "browser_search",
        "description": "Открывает в браузере поиск (Google, Яндекс или YouTube) по запросу — "
        "когда пользователь хочет сам посмотреть результаты.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "engine": {"type": "string", "enum": ["google", "yandex", "youtube"]},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "look_at_screen",
        "description": "Смотрит на экран и отвечает на вопрос о нём: что открыто, что написано, "
        "где кнопка (с координатами для клика). Используй, когда просят подсказать или помочь с тем, что на экране.",
        "input_schema": {
            "type": "object",
            "properties": {"question": {"type": "string", "description": "Что нужно узнать на экране"}},
            "required": ["question"],
            "additionalProperties": False,
        },
    },
    {
        "name": "mouse_click",
        "description": "Кликает мышью в точку. Координаты — те, что вернул look_at_screen.",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "button": {"type": "string", "enum": ["left", "right", "middle"]},
                "double": {"type": "boolean"},
            },
            "required": ["x", "y"],
            "additionalProperties": False,
        },
    },
    {
        "name": "scroll",
        "description": "Прокручивает колесо мыши. Положительное значение — вверх, отрицательное — вниз.",
        "input_schema": {
            "type": "object",
            "properties": {"amount": {"type": "integer", "description": "Щелчки колеса, обычно 3..10"}},
            "required": ["amount"],
            "additionalProperties": False,
        },
    },
    {
        "name": "type_text",
        "description": "Печатает текст в активное окно (через буфер обмена, кириллица поддерживается).",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "press_enter": {"type": "boolean", "description": "Нажать Enter после ввода"},
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "press_keys",
        "description": "Нажимает клавишу или сочетание, например ['ctrl','c'], ['alt','tab'], ['win','d'], ['enter'].",
        "input_schema": {
            "type": "object",
            "properties": {"keys": {"type": "array", "items": {"type": "string"}}},
            "required": ["keys"],
            "additionalProperties": False,
        },
    },
    {
        "name": "media",
        "description": "Управление звуком и плеером через мультимедийные клавиши.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["play_pause", "next", "previous", "volume_up", "volume_down", "mute"],
                },
                "times": {"type": "integer", "description": "Сколько раз нажать (для громкости 1 нажатие ≈ 2%)"},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    },
    {
        "name": "find_fishing_spot",
        "description": "Русская Рыбалка 4: ищет посты и сайты (Telegram, ВК, форумы) про то, где и на что ловить "
        "указанную рыбу (или добывать наживку) на водоёме, вытаскивает координаты и фразы про наживку "
        "и открывает лучший пост в браузере. Используй, когда спрашивают про точки, клёв, наживку в РР4.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Рыба или наживка, например «перловица», «карп»"},
                "water": {"type": "string", "description": "Водоём, например «Река Вьюнок»"},
                "open_link": {"type": "boolean", "description": "Открыть лучший найденный пост (по умолчанию да)"},
            },
            "required": ["target", "water"],
            "additionalProperties": False,
        },
    },
    {
        "name": "add_task",
        "description": "Добавляет задачу в список дел пользователя.",
        "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"],
                         "additionalProperties": False},
    },
    {
        "name": "complete_task",
        "description": "Отмечает задачу выполненной (по части текста).",
        "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"],
                         "additionalProperties": False},
    },
    {
        "name": "list_tasks",
        "description": "Список текущих задач пользователя.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "add_note",
        "description": "Сохраняет заметку пользователя.",
        "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"],
                         "additionalProperties": False},
    },
    {
        "name": "list_notes",
        "description": "Показывает заметки пользователя (последние 20).",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "add_event",
        "description": "Добавляет событие в календарь. date — ГГГГ-ММ-ДД, time — ЧЧ:ММ (если известно). "
        "Можно не указывать date, а написать дату словами в text («завтра в 18:00 тренировка»).",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}, "date": {"type": "string"}, "time": {"type": "string"}},
            "required": ["text"], "additionalProperties": False,
        },
    },
    {
        "name": "list_events",
        "description": "События календаря на ближайшие дни.",
        "input_schema": {"type": "object", "properties": {"days": {"type": "integer"}}, "additionalProperties": False},
    },
    {
        "name": "delete_item",
        "description": "Удаляет задачу, заметку или событие по части текста. kind: tasks / notes / events.",
        "input_schema": {
            "type": "object",
            "properties": {"kind": {"type": "string", "enum": ["tasks", "notes", "events"]}, "text": {"type": "string"}},
            "required": ["kind", "text"], "additionalProperties": False,
        },
    },
    {
        "name": "smart_home",
        "description": "Умный дом (Home Assistant): включить/выключить/переключить устройство по названию — "
        "свет, розетку, кондиционер, шторы, сцену. action: on / off / toggle / list (list — показать устройства).",
        "input_schema": {
            "type": "object",
            "properties": {"action": {"type": "string", "enum": ["on", "off", "toggle", "list"]},
                           "name": {"type": "string", "description": "Например «свет в комнате»"}},
            "required": ["action"], "additionalProperties": False,
        },
    },
    {
        "name": "set_timer",
        "description": "Ставит напоминание: через указанное время помощник скажет вслух текст. "
        "Например «напомни через 15 минут покормить персонажа».",
        "input_schema": {
            "type": "object",
            "properties": {
                "minutes": {"type": "number", "description": "Через сколько минут (можно дробное)"},
                "text": {"type": "string", "description": "Что напомнить"},
            },
            "required": ["minutes", "text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_timers",
        "description": "Показывает активные напоминания и сколько до них осталось.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "cancel_timers",
        "description": "Отменяет напоминания: все или те, в тексте которых есть указанное слово.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "Слово из напоминания; пусто — отменить все"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "save_screenshot",
        "description": "Сохраняет скриншот экрана в полном качестве в папку «Изображения/Jarvis» (например, трофей в игре).",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "web_search",
        "description": "Ищет в интернете и возвращает краткие результаты. Для вопросов о фактах, новостях, погоде, курсах.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_windows",
        "description": "Список открытых окон (только Windows).",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "focus_window",
        "description": "Переключается на окно, в заголовке которого есть указанный текст (только Windows).",
        "input_schema": {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
            "additionalProperties": False,
        },
    },
    {
        "name": "system_info",
        "description": "Загрузка процессора, память, диски, батарея и самые прожорливые процессы.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "find_files",
        "description": "Ищет файлы и папки по части имени в домашней папке пользователя (или в указанной).",
        "input_schema": {
            "type": "object",
            "properties": {
                "name_part": {"type": "string"},
                "folder": {"type": "string", "description": "Где искать; пустая строка = домашняя папка"},
            },
            "required": ["name_part"],
            "additionalProperties": False,
        },
    },
    {
        "name": "clipboard",
        "description": "Читает или записывает буфер обмена.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["read", "write"]},
                "text": {"type": "string", "description": "Текст для записи (для read — пустая строка)"},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    },
    {
        "name": "run_command",
        "description": "Выполняет команду в консоли (PowerShell на Windows, sh на Linux/Mac) и возвращает вывод. "
        "Требует подтверждения пользователя. Используй, когда другие инструменты не подходят.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "explanation": {"type": "string", "description": "Коротко по-русски: что делает команда (зачитается при подтверждении)"},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    },
    {
        "name": "kill_process",
        "description": "Закрывает процесс по имени, например 'chrome' или 'notepad.exe'. Требует подтверждения.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "power",
        "description": "Блокировка, сон, перезагрузка, выключение компьютера (сразу или через delay_minutes) "
        "и отмена запланированного выключения (cancel). Сон, перезагрузка и выключение спрашивают подтверждение.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["lock", "sleep", "restart", "shutdown", "cancel"]},
                "delay_minutes": {"type": "number", "description": "Через сколько минут; 0 — сразу"},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    },
]

VISION_TOOLS = {"look_at_screen", "mouse_click"}


class ToolError(Exception):
    pass


class ToolBox:
    def __init__(
        self,
        confirm: Callable[[str], bool],
        confirm_dangerous: bool = True,
        vision: Callable[[str, str], str] | None = None,
        notify: Callable[[str], None] | None = None,
    ):
        self.confirm = confirm
        self.confirm_dangerous = confirm_dangerous
        self.confirm_power = True  # спрашивать «да/нет» перед выключением, перезагрузкой и сном
        # vision(картинка_base64, вопрос) -> ответ мультимодальной модели.
        self.vision = vision
        # notify(текст) — сказать что-то вслух вне разговора (напоминания).
        self.notify = notify or print
        self._timers: list[tuple[float, str, threading.Timer]] = []
        self._timers_lock = threading.Lock()
        self.organizer = None  # Organizer — задачи, заметки, календарь
        self.home = None       # Home — умный дом (Home Assistant)
        self.fishing_channels: list[str] = []
        self.discord_token = ""
        self.discord_channels: list = []
        self.discord_open_mode = "web"
        # Куда открывать посты из Telegram: web — web.telegram.org, app — приложение, browser — t.me.
        self.tg_open_mode = "web"
        # Во сколько раз реальный экран больше отправленного скриншота.
        self.screen_scale = 1.0

    def run(self, name: str, args: dict[str, Any]) -> str:
        method = getattr(self, f"tool_{name}", None)
        if method is None:
            raise ToolError(f"Нет такого инструмента: {name}")
        return method(**_coerce_args(name, args))

    def _ask(self, question: str) -> None:
        if self.confirm_dangerous and not self.confirm(question):
            raise ToolError("Пользователь не подтвердил действие.")

    # --- запуск и браузер ----------------------------------------------------

    def tool_open_app(self, name: str) -> str:
        name = name.strip()
        target = APP_ALIASES.get(SYSTEM, {}).get(name.lower(), name)
        scheme = scheme_of(target)
        if scheme and scheme not in WEB_SCHEMES + APP_SCHEMES:
            raise ToolError(f"Не открываю протокол «{scheme}:» — так открывают вредоносные программы.")
        risk = risky_target(target)
        if risk:
            self._ask(f"Запустить {risk}? Это не установленная программа.")
        if target.startswith(("http://", "https://")):
            webbrowser.open(target)
        elif SYSTEM == "Windows":
            from . import apps

            # Алиас может указывать на путь, которого на этом компе нет — тогда ищем по названию.
            if "%" in target and not Path(os.path.expandvars(target)).exists():
                target = name
            try:
                launched = apps.launch_windows(target) or (target != name and apps.launch_windows(name))
            except OSError as e:
                raise ToolError(f"Не получилось запустить {name}: {e}")
            if not launched:
                url = _web_app(name)
                if url:
                    safe_open(url)
                    return f"Программы «{name}» на компьютере нет, открыл сайт {url} в браузере"
                similar = ", ".join(apps.suggestions(name)) or "ничего похожего"
                raise ToolError(f"Программа «{name}» не найдена. Похожие в меню Пуск: {similar}. "
                                f"Если это сайт или онлайн-сервис — открой его через open_url.")
            return f"Запущено: {launched}"
        elif _web_app(name) and not Path(target).expanduser().exists():
            url = _web_app(name)
            safe_open(url)
            return f"Открыл сайт {url} в браузере"
        elif SYSTEM == "Darwin":
            path = Path(target).expanduser()
            subprocess.Popen(["open", str(path)] if path.exists() else ["open", "-a", target])
        else:
            path = Path(target).expanduser()
            if path.exists():
                subprocess.Popen(["xdg-open", str(path)])
            else:
                import shlex
                import shutil

                argv = shlex.split(target)  # без shell: «название; rm -rf ~» не выполнит вторую команду
                if not argv or not shutil.which(argv[0]):
                    raise ToolError(f"Программа «{name}» не найдена")
                subprocess.Popen(argv, start_new_session=True)
        return f"Запущено: {target}"

    def tool_open_url(self, url: str) -> str:
        url = url.strip()
        if url.startswith(("\\\\", "//")):
            raise ToolError("Сетевые пути не открываю — так подсовывают вредоносные файлы.")
        if not scheme_of(url) or re.match(r"^[\w.-]+:\d+([/?#]|$)", url):
            url = "https://" + url  # «youtube.com», «localhost:8080»
        safe_open(url, WEB_SCHEMES + APP_SCHEMES)
        return f"Открыто: {url}"

    def tool_browser_search(self, query: str, engine: str = "google") -> str:
        base = {
            "google": "https://www.google.com/search?q=",
            "yandex": "https://yandex.ru/search/?text=",
            "youtube": "https://www.youtube.com/results?search_query=",
        }.get(engine, "https://www.google.com/search?q=")
        webbrowser.open(base + quote_plus(query))
        return f"Открыт поиск {engine}: {query}"

    # --- экран, мышь, клавиатура --------------------------------------------

    def capture_screen(self) -> tuple[str, int, int]:
        """Скриншот основного монитора в JPEG base64, уменьшенный до SCREENSHOT_MAX_WIDTH."""
        import mss
        from PIL import Image

        with mss.mss() as sct:
            shot = sct.grab(sct.monitors[1])
            img = Image.frombytes("RGB", shot.size, shot.rgb)
        self.screen_scale = max(1.0, img.width / SCREENSHOT_MAX_WIDTH)
        if self.screen_scale > 1:
            img = img.resize((round(img.width / self.screen_scale), round(img.height / self.screen_scale)))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=75)
        return base64.standard_b64encode(buf.getvalue()).decode(), img.width, img.height

    def tool_look_at_screen(self, question: str) -> str:
        if self.vision is None:
            raise ToolError("Модель для зрения не настроена (vision_model в config.yaml)")
        image, w, h = self.capture_screen()
        prompt = (
            f"Это скриншот экрана размером {w}x{h} пикселей. {question}\n"
            "Отвечай по-русски и по делу. Если речь о кнопке, поле или другом элементе, "
            "назови его примерные координаты центра в пикселях этого скриншота в виде (x, y)."
        )
        return f"Экран {w}x{h}. " + self.vision(image, prompt)

    def tool_mouse_click(self, x: int, y: int, button: str = "left", double: bool = False) -> str:
        import pyautogui

        rx, ry = round(x * self.screen_scale), round(y * self.screen_scale)
        pyautogui.click(rx, ry, clicks=2 if double else 1, button=button, interval=0.1)
        return f"Клик {button} в ({x}, {y})"

    def tool_scroll(self, amount: int) -> str:
        import pyautogui

        pyautogui.scroll(amount * 120 if SYSTEM == "Windows" else amount)
        return f"Прокручено на {amount}"

    def tool_type_text(self, text: str, press_enter: bool = False) -> str:
        import pyautogui
        import pyperclip

        # Защита от «Win+R → напечатай команду → Enter» по указке чужого сайта или поста:
        # ввод в консоль или команды, похожей на опасную, — только после «да».
        title = _active_window_title()
        if SHELL_WINDOW_RE.search(title.strip()):
            self._ask(f"Ввести в «{title.strip()[:40]}»: {text[:80]}?")
        elif press_enter and COMMANDISH_RE.search(text):
            self._ask(f"Ввести и отправить: {text[:80]}?")
        # pyautogui.write() не умеет кириллицу, поэтому вставляем через буфер.
        old = _safe_paste(pyperclip)
        pyperclip.copy(text)
        time.sleep(0.05)
        pyautogui.hotkey("command" if SYSTEM == "Darwin" else "ctrl", "v")
        time.sleep(0.15)
        if press_enter:
            pyautogui.press("enter")
        if old is not None:
            pyperclip.copy(old)
        return "Текст введён"

    def tool_press_keys(self, keys: list[str]) -> str:
        import pyautogui

        keys = [_normalize_key(k) for k in keys]
        low = {k.lower() for k in keys}
        if low & {"win", "winleft", "winright", "command"} and low & {"r", "x"}:
            self._ask("Открыть окно «Выполнить» / меню администратора (Win+R / Win+X)?")
        elif low & {"enter", "return"} and SHELL_WINDOW_RE.search(_active_window_title().strip()):
            self._ask("Нажать Enter в консоли — выполнить то, что там набрано?")
        if len(keys) == 1:
            pyautogui.press(keys[0])
        else:
            pyautogui.hotkey(*keys)
        return f"Нажато: {'+'.join(keys)}"

    def tool_media(self, action: str, times: int = 1) -> str:
        import pyautogui

        key = {
            "play_pause": "playpause",
            "next": "nexttrack",
            "previous": "prevtrack",
            "volume_up": "volumeup",
            "volume_down": "volumedown",
            "mute": "volumemute",
        }[action]
        pyautogui.press(key, presses=max(1, min(times, 50)), interval=0.03)
        return f"{action} x{times}"

    # --- Русская Рыбалка 4 --------------------------------------------------

    def tool_find_fishing_spot(self, target: str, water: str, open_link: bool = True) -> str:
        from .fishing import FishingSearch, describe, tg_app_link, tg_web_link

        search = FishingSearch(self.fishing_channels, discord_token=self.discord_token,
                               discord_channels=self.discord_channels)
        hits = search.search(target, water)
        sources = 3 + len(self.fishing_channels) + len(search.discord_channels if search.discord_token else [])
        if not hits and len(search.errors) >= sources:
            raise ToolError("Поиск в интернете сейчас недоступен (нет связи с поисковиком и Telegram): "
                            + "; ".join(search.errors[:3]))
        report = describe(target, water, hits)
        if hits and open_link:
            url = hits[0].url
            if "t.me/" in url and self.tg_open_mode == "web":
                url = tg_web_link(url)
            elif "t.me/" in url and self.tg_open_mode == "app":
                url = tg_app_link(url)
            elif "discord.com/channels/" in url and self.discord_open_mode == "app":
                url = url.replace("https://discord.com/", "discord://discord.com/", 1)
            safe_open(url, WEB_SCHEMES + ("tg", "discord"))  # ссылка из чужого поста — только веб и наши
            report += f"\nОткрыл в браузере: {hits[0].url}"
        return report

    # --- органайзер ------------------------------------------------------------

    def _org(self):
        if self.organizer is None:
            raise ToolError("Органайзер не подключён")
        return self.organizer

    def tool_add_task(self, text: str) -> str:
        return self._org().add_task(text)

    def tool_complete_task(self, text: str) -> str:
        return self._org().set_done(text)

    def tool_list_tasks(self) -> str:
        tasks = self._org().list_tasks()
        return "\n".join(f"- {t['text']}" for t in tasks) or "Задач нет"

    def tool_add_note(self, text: str) -> str:
        return self._org().add_note(text)

    def tool_list_notes(self) -> str:
        notes = self._org().data["notes"][-20:]
        return "\n".join(f"- {n['text']}" for n in notes) or "Заметок нет"

    def tool_add_event(self, text: str, date: str = "", time: str = "") -> str:
        return self._org().add_event(text, date or None, time or None)

    def tool_list_events(self, days: int = 7) -> str:
        from .organizer import human_date
        from datetime import date as _date

        evs = self._org().upcoming(int(days or 7))
        return "\n".join(f"- {human_date(_date.fromisoformat(e['date']))}"
                         f"{' ' + e['time'] if e.get('time') else ''}: {e['text']}" for e in evs) or "Событий нет"

    def tool_delete_item(self, kind: str, text: str) -> str:
        return self._org().delete(kind, text)

    # --- умный дом -----------------------------------------------------------

    def tool_smart_home(self, action: str, name: str = "") -> str:
        if self.home is None:
            raise ToolError("Умный дом не подключён: впиши ha_url и ha_token в config.yaml")
        from .home import HomeError

        try:
            if action == "list":
                return "\n".join(f"- {d['name']}: {d['state']}" for d in self.home.devices()) or "Устройств нет"
            if not name:
                raise ToolError("Какое устройство?")
            device = self.home.find(name)
            # Замки, ворота и гаражные двери — только после «да»: их может «попросить» и чужой текст.
            entry = device and (device["domain"] == "lock" or device["domain"] == "cover" and re.search(
                r"гараж|ворот|garage|gate|двер|door", device["name"], re.I))
            if entry:
                self._ask(f"{device['name']}: точно {'открыть' if action != 'off' else 'закрыть'}?")
            return self.home.act(action, name)
        except HomeError as e:
            raise ToolError(str(e)) from e

    # --- напоминания и скриншоты ---------------------------------------------

    def tool_set_timer(self, minutes: float, text: str) -> str:
        minutes = float(minutes)
        if not 0 < minutes <= 24 * 60:
            raise ToolError("Время напоминания должно быть от нескольких секунд до суток")
        fire_at = time.time() + minutes * 60

        def fire() -> None:
            with self._timers_lock:
                self._timers = [t for t in self._timers if t[2] is not timer]
            self.notify(f"Напоминаю: {text}")

        timer = threading.Timer(minutes * 60, fire)
        timer.daemon = True
        timer.start()
        with self._timers_lock:
            self._timers.append((fire_at, text, timer))
        return f"Напоминание через {_human_minutes(minutes)}: {text}"

    def tool_list_timers(self) -> str:
        with self._timers_lock:
            timers = sorted(self._timers, key=lambda t: t[0])
        if not timers:
            return "Напоминаний нет"
        now = time.time()
        return "\n".join(f"через {_human_minutes((at - now) / 60)}: {text}" for at, text, _ in timers)

    def tool_cancel_timers(self, text: str = "") -> str:
        needle = text.strip().lower()
        with self._timers_lock:
            hit = [t for t in self._timers if needle in t[1].lower()]
            for _, _, timer in hit:
                timer.cancel()
            self._timers = [t for t in self._timers if t not in hit]
        return f"Отменено напоминаний: {len(hit)}" if hit else "Таких напоминаний нет"

    def tool_save_screenshot(self) -> str:
        import mss
        import mss.tools

        folder = Path.home() / "Pictures" / "Jarvis"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"screen_{datetime.now():%Y-%m-%d_%H-%M-%S}.png"
        with mss.mss() as sct:
            shot = sct.grab(sct.monitors[1])
            mss.tools.to_png(shot.rgb, shot.size, output=str(path))
        return f"Скриншот сохранён: {path}"

    def tool_web_search(self, query: str) -> str:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
        results = DDGS().text(query, region="ru-ru", max_results=5)
        if not results:
            return "Ничего не нашлось"
        return "\n\n".join(f"{r.get('title', '')}\n{r.get('body', '')}\n{r.get('href', '')}" for r in results)

    # --- окна ---------------------------------------------------------------

    def tool_list_windows(self) -> str:
        gw = _windows_module()
        titles = [t for t in gw.getAllTitles() if t.strip()]
        return "\n".join(titles) or "Окон не найдено"

    def tool_focus_window(self, title: str) -> str:
        gw = _windows_module()
        matches = [w for w in gw.getAllWindows() if title.lower() in w.title.lower()]
        if not matches:
            raise ToolError(f"Окно с '{title}' не найдено")
        win = matches[0]
        if win.isMinimized:
            win.restore()
        win.activate()
        return f"Активно окно: {win.title}"

    # --- система ------------------------------------------------------------

    def tool_system_info(self) -> str:
        import psutil

        lines = [
            f"Время: {datetime.now():%d.%m.%Y %H:%M}",
            f"ОС: {platform.platform()}",
            f"CPU: {psutil.cpu_percent(interval=0.5)}%",
        ]
        mem = psutil.virtual_memory()
        lines.append(f"RAM: {mem.percent}% ({mem.used / 2**30:.1f} из {mem.total / 2**30:.1f} ГБ)")
        for part in psutil.disk_partitions(all=False):
            try:
                du = psutil.disk_usage(part.mountpoint)
            except (PermissionError, OSError):
                continue
            lines.append(f"Диск {part.mountpoint}: свободно {du.free / 2**30:.0f} ГБ из {du.total / 2**30:.0f}")
        battery = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
        if battery:
            lines.append(f"Батарея: {battery.percent:.0f}%{' (заряжается)' if battery.power_plugged else ''}")
        procs = []
        for p in psutil.process_iter(["name", "memory_info"]):
            if p.info["memory_info"]:
                procs.append((p.info["memory_info"].rss, p.info["name"]))
        procs.sort(reverse=True)
        lines.append("Больше всего памяти: " + ", ".join(f"{n} {r / 2**20:.0f} МБ" for r, n in procs[:5]))
        return "\n".join(lines)

    def tool_find_files(self, name_part: str, folder: str = "") -> str:
        root = Path(folder).expanduser() if folder else Path.home()
        if not root.exists():
            raise ToolError(f"Папка не найдена: {root}")
        needle = name_part.lower()
        found: list[str] = []
        deadline = time.monotonic() + 8
        skip = {"node_modules", "AppData", ".git", "__pycache__", "Library", ".cache"}
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]
            for n in dirnames + filenames:
                if needle in n.lower():
                    found.append(str(Path(dirpath) / n))
            if len(found) >= 30 or time.monotonic() > deadline:
                break
        return "\n".join(found[:30]) or "Ничего не найдено"

    def tool_clipboard(self, action: str, text: str = "") -> str:
        import pyperclip

        if action == "write":
            pyperclip.copy(text)
            return "Скопировано в буфер"
        content = _safe_paste(pyperclip) or ""
        return content[:5000] or "Буфер пуст"

    def tool_run_command(self, command: str, explanation: str = "") -> str:
        self._ask(f"Выполнить команду: {explanation or command}?")
        if SYSTEM == "Windows":
            argv = ["powershell", "-NoProfile", "-Command", command]
        else:
            argv = ["sh", "-c", command]
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=60, errors="replace")
        except subprocess.TimeoutExpired:
            raise ToolError("Команда выполнялась дольше 60 секунд и была остановлена")
        out = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
        out = out.strip() or "(пустой вывод)"
        if len(out) > 6000:
            out = out[:6000] + "\n...[обрезано]"
        return f"Код возврата {proc.returncode}\n{out}"

    def tool_kill_process(self, name: str) -> str:
        import psutil

        target = name.lower().removesuffix(".exe")
        victims = [p for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower().removesuffix(".exe") == target]
        if not victims:
            raise ToolError(f"Процесс {name} не найден")
        self._ask(f"Закрыть {name}? Найдено процессов: {len(victims)}.")
        for p in victims:
            try:
                p.terminate()
            except psutil.Error:
                pass
        psutil.wait_procs(victims, timeout=3)
        return f"Закрыто процессов {name}: {len(victims)}"

    def tool_power(self, action: str, delay_minutes: float = 0) -> str:
        delay = max(0.0, float(delay_minutes or 0))
        if action == "cancel":
            return self._power_cancel()
        if action not in ("lock", "sleep", "restart", "shutdown"):
            raise ToolError(f"Неизвестное действие: {action}")
        if action != "lock" and self.confirm_power:
            names = {"sleep": "Отправить компьютер в сон", "restart": "Перезагрузить компьютер",
                     "shutdown": "Выключить компьютер"}
            when = f" через {_human_minutes(delay)}" if delay else ""
            if not self.confirm(f"{names[action]}{when}?"):
                raise ToolError("Пользователь не подтвердил действие.")

        # Выключение/перезагрузку ставим самой системе: сработает, даже если Джарвис закрыть.
        seconds = max(10, round(delay * 60))  # 10 секунд — успеть сказать «отмена» и сохранить память
        if action in ("shutdown", "restart") and SYSTEM == "Windows":
            flag = "/s" if action == "shutdown" else "/r"
            subprocess.run(["shutdown", flag, "/t", str(seconds)], check=False)
            return f"{'Выключение' if action == 'shutdown' else 'Перезагрузка'} через {_human_minutes(seconds / 60)}. " \
                   f"Отменить: «отмени выключение»."
        if action in ("shutdown", "restart") and SYSTEM in ("Linux", "Darwin"):
            minutes = max(1, round(delay)) if delay else "now"
            flag = "-h" if action == "shutdown" else "-r"
            subprocess.Popen(["shutdown", flag, f"+{minutes}" if minutes != "now" else "now"])
            return f"{action} запланирован"

        commands = {
            "Windows": {
                "lock": ["rundll32.exe", "user32.dll,LockWorkStation"],
                # rundll32 SetSuspendState часто уводит в гибернацию — через .NET это честный сон.
                "sleep": ["powershell", "-NoProfile", "-Command",
                          "Add-Type -AssemblyName System.Windows.Forms; "
                          "[System.Windows.Forms.Application]::SetSuspendState('Suspend', $false, $false)"],
            },
            "Darwin": {"lock": ["pmset", "displaysleepnow"], "sleep": ["pmset", "sleepnow"]},
            "Linux": {"lock": ["loginctl", "lock-session"], "sleep": ["systemctl", "suspend"]},
        }[SYSTEM]
        if delay and action == "sleep":
            timer = threading.Timer(delay * 60, lambda: subprocess.Popen(commands["sleep"]))
            timer.daemon = True
            timer.start()
            with self._timers_lock:
                self._timers.append((time.time() + delay * 60, "сон компьютера", timer))
            return f"Сон через {_human_minutes(delay)}"
        subprocess.Popen(commands[action])
        return {"lock": "Компьютер заблокирован", "sleep": "Отправляю в сон"}[action]

    def _power_cancel(self) -> str:
        cancelled = []
        if SYSTEM == "Windows":
            r = subprocess.run(["shutdown", "/a"], capture_output=True, text=True, errors="replace")
            if r.returncode == 0:
                cancelled.append("выключение/перезагрузка")
        elif SYSTEM in ("Linux", "Darwin"):
            if subprocess.run(["shutdown", "-c"], capture_output=True).returncode == 0:
                cancelled.append("выключение")
        with self._timers_lock:
            sleepers = [t for t in self._timers if t[1] == "сон компьютера"]
            for _, _, timer in sleepers:
                timer.cancel()
            self._timers = [t for t in self._timers if t not in sleepers]
        if sleepers:
            cancelled.append("сон")
        return "Отменено: " + ", ".join(cancelled) if cancelled else "Ничего не было запланировано"

def _normalize_key(key: str) -> str:
    key = key.strip().lower()
    return {"win": "winleft", "windows": "winleft", "cmd": "command", "control": "ctrl", "escape": "esc",
            "return": "enter", "del": "delete", "pgup": "pageup", "pgdn": "pagedown"}.get(key, key)


def _safe_paste(pyperclip) -> str | None:
    try:
        return pyperclip.paste()
    except Exception:
        return None


def _windows_module():
    if SYSTEM != "Windows":
        raise ToolError("Работа с окнами пока поддерживается только на Windows")
    import pygetwindow

    return pygetwindow



def _human_minutes(minutes: float) -> str:
    total = max(0, round(minutes * 60))
    h, rest = divmod(total, 3600)
    m, sec = divmod(rest, 60)
    parts = []
    if h:
        parts.append(f"{h} ч")
    if m:
        parts.append(f"{m} мин")
    if sec and not h:
        parts.append(f"{sec} сек")
    return " ".join(parts) or "0 сек"


_SCHEMAS = {t["name"]: t["input_schema"].get("properties", {}) for t in TOOLS}


def _coerce_args(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Маленькие модели шлют числа строкой («5»), «true» вместо true и лишние поля — чиним."""
    props = _SCHEMAS.get(name)
    if props is None or not isinstance(args, dict):
        return args if isinstance(args, dict) else {}
    out: dict[str, Any] = {}
    for key, value in args.items():
        if key not in props:
            continue  # лишнее поле — выкидываем, иначе метод упадёт
        kind = props[key].get("type")
        try:
            if kind == "integer" and not isinstance(value, bool):
                value = int(round(float(value)))
            elif kind == "number" and not isinstance(value, bool):
                value = float(str(value).replace(",", "."))
            elif kind == "boolean" and isinstance(value, str):
                value = value.strip().lower() in ("true", "1", "yes", "да")
            elif kind == "string" and not isinstance(value, str):
                value = "" if value is None else str(value)
            elif kind == "array" and isinstance(value, str):
                value = [v.strip() for v in re.split(r"[+,]", value) if v.strip()]
        except (TypeError, ValueError):
            pass  # не смогли привести — пусть инструмент сам скажет, что не так
        out[key] = value
    return out


def describe_call(name: str, args: dict) -> str:
    """Человеческое описание шага агента — для HUD и панели («Нажимаю (640, 700)»)."""
    a = args or {}
    q = lambda v, n=40: (str(v)[:n] + "…") if len(str(v)) > n else str(v)
    return {
        "open_app": lambda: f"Открываю {q(a.get('name', ''))}",
        "open_url": lambda: f"Открываю {q(a.get('url', ''))}",
        "browser_search": lambda: f"Ищу в браузере «{q(a.get('query', ''))}»",
        "web_search": lambda: f"Ищу в интернете «{q(a.get('query', ''))}»",
        "look_at_screen": lambda: "Смотрю на экран",
        "mouse_click": lambda: f"Нажимаю ({a.get('x')}, {a.get('y')})",
        "scroll": lambda: "Прокручиваю",
        "type_text": lambda: f"Печатаю «{q(a.get('text', ''), 30)}»",
        "press_keys": lambda: f"Жму {'+'.join(a.get('keys', []))}",
        "media": lambda: "Звук и музыка",
        "system_info": lambda: "Смотрю, как дела у компа",
        "find_files": lambda: f"Ищу файлы «{q(a.get('name_part', ''))}»",
        "run_command": lambda: f"Выполняю команду: {q(a.get('explanation') or a.get('command', ''))}",
        "kill_process": lambda: f"Закрываю {a.get('name', '')}",
        "power": lambda: f"Питание: {a.get('action', '')}",
        "set_timer": lambda: f"Ставлю напоминание: {q(a.get('text', ''))}",
        "find_fishing_spot": lambda: f"Ищу точку: {a.get('target', '')}, {a.get('water', '')}",
        "add_task": lambda: f"Добавляю задачу «{q(a.get('text', ''))}»",
        "add_event": lambda: f"Записываю в календарь «{q(a.get('text', ''))}»",
        "remember": lambda: "Запоминаю",
        "smart_home": lambda: f"Умный дом: {a.get('name', '') or 'устройства'}",
        "save_screenshot": lambda: "Сохраняю скриншот",
    }.get(name, lambda: (f"MCP: {name[4:]}" if name.startswith("mcp_") else name.replace("_", " ").capitalize()))()
