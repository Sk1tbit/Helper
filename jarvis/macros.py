"""Голосовые макросы: одна фраза — несколько действий.

«Запомни команду погнали: открой дискорд, открой стим, громкость 30» — и потом
«погнали» выполняет всё по очереди. Хранятся в memory/macros.json.
Понимает и текст без знаков препинания (как его отдаёт Vosk): шаги делим по глаголам.
"""

from __future__ import annotations

import json
import re
import threading

from .learning import MEMORY_DIR

MACROS_FILE = MEMORY_DIR / "macros.json"

# С этих слов начинается новый шаг, если нет запятых.
STEP_STARTS = (
    "открой", "запусти", "включи", "выключи", "закрой", "сделай", "поставь", "громкость", "громче", "тише",
    "пауза", "напомни", "сверни", "найди", "переключи", "следующ", "предыдущ", "скрин", "сохрани",
    "заблокируй", "перезагрузи", "игровой", "обычный", "напиши", "нажми", "убавь", "прибавь", "отправь",
)
DEFINE_RE = re.compile(r"^(?:запомни|создай|сделай|добавь|заведи) (?:команду|макрос)\s+(?P<rest>.+)$")
DELETE_RE = re.compile(r"^(?:удали|забудь|сотри) (?:команду|макрос)\s+(?P<name>.+)$")
LIST_RE = re.compile(r"^(?:какие|покажи|перечисли) (?:у меня )?(?:есть )?(?:команды|макросы)$")


def _norm(text: str) -> str:
    text = text.lower().replace("ё", "е")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s,:;—–-]", " ", text)).strip(" ,.")


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text.lower().replace("ё", "е"))).strip()


def _is_step_start(word: str) -> bool:
    return any(word.startswith(s) for s in STEP_STARTS)


def parse_define(text: str) -> tuple[str, list[str]] | None:
    """«запомни команду погнали: открой дискорд, открой стим» -> ("погнали", ["открой дискорд", "открой стим"])."""
    m = DEFINE_RE.match(_norm(text))
    if not m:
        return None
    rest = m.group("rest")
    name, steps = "", []
    parts = re.split(r"\s*(?::|—|–|\s-\s)\s*", rest, maxsplit=1)
    chunks = [_plain(c) for c in re.split(r"\s*[,;]\s*", rest) if _plain(c)]
    if len(parts) == 2 and _plain(parts[1]):
        # «имя: шаг, шаг» (так пишет Whisper)
        name = _plain(parts[0])
        steps = [_plain(c) for c in re.split(r"\s*[,;]\s*|\s+и\s+(?=\w)", parts[1]) if _plain(c)]
    elif len(chunks) > 1 and not _is_step_start(chunks[0].split()[0]):
        # «имя, шаг, шаг»
        name, steps = chunks[0], chunks[1:]
    else:
        # Без знаков (Vosk): имя — слова до первого глагола-действия, дальше режем по глаголам.
        words = [w for w in _plain(rest).split() if w not in ("это", "значит")]
        first = next((i for i, w in enumerate(words) if i > 0 and _is_step_start(w)), None)
        if first is None:
            return None
        name = " ".join(words[:first])
        cur: list[str] = []
        for w in words[first:]:
            # «сделай тише», «поставь громкость» — второй глагол продолжает шаг, а не начинает новый.
            if _is_step_start(w) and cur and not _is_step_start(cur[-1]):
                steps.append(" ".join(cur))
                cur = []
            cur.append(w)
        if cur:
            steps.append(" ".join(cur))
        steps = [re.sub(r"\s+и$", "", st) for st in steps]
    name = re.sub(r"^(?:когда я говорю|когда говорю|по слову)\s+", "", name).strip()
    if not name or not steps:
        return None
    return name, steps


class Macros:
    def __init__(self):
        self._lock = threading.Lock()
        try:
            self.items: dict[str, list[str]] = json.loads(MACROS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.items = {}

    def _save(self) -> None:
        MACROS_FILE.parent.mkdir(parents=True, exist_ok=True)
        MACROS_FILE.write_text(json.dumps(self.items, ensure_ascii=False, indent=2), encoding="utf-8")

    def find(self, text: str) -> tuple[str, list[str]] | None:
        t = _plain(text)
        for name, steps in self.items.items():
            if t == name or t in (f"давай {name}", f"режим {name}", f"{name} режим"):
                return name, steps
        return None

    def handle_management(self, text: str) -> str | None:
        """Создать/удалить/перечислить. Возвращает, что сказать, или None — это не про макросы."""
        defined = parse_define(text)
        if defined:
            name, steps = defined
            with self._lock:
                self.items[name] = steps
                self._save()
            return f"Запомнил команду «{name}»: " + ", ".join(steps) + "."
        m = DELETE_RE.match(_plain(text))
        if m:
            name = m.group("name").strip()
            with self._lock:
                existed = self.items.pop(name, None)
                self._save()
            return f"Удалил команду «{name}»." if existed else f"Команды «{name}» и не было."
        if LIST_RE.match(_plain(text)):
            if not self.items:
                return "Своих команд пока нет. Скажи, например: запомни команду погнали, открой дискорд, открой стим."
            return "Твои команды: " + "; ".join(f"{n} — {', '.join(s)}" for n, s in self.items.items()) + "."
        return None
