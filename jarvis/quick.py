"""Простые команды, которые выполняются сразу, без нейросети.

«Открой телеграм», «громче», «пауза» — нейросеть тут не нужна, а маленькие локальные модели
ещё и путаются с вызовом инструментов. Всё, что не распознано здесь, уходит в нейросеть.
"""

from __future__ import annotations

import re
from typing import Callable

from .tools import ToolError

# (регулярка на всю фразу, инструмент, аргументы, что сказать)
MEDIA_COMMANDS: list[tuple[str, str, dict, str]] = [
    (r"(сделай )?(погромче|громче)( сделай)?|прибавь( звук| громкость)?", "media", {"action": "volume_up", "times": 5}, "Сделал громче."),
    (r"(сделай )?(потише|тише)( сделай)?|убавь( звук| громкость)?", "media", {"action": "volume_down", "times": 5}, "Сделал тише."),
    (r"(выключи|отключи|убери) звук|без звука|замолчи музыка", "media", {"action": "mute", "times": 1}, "Звук выключен."),
    (r"(поставь )?(на )?паузу?|пауза|стоп музыка|продолжи|продолжай|играй|воспроизведи", "media", {"action": "play_pause", "times": 1}, "Готово."),
    (r"(следующ\w*|дальше|переключи)( трек| песню| песня| видео)?", "media", {"action": "next", "times": 1}, "Следующий."),
    (r"(предыдущ\w*|назад)( трек| песню| песня| видео)?", "media", {"action": "previous", "times": 1}, "Предыдущий."),
    (r"сверни (все|всё)( окна)?|покажи рабочий стол", "press_keys", {"keys": ["win", "d"]}, "Свернул."),
    (r"заблокируй (комп|компьютер|экран|пк)", "power", {"action": "lock"}, "Блокирую."),
]

OPEN_RE = re.compile(r"^(открой|откройте|запусти|запустите)\s+(.+)$")
FILLER_RE = re.compile(r"\b(пожалуйста|мне|программу|приложение|прогу)\b")
# Это лучше отдать нейросети: тут нужно понять, что именно открывать.
COMPLEX_RE = re.compile(r"\b(папк\w*|файл\w*|вкладк\w*|документ\w*|сайт\w*|ссылк\w*|последн\w*|где|который|которую)\b")
DOMAIN_RE = re.compile(r"^[\w-]+(\.[\w-]+)+(/\S*)?$")


def _norm(text: str) -> str:
    text = text.lower().replace("ё", "е")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s./-]", " ", text)).strip(" .")


def quick_command(text: str, run_tool: Callable[[str, dict], str]) -> str | None:
    """Выполняет простую команду. Возвращает, что сказать, или None — тогда нужна нейросеть."""
    t = _norm(text)
    if not t:
        return None

    for pattern, tool, args, reply in MEDIA_COMMANDS:
        if re.fullmatch(pattern, t):
            print(f"[quick] {tool} {args}")
            try:
                run_tool(tool, dict(args))
            except Exception as e:  # не вышло — пусть разбирается нейросеть
                print(f"[quick] не вышло: {e}")
                return None
            return reply

    m = OPEN_RE.match(t)
    if not m:
        return None
    target = re.sub(r"\s+", " ", FILLER_RE.sub(" ", m.group(2))).strip()
    if not target or COMPLEX_RE.search(target):
        return None
    tool, args = ("open_url", {"url": target}) if DOMAIN_RE.match(target) else ("open_app", {"name": target})
    print(f"[quick] {tool} {args}")
    try:
        result = run_tool(tool, args)
    except ToolError as e:
        print(f"[quick] {e} — передаю нейросети")
        return None
    except Exception as e:
        print(f"[quick] ошибка {type(e).__name__}: {e} — передаю нейросети")
        return None
    print(f"[quick] {result}")
    return "Открываю."
