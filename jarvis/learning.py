"""Всё, что помогает помощнику подстраиваться под пользователя.

- Память: папка memory/memories с заметками, которые нейросеть сама ведёт инструментами
  remember/forget (кто пользователь, что он любит, его команды, что сработало на этом ПК).
- Снимок памяти подкладывается в начало каждого нового разговора.
- После разговора строится его текстовая запись для «разбора полётов» (см. Brain.reflect).
- Распознавалка часто слышит имя по-разному — новые варианты запоминаются сами.
"""

from __future__ import annotations

import difflib
import json
import threading
from pathlib import Path

from .config import ROOT

MEMORY_DIR = ROOT / "memory"
MEMORIES = MEMORY_DIR / "memories"
WAKE_FILE = MEMORY_DIR / "wake_words.json"
SNAPSHOT_LIMIT = 8_000   # символов памяти в начале разговора (у локальных моделей контекст маленький)

MEMORY_FILES = {
    "profile": "кто пользователь, как к нему обращаться, какой стиль ответов любит",
    "preferences": "любимые программы, сайты, музыка, привычки",
    "commands": "личные команды пользователя и что они значат",
    "pc": "что сработало на этом компьютере: пути к программам, команды, расположение кнопок",
}

MEMORY_TOOLS = [
    {
        "name": "remember",
        "description": "Записывает факт о пользователе в долговременную память. "
        "Файлы: " + "; ".join(f"{k} — {v}" for k, v in MEMORY_FILES.items()) + ".",
        "input_schema": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "enum": list(MEMORY_FILES)},
                "fact": {"type": "string", "description": "Один короткий факт"},
            },
            "required": ["file", "fact"],
        },
    },
    {
        "name": "forget",
        "description": "Удаляет из памяти строки, содержащие указанный текст (устаревшее или неверное).",
        "input_schema": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "enum": list(MEMORY_FILES)},
                "text": {"type": "string", "description": "Кусок текста удаляемой строки"},
            },
            "required": ["file", "text"],
        },
    },
]

_memory_lock = threading.Lock()


def _memory_path(file: str) -> Path:
    name = file.strip().lower().removesuffix(".md").split("/")[-1]
    if name not in MEMORY_FILES:
        raise ValueError(f"Нет такого файла памяти: {file}. Есть: {', '.join(MEMORY_FILES)}")
    return MEMORIES / f"{name}.md"


def remember(file: str, fact: str) -> str:
    fact = " ".join(fact.split()).lstrip("-• ").strip()
    if not fact:
        raise ValueError("Пустой факт")
    with _memory_lock:
        path = _memory_path(file)
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        if any(fact.lower() == line.lstrip("- ").strip().lower() for line in lines):
            return "Это уже есть в памяти"
        MEMORIES.mkdir(parents=True, exist_ok=True)
        lines.append(f"- {fact}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f"Записано в {path.stem}"


def forget(file: str, text: str) -> str:
    needle = text.strip().lower()
    if not needle:
        raise ValueError("Пустой текст")
    with _memory_lock:
        path = _memory_path(file)
        if not path.exists():
            return "В этом файле пока ничего нет"
        lines = path.read_text(encoding="utf-8").splitlines()
        kept = [line for line in lines if needle not in line.lower()]
        path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    removed = len(lines) - len(kept)
    return f"Удалено строк: {removed}" if removed else "Такого в памяти не нашлось"

FUZZY_THRESHOLD = 0.75   # насколько слово должно быть похоже на имя
HITS_TO_LEARN = 2        # сколько раз услышать вариант, прежде чем запомнить навсегда


def memory_snapshot() -> str:
    """Содержимое всех файлов памяти одним текстом (или список файлов, если их много)."""
    if not MEMORIES.exists():
        return ""
    parts = []
    with _memory_lock:
        for name in MEMORY_FILES:
            path = MEMORIES / f"{name}.md"
            try:
                text = path.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError):
                continue
            if text:
                parts.append(f"[{name}]\n{text}")
    snapshot = "\n\n".join(parts)
    if len(snapshot) > SNAPSHOT_LIMIT:
        # Новые факты дописываются в конец файлов, поэтому при обрезке теряем самые старые.
        snapshot = "...\n" + snapshot[-SNAPSHOT_LIMIT:]
    return snapshot


def transcript(messages: list[dict], assistant_name: str) -> str:
    """Текстовая запись разговора — для разбора после его окончания."""
    lines: list[str] = []
    for m in messages:
        if m["role"] == "user":
            lines.append(f"Пользователь: {m['content'].split('</memory>')[-1].strip()}")
        elif m["role"] == "assistant":
            if m.get("content"):
                lines.append(f"{assistant_name}: {m['content']}")
            for call in m.get("tool_calls") or []:
                args = json.dumps(call["arguments"], ensure_ascii=False)
                lines.append(f"  [вызов {call['name']} {args[:300]}]")
        elif m["role"] == "tool":
            lines.append(f"  [результат: {m['content'][:300]}]")
    return "\n".join(lines)


class WakeWords:
    """Ищет имя помощника в фразе и учит новые варианты того, как его слышит Vosk."""

    def __init__(self, base_words: list[str]):
        self.base = [w.lower() for w in base_words]
        self._lock = threading.Lock()
        data = self._load()
        self.learned: list[str] = data.get("learned", [])
        self.candidates: dict[str, int] = data.get("candidates", {})

    def _load(self) -> dict:
        try:
            return json.loads(WAKE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        data = {"learned": self.learned, "candidates": self.candidates}
        WAKE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def strip(self, text: str) -> str | None:
        """Текст после имени, или None, если имя не прозвучало."""
        words = text.lower().split()
        known = self.base + self.learned
        for i, word in enumerate(words):
            if any(word.startswith(w) for w in known):
                return " ".join(words[i + 1:]).strip(" ,.")
        # Похожее слово в начале фразы — скорее всего, это имя, просто плохо расслышанное.
        for i, word in enumerate(words[:2]):
            if len(word) >= 4 and self._similar(word):
                self._remember(word)
                return " ".join(words[i + 1:]).strip(" ,.")
        return None

    def _similar(self, word: str) -> bool:
        return any(difflib.SequenceMatcher(None, word, w).ratio() >= FUZZY_THRESHOLD for w in self.base)

    def _remember(self, word: str) -> None:
        with self._lock:
            self.candidates[word] = self.candidates.get(word, 0) + 1
            if self.candidates[word] >= HITS_TO_LEARN and word not in self.learned:
                self.learned.append(word)
                del self.candidates[word]
                print(f"[учусь] теперь «{word}» тоже считается моим именем")
            self._save()
