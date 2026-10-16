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
    (r"(сделай |сохрани )?(скрин|скриншот|снимок экрана)( сохрани| сделай)?|сохрани (экран|трофей)", "save_screenshot", {}, "Скриншот сохранён."),
    (r"(отмени|удали|сбрось) (все )?(напоминания|напоминание|таймеры|таймер)", "cancel_timers", {"text": ""}, "Напоминания отменены."),
]

NUMBERS = {
    "ноль": 0, "один": 1, "одну": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5,
    "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11, "двенадцать": 12,
    "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17,
    "восемнадцать": 18, "девятнадцать": 19, "двадцать": 20, "тридцать": 30, "сорок": 40,
    "пятьдесят": 50, "шестьдесят": 60, "семьдесят": 70, "восемьдесят": 80, "девяносто": 90, "сто": 100,
}
UNITS = {"секунд": 1 / 60, "минут": 1, "час": 60}
# «через пятнадцать минут», «через 2 часа», «через полчаса», «через минуту»
TIMER_RE = re.compile(
    r"^(?:напомни|напомнить|поставь (?:таймер|напоминание))(?: мне)?(?: через| на)? ?(?P<amount>.*?) ?"
    r"(?P<unit>секунд\w*|минут\w*|час\w*|полчаса|полтора часа)"
    r"(?:\s+(?:что(?:бы)?|чтоб|о том что|про)(?=\s))?\s*(?P<text>.*)$"
)


def parse_amount(words: str) -> float | None:
    """«двадцать пять» -> 25, «2» -> 2, «» -> 1 («через минуту»), «полторы» -> 1.5."""
    words = words.strip()
    if not words:
        return 1
    if re.fullmatch(r"\d+([.,]\d+)?", words):
        return float(words.replace(",", "."))
    if words in ("полторы", "полтора"):
        return 1.5
    total = 0
    for w in words.split():
        if w not in NUMBERS:
            return None
        total += NUMBERS[w]
    return total


def parse_timer(t: str) -> tuple[float, str] | None:
    m = TIMER_RE.match(t)
    if not m:
        return None
    unit = m.group("unit")
    if unit == "полчаса":
        minutes = 30.0
    elif unit == "полтора часа":
        minutes = 90.0
    else:
        amount = parse_amount(m.group("amount"))
        if amount is None:
            return None
        key = next(k for k in UNITS if unit.startswith(k))
        minutes = amount * UNITS[key]
    text = m.group("text").strip() or "время вышло"
    return minutes, text

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

    timer = parse_timer(t)
    if timer:
        minutes, what = timer
        print(f"[quick] set_timer {minutes} мин: {what}")
        try:
            return run_tool("set_timer", {"minutes": minutes, "text": what}).replace("Напоминание", "Напомню", 1)
        except Exception as e:
            print(f"[quick] не вышло: {e}")
            return None

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
