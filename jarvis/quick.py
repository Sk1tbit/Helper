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
    (r"заблокируй (комп|компьютер|экран|пк)", "power", {"action": "lock", "delay_minutes": 0}, "Блокирую."),
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


VOLUME_RE = re.compile(r"^(?:поставь |сделай |установи )?громкость (?:на )?(?P<n>.+?)(?: процент\w*)?$")
TIME_RE = re.compile(r"^(?:сколько (?:сейчас )?времени|который (?:сейчас )?час|сколько время|время)$")
DATE_RE = re.compile(r"^(?:какое (?:сегодня )?число|какой сегодня день|какая сегодня дата|какое сегодня число и день)$")
WEATHER_RE = re.compile(r"^(?:какая |что по |че по |чё по |скажи )?погод\w*(?: (?:в|во) (?P<city>[\w\s-]+?))?(?: сегодня| сейчас| на улице)?$")
WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
          "октября", "ноября", "декабря")
DEFAULT_CITY = {"name": "Москва"}
RATE_RE = re.compile(r"^(?:(?:какой |скажи )?(?:курс\w*|сколько стоит)|че по|чё по|что по)\s*(?P<what>доллар\w*|бакс\w*|евро|юан\w*|тенге|фунт\w*|лир\w*|валют\w*)?(?: сегодня| сейчас)?$")
RATE_CODES = {"доллар": "USD", "бакс": "USD", "евро": "EUR", "юан": "CNY", "тенге": "KZT", "фунт": "GBP", "лир": "TRY"}


def info_command(t: str) -> str | None:
    """Время, дата, погода — без нейросети."""
    from datetime import datetime

    now = datetime.now()
    if TIME_RE.match(t):
        return f"Сейчас {now:%H:%M}."
    if DATE_RE.match(t):
        return f"Сегодня {WEEKDAYS[now.weekday()]}, {now.day} {MONTHS[now.month - 1]}."
    m = WEATHER_RE.match(t)
    if m:
        return weather(m.group("city") or DEFAULT_CITY["name"])
    m = RATE_RE.match(t)
    if m and (m.group("what") or t.startswith("курс")):
        from .rates import say_rate

        what = m.group("what") or ""
        code = next((c for k, c in RATE_CODES.items() if what.startswith(k)), None)
        return say_rate(code)
    return None


def weather(city: str) -> str | None:
    """Погода голосом (wttr.in, без ключей)."""
    from .rates import weather_data

    w = weather_data(city)
    if not w:
        return None
    sign = lambda v: f"плюс {v}" if v > 0 else (f"минус {-v}" if v < 0 else "ноль")
    return (f"{w['city']}: {w['desc']}, {sign(w['temp'])}, ощущается как {sign(w['feels'])}, "
            f"ветер {w['wind']} метров в секунду.")


PC = r"(?:комп\w*|пк|ноут\w*|систем\w*)"
DELAY = r"(?: через (?P<amount>.*?) ?(?P<unit>секунд\w*|минут\w*|час\w*|полчаса|полтора часа))?"
POWER_RES = [
    ("cancel", re.compile(r"^(?:отмени|отменить|стоп|отбой) (?:выключени\w*|перезагрузк\w*|сон|выключение компа)$"
                          r"|^не (?:выключай|перезагружай)(?: " + PC + r")?$")),
    ("shutdown", re.compile(r"^(?:выключи|выключить|выруби|вырубай|вырубить|отключи|заверши работу) " + PC + DELAY + r"$")),
    ("restart", re.compile(r"^(?:перезагрузи|перезагрузить|ребутни|перезапусти) " + PC + DELAY + r"$")),
    ("sleep", re.compile(r"^(?:(?:отправь|переведи|уведи)(?: " + PC + r")? в (?:сон|спящий режим)|усыпи " + PC
                         + r"|" + PC + r" в сон)" + DELAY + r"$")),
]


def parse_power(t: str) -> tuple[str, float] | None:
    """«выключи комп через полчаса» -> ("shutdown", 30). «отмени выключение» -> ("cancel", 0)."""
    for action, regex in POWER_RES:
        m = regex.match(t)
        if not m:
            continue
        unit = m.groupdict().get("unit")
        if not unit:
            return action, 0
        if unit == "полчаса":
            return action, 30
        if unit == "полтора часа":
            return action, 90
        amount = parse_amount(m.group("amount") or "")
        if amount is None:
            return None
        key = next(k for k in UNITS if unit.startswith(k))
        return action, amount * UNITS[key]
    return None


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
COMPLEX_RE = re.compile(r"\b(папк\w*|файл\w*|вкладк\w*|документ\w*|сайт\w*|ссылк\w*|последн\w*|где|который|которую"
                        r"|и|а потом|потом|затем|напиши|найди|включи|поставь|набери|скажи)\b")
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

    info = info_command(t)
    if info is not None:
        return info

    vol = VOLUME_RE.match(t)
    if vol:
        n = vol.group("n").strip()
        level = {"максимум": 100, "максимальную": 100, "полную": 100, "минимум": 0, "ноль": 0,
                 "половину": 50, "середину": 50}.get(n, None)
        if level is None:
            level = parse_amount(n)
        if level is not None and 0 <= level <= 100:
            print(f"[quick] громкость {level}%")
            try:
                # Точной громкости через мультимедийные клавиши нет: в ноль и вверх (1 нажатие ≈ 2%).
                run_tool("media", {"action": "volume_down", "times": 50})
                if level:
                    run_tool("media", {"action": "volume_up", "times": round(level / 2)})
            except Exception as e:
                print(f"[quick] не вышло: {e}")
                return None
            return f"Громкость {int(level)} процентов."

    power = parse_power(t)
    if power:
        action, minutes = power
        print(f"[quick] power {action} через {minutes} мин")
        try:
            return run_tool("power", {"action": action, "delay_minutes": minutes})
        except ToolError as e:
            return "Ок, не трогаю." if "не подтвердил" in str(e) else None
        except Exception as e:
            print(f"[quick] не вышло: {e}")
            return None

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
