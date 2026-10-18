"""Задачи, заметки и календарь. Хранятся в memory/organizer.json.

Голосом: «добавь задачу купить подарок», «запиши заметку …», «24 октября день рождения мамы»,
«завтра в 18:00 тренировка», «что у меня на сегодня», «какие задачи», «задача подарок выполнена».
События с временем напоминаются вслух, без времени — утром при запуске.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from datetime import date, datetime, timedelta

MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6, "июл": 7, "август": 8,
    "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}
MONTH_NAMES = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
               "октября", "ноября", "декабря")
WEEKDAYS = {"понедельник": 0, "вторник": 1, "сред": 2, "четверг": 3, "пятниц": 4, "суббот": 5, "воскресень": 6}
WEEKDAY_NAMES = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
ORDINALS = {
    "первое": 1, "второе": 2, "третье": 3, "четвертое": 4, "пятое": 5, "шестое": 6, "седьмое": 7, "восьмое": 8,
    "девятое": 9, "десятое": 10, "одиннадцатое": 11, "двенадцатое": 12, "тринадцатое": 13, "четырнадцатое": 14,
    "пятнадцатое": 15, "шестнадцатое": 16, "семнадцатое": 17, "восемнадцатое": 18, "девятнадцатое": 19,
    "двадцатое": 20, "тридцатое": 30,
}


def _path():
    from . import learning  # путь берём каждый раз — так его можно подменить в тестах

    return learning.MEMORY_DIR / "organizer.json"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s:.-]", " ", text.lower().replace("ё", "е"))).strip(" .")


def parse_date(text: str, today: date | None = None) -> tuple[date | None, str | None, str]:
    """Ищет в тексте дату и время. Возвращает (дата, "HH:MM" или None, текст без даты)."""
    today = today or date.today()
    t = _norm(text)
    found: date | None = None
    hhmm: str | None = None

    m = re.search(r"\b(?:в|к|на)?\s*(\d{1,2})[:.](\d{2})\b", t)
    if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
        hhmm = f"{int(m.group(1)):02d}:{m.group(2)}"
        t = (t[: m.start()] + t[m.end():]).strip()
    else:
        m = re.search(r"\bв (\d{1,2}) (?:час\w*|утра|вечера|дня)\b", t)
        if m:
            h = int(m.group(1)) + (12 if "вечера" in m.group(0) or ("дня" in m.group(0) and int(m.group(1)) < 6) else 0)
            if h < 24:
                hhmm = f"{h:02d}:00"
                t = (t[: m.start()] + t[m.end():]).strip()

    rel = {"сегодня": 0, "завтра": 1, "послезавтра": 2}
    for word, add in rel.items():
        if re.search(rf"\b{word}\b", t):
            found = today + timedelta(days=add)
            t = re.sub(rf"\b{word}\b", " ", t)
            break
    if not found:
        m = re.search(r"\b((?:двадцать |тридцать )?(?:" + "|".join(ORDINALS) + r")|\d{1,2})\s+("
                      + "|".join(MONTHS) + r")\w*", t)
        if m:
            word = m.group(1)
            if word.isdigit():
                day = int(word)
            else:
                parts = word.split()
                day = ORDINALS[parts[-1]] + ({"двадцать": 20, "тридцать": 30}[parts[0]] if len(parts) == 2 else 0)
            month = next(v for k, v in MONTHS.items() if m.group(2).startswith(k))
            try:
                found = date(today.year, month, day)
                if found < today - timedelta(days=1):
                    found = date(today.year + 1, month, day)  # «5 января» в декабре — это следующий год
            except ValueError:
                found = None
            t = t[: m.start()] + t[m.end():]
    if not found:
        m = re.search(r"\b(?:в|во)\s+(" + "|".join(WEEKDAYS) + r")\w*", t)
        if m:
            wd = next(v for k, v in WEEKDAYS.items() if m.group(1).startswith(k))
            found = today + timedelta(days=(wd - today.weekday()) % 7 or 7)
            t = t[: m.start()] + t[m.end():]
    if hhmm and not found:
        found = today
    return found, hhmm, re.sub(r"\s+", " ", t).strip(" ,.-")


def human_date(d: date, today: date | None = None) -> str:
    today = today or date.today()
    if d == today:
        return "сегодня"
    if d == today + timedelta(days=1):
        return "завтра"
    return f"{d.day} {MONTH_NAMES[d.month - 1]}"


class Organizer:
    def __init__(self, notify=None):
        self._lock = threading.Lock()
        self.notify = notify or print
        self._timers: dict[str, threading.Timer] = {}
        self.data = self._load()

    # --- хранение ------------------------------------------------------------

    def _load(self) -> dict:
        try:
            data = json.loads(_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        for key in ("tasks", "notes", "events"):
            data.setdefault(key, [])
        return data

    def _save(self) -> None:
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def _find(self, kind: str, query: str) -> dict | None:
        q = _norm(query)
        items = self.data[kind]
        for it in items:
            if it["id"] == query:
                return it
        stems = [w[:5] for w in q.split() if len(w) > 2]
        best = [it for it in items if stems and all(s in _norm(it["text"]) for s in stems)]
        return best[0] if best else None

    # --- задачи ---------------------------------------------------------------

    def add_task(self, text: str) -> str:
        text = text.strip(" .")
        if not text:
            raise ValueError("Пустая задача")
        with self._lock:
            self.data["tasks"].append({"id": uuid.uuid4().hex[:8], "text": text, "done": False, "created": time.time()})
            self._save()
        return f"Добавил задачу: {text}"

    def set_done(self, query: str, done: bool = True) -> str:
        with self._lock:
            it = self._find("tasks", query)
            if not it:
                return f"Не нашёл задачу «{query}»"
            it["done"] = done
            self._save()
        return f"Задача «{it['text']}» {'выполнена' if done else 'снова в работе'}"

    def list_tasks(self, include_done: bool = False) -> list[dict]:
        return [t for t in self.data["tasks"] if include_done or not t["done"]]

    # --- заметки --------------------------------------------------------------

    def add_note(self, text: str) -> str:
        text = text.strip(" .")
        if not text:
            raise ValueError("Пустая заметка")
        with self._lock:
            self.data["notes"].append({"id": uuid.uuid4().hex[:8], "text": text, "created": time.time()})
            self._save()
        return "Записал заметку"

    # --- календарь ------------------------------------------------------------

    def add_event(self, text: str, when: date | str | None = None, hhmm: str | None = None) -> str:
        if isinstance(when, str):
            when = date.fromisoformat(when)
        if when is None:
            when, hhmm2, text = parse_date(text)
            hhmm = hhmm or hhmm2
        if when is None:
            raise ValueError("Не понял дату события")
        text = text.strip(" .,")
        if not text:
            raise ValueError("Пустое событие")
        ev = {"id": uuid.uuid4().hex[:8], "date": when.isoformat(), "time": hhmm, "text": text}
        with self._lock:
            self.data["events"].append(ev)
            self.data["events"].sort(key=lambda e: (e["date"], e.get("time") or ""))
            self._save()
        self._schedule(ev)
        at = f" в {hhmm}" if hhmm else ""
        return f"Записал в календарь: {human_date(when)}{at} — {text}"

    def events_on(self, day: date) -> list[dict]:
        return [e for e in self.data["events"] if e["date"] == day.isoformat()]

    def upcoming(self, days: int = 7, today: date | None = None) -> list[dict]:
        today = today or date.today()
        end = (today + timedelta(days=days)).isoformat()
        return [e for e in self.data["events"] if today.isoformat() <= e["date"] <= end]

    # --- удаление -------------------------------------------------------------

    def delete(self, kind: str, query: str) -> str:
        kind = {"task": "tasks", "note": "notes", "event": "events"}.get(kind, kind)
        if kind not in ("tasks", "notes", "events"):
            raise ValueError("Что удалить: tasks, notes или events")
        with self._lock:
            it = self._find(kind, query)
            if not it:
                return f"Не нашёл «{query}»"
            self.data[kind].remove(it)
            self._save()
        timer = self._timers.pop(it["id"], None)
        if timer:
            timer.cancel()
        return f"Удалил: {it['text']}"

    # --- сводка и напоминания -------------------------------------------------

    def summary(self, day: date | None = None) -> str:
        day = day or date.today()
        events = self.events_on(day)
        tasks = self.list_tasks()
        parts = []
        if events:
            parts.append("События: " + "; ".join((f"{e['time']} " if e.get("time") else "") + e["text"] for e in events))
        if tasks:
            parts.append(f"Задач: {len(tasks)} — " + ", ".join(t["text"] for t in tasks[:5]))
        if not parts:
            return f"На {human_date(day)} ничего не запланировано."
        return f"На {human_date(day)}: " + ". ".join(parts) + "."

    def _schedule(self, ev: dict) -> None:
        if not ev.get("time"):
            return
        at = datetime.fromisoformat(f"{ev['date']}T{ev['time']}")
        delay = (at - datetime.now()).total_seconds() - 600  # за 10 минут
        if delay < -600 or delay > 2 * 86400:  # прошло или далеко — запланируем при следующем запуске
            return
        timer = threading.Timer(max(1, delay), lambda: self.notify(f"Напоминаю: в {ev['time']} — {ev['text']}"))
        timer.daemon = True
        timer.start()
        self._timers[ev["id"]] = timer

    def start(self) -> str | None:
        """При запуске: поставить напоминания на ближайшие события и вернуть утреннюю сводку на сегодня."""
        for ev in self.upcoming(2):
            self._schedule(ev)
        today = self.events_on(date.today())
        if today:
            return "Сегодня: " + "; ".join((f"в {e['time']} " if e.get("time") else "") + e["text"] for e in today) + "."
        return None


# --- голосовые команды без нейросети -------------------------------------------

TASK_ADD_RE = re.compile(r"^(?:добавь|создай|запиши|новая) (?:задачу|задача|в задачи|дело)\s+(?P<text>.+)$")
NOTE_ADD_RE = re.compile(r"^(?:запиши|запомни|сохрани|добавь|создай) (?:заметку|в заметки|заметка)\s+(?P<text>.+)$")
TASK_DONE_RE = re.compile(r"^(?:задача\s+)?(?P<text>.+?)\s+(?:выполнен\w*|сделан\w*|готов\w*)$|^(?:отметь|закрой|выполни)"
                          r" (?:задачу\s+)?(?P<text2>.+?)(?:\s+как\s+(?:выполненн\w*|сделанн\w*))?$")
TASKS_LIST_RE = re.compile(r"^(?:какие|покажи|перечисли|список)(?: у меня)? (?:задачи|дела|задач)(?: на сегодня)?$")
TODAY_RE = re.compile(r"^(?:что у меня|что|какие планы)(?: на| в)? (?P<day>сегодня|завтра)$|^планы на (?P<day2>сегодня|завтра)$")
EVENT_ADD_RE = re.compile(r"^(?:запиши в календарь|добавь в календарь|добавь событие|в календарь|напомни)\s+(?P<text>.+)$")
QUESTION_RE = re.compile(r"\b(какая|какой|какое|какие|что|сколько|где|когда|как|почему|зачем|покажи|открой|включи|"
                         r"найди|поставь|сделай|скажи|расскажи|погода|курс|напомни через)\b")
DELETE_RE = re.compile(r"^(?:удали|убери|сотри) (?P<kind>задачу|заметку|событие)\s+(?P<text>.+)$")


def quick_organizer(text: str, org: Organizer) -> str | None:
    t = _norm(text)
    m = TASK_ADD_RE.match(t)
    if m:
        return org.add_task(m.group("text"))
    m = NOTE_ADD_RE.match(t)
    if m:
        return org.add_note(m.group("text"))
    m = DELETE_RE.match(t)
    if m:
        kind = {"задачу": "tasks", "заметку": "notes", "событие": "events"}[m.group("kind")]
        return org.delete(kind, m.group("text"))
    if TASKS_LIST_RE.match(t):
        tasks = org.list_tasks()
        return ("Задачи: " + "; ".join(x["text"] for x in tasks) + ".") if tasks else "Задач нет, красота."
    m = TODAY_RE.match(t)
    if m:
        word = m.group("day") or m.group("day2")
        return org.summary(date.today() + timedelta(days=1 if word == "завтра" else 0))
    m = TASK_DONE_RE.match(t)
    if m:
        query = m.group("text") or m.group("text2")
        if query and org._find("tasks", query):
            return org.set_done(query)
    # «24 октября день рождения мамы», «завтра в 18:00 тренировка», «в пятницу сдать отчёт»
    m = EVENT_ADD_RE.match(t)
    body = m.group("text") if m else t
    when, hhmm, rest = parse_date(body)
    # Без явного «в календарь» считаем событием только фразу, которая начинается с даты и не похожа
    # на вопрос или команду: «24 октября день рождения мамы» — да, «завтра какая погода» — нет.
    implicit = (not m and re.match(r"^(\d{1,2}|двадцать|тридцать|" + "|".join(ORDINALS) + r") \w+|^(завтра|послезавтра|сегодня|в |во )", t)
                and not QUESTION_RE.search(rest))
    if when and rest and (m or implicit):
        if m and m.group(0).startswith("напомни") and re.search(r"через", t):
            return None  # «напомни через 10 минут» — это таймер, не календарь
        return org.add_event(rest, when, hhmm)
    return None
