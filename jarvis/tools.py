"""Инструменты, через которые Claude управляет компьютером.

Каждый инструмент — метод ToolBox с описанием-схемой в TOOLS. Тяжёлые библиотеки
(pyautogui, mss) импортируются лениво, чтобы текстовый режим работал где угодно.
"""

from __future__ import annotations

import base64
import io
import os
import platform
import subprocess
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote_plus

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

TOOLS: list[dict[str, Any]] = [
    {
        "name": "open_app",
        "description": "Запускает программу или открывает файл/папку по имени или пути. "
        "Понимает частые названия по-русски (блокнот, калькулятор, телеграм, стим...).",
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
            "required": ["query", "engine"],
            "additionalProperties": False,
        },
    },
    {
        "name": "screenshot",
        "description": "Делает снимок экрана. Используй, чтобы увидеть, что сейчас открыто, "
        "найти кнопку перед кликом или подсказать пользователю, что делать на экране.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "mouse_click",
        "description": "Кликает мышью в точку. Координаты — в пикселях последнего скриншота.",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "button": {"type": "string", "enum": ["left", "right", "middle"]},
                "double": {"type": "boolean"},
            },
            "required": ["x", "y", "button", "double"],
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
            "required": ["text", "press_enter"],
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
            "required": ["action", "times"],
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
            "required": ["name_part", "folder"],
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
            "required": ["action", "text"],
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
            "required": ["command", "explanation"],
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
        "description": "Блокировка, сон, перезагрузка или выключение компьютера. Всё, кроме lock, требует подтверждения.",
        "input_schema": {
            "type": "object",
            "properties": {"action": {"type": "string", "enum": ["lock", "sleep", "restart", "shutdown"]}},
            "required": ["action"],
            "additionalProperties": False,
        },
    },
]

for _tool in TOOLS:
    _tool["strict"] = True


class ToolError(Exception):
    pass


class ToolBox:
    def __init__(self, confirm: Callable[[str], bool], confirm_dangerous: bool = True):
        self.confirm = confirm
        self.confirm_dangerous = confirm_dangerous
        # Во сколько раз реальный экран больше отправленного скриншота.
        self.screen_scale = 1.0

    def run(self, name: str, args: dict[str, Any]) -> str | list[dict[str, Any]]:
        method = getattr(self, f"tool_{name}", None)
        if method is None:
            raise ToolError(f"Нет такого инструмента: {name}")
        return method(**args)

    def _ask(self, question: str) -> None:
        if self.confirm_dangerous and not self.confirm(question):
            raise ToolError("Пользователь не подтвердил действие.")

    # --- запуск и браузер ----------------------------------------------------

    def tool_open_app(self, name: str) -> str:
        target = APP_ALIASES.get(SYSTEM, {}).get(name.strip().lower(), name.strip())
        if target.startswith(("http://", "https://")):
            webbrowser.open(target)
        elif SYSTEM == "Windows":
            try:
                os.startfile(target)  # type: ignore[attr-defined]
            except OSError:
                # `start` находит программы из "App Paths" (chrome, winword...), которых нет в PATH.
                subprocess.Popen(f'start "" "{target}"', shell=True)
        elif SYSTEM == "Darwin":
            path = Path(target).expanduser()
            subprocess.Popen(["open", str(path)] if path.exists() else ["open", "-a", target])
        else:
            path = Path(target).expanduser()
            if path.exists():
                subprocess.Popen(["xdg-open", str(path)])
            else:
                subprocess.Popen(target, shell=True, start_new_session=True)
        return f"Запущено: {target}"

    def tool_open_url(self, url: str) -> str:
        if "://" not in url:
            url = "https://" + url
        webbrowser.open(url)
        return f"Открыто: {url}"

    def tool_browser_search(self, query: str, engine: str) -> str:
        base = {
            "google": "https://www.google.com/search?q=",
            "yandex": "https://yandex.ru/search/?text=",
            "youtube": "https://www.youtube.com/results?search_query=",
        }[engine]
        webbrowser.open(base + quote_plus(query))
        return f"Открыт поиск {engine}: {query}"

    # --- экран, мышь, клавиатура --------------------------------------------

    def tool_screenshot(self) -> list[dict[str, Any]]:
        import mss
        from PIL import Image

        with mss.mss() as sct:
            shot = sct.grab(sct.monitors[1])
            img = Image.frombytes("RGB", shot.size, shot.rgb)
        self.screen_scale = max(1.0, img.width / SCREENSHOT_MAX_WIDTH)
        if self.screen_scale > 1:
            img = img.resize((round(img.width / self.screen_scale), round(img.height / self.screen_scale)))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=70)
        data = base64.standard_b64encode(buf.getvalue()).decode()
        return [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}},
            {"type": "text", "text": f"Скриншот {img.width}x{img.height}. Координаты для кликов — в этих пикселях."},
        ]

    def tool_mouse_click(self, x: int, y: int, button: str, double: bool) -> str:
        import pyautogui

        rx, ry = round(x * self.screen_scale), round(y * self.screen_scale)
        pyautogui.click(rx, ry, clicks=2 if double else 1, button=button, interval=0.1)
        return f"Клик {button} в ({x}, {y})"

    def tool_scroll(self, amount: int) -> str:
        import pyautogui

        pyautogui.scroll(amount * 120 if SYSTEM == "Windows" else amount)
        return f"Прокручено на {amount}"

    def tool_type_text(self, text: str, press_enter: bool) -> str:
        import pyautogui
        import pyperclip

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
        if len(keys) == 1:
            pyautogui.press(keys[0])
        else:
            pyautogui.hotkey(*keys)
        return f"Нажато: {'+'.join(keys)}"

    def tool_media(self, action: str, times: int) -> str:
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

    def tool_find_files(self, name_part: str, folder: str) -> str:
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

    def tool_clipboard(self, action: str, text: str) -> str:
        import pyperclip

        if action == "write":
            pyperclip.copy(text)
            return "Скопировано в буфер"
        content = _safe_paste(pyperclip) or ""
        return content[:5000] or "Буфер пуст"

    def tool_run_command(self, command: str, explanation: str) -> str:
        self._ask(f"Выполнить команду: {explanation}?")
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

    def tool_power(self, action: str) -> str:
        if action != "lock":
            names = {"sleep": "Отправить компьютер в сон", "restart": "Перезагрузить компьютер", "shutdown": "Выключить компьютер"}
            self._ask(f"{names[action]}?")
        commands = {
            "Windows": {
                "lock": "rundll32.exe user32.dll,LockWorkStation",
                "sleep": "rundll32.exe powrprof.dll,SetSuspendState 0,1,0",
                "restart": "shutdown /r /t 5",
                "shutdown": "shutdown /s /t 5",
            },
            "Darwin": {
                "lock": "pmset displaysleepnow",
                "sleep": "pmset sleepnow",
                "restart": "osascript -e 'tell app \"System Events\" to restart'",
                "shutdown": "osascript -e 'tell app \"System Events\" to shut down'",
            },
            "Linux": {
                "lock": "loginctl lock-session",
                "sleep": "systemctl suspend",
                "restart": "systemctl reboot",
                "shutdown": "systemctl poweroff",
            },
        }[SYSTEM]
        subprocess.Popen(commands[action], shell=True)
        return f"Выполняется: {action}"


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

