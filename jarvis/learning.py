"""Всё, что помогает помощнику подстраиваться под пользователя.

- Память: папка memory/memories с заметками, которые Claude сам ведёт через memory tool
  (кто пользователь, что он любит, его словечки и команды, что сработало на этом ПК).
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
SNAPSHOT_LIMIT = 20_000  # символов памяти, которые подкладываем целиком

FUZZY_THRESHOLD = 0.75   # насколько слово должно быть похоже на имя
HITS_TO_LEARN = 2        # сколько раз услышать вариант, прежде чем запомнить навсегда


def memory_snapshot() -> str:
    """Содержимое всех файлов памяти одним текстом (или список файлов, если их много)."""
    if not MEMORIES.exists():
        return ""
    files = sorted(p for p in MEMORIES.rglob("*") if p.is_file())
    parts, total = [], 0
    for path in files:
        rel = "/memories/" + path.relative_to(MEMORIES).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        total += len(text)
        parts.append(f'<file path="{rel}">\n{text.strip()}\n</file>')
    if not parts:
        return ""
    if total > SNAPSHOT_LIMIT:
        listing = "\n".join("/memories/" + p.relative_to(MEMORIES).as_posix() for p in files)
        return f"Память большая, вот список файлов (читай нужные через memory view):\n{listing}"
    return "\n".join(parts)


def transcript(messages: list, assistant_name: str) -> str:
    """Текстовая запись разговора без картинок — для разбора после его окончания."""
    lines: list[str] = []
    for msg in messages:
        content = msg["content"]
        if msg["role"] == "user":
            if isinstance(content, str):
                lines.append(f"Пользователь: {content.split('</memory>')[-1].strip()}")
                continue
            for block in content:
                if block.get("type") != "tool_result":
                    continue
                if block.get("is_error"):
                    lines.append(f"  [ошибка: {str(block['content'])[:300]}]")
                elif isinstance(block["content"], str):
                    lines.append(f"  [результат: {block['content'][:300]}]")
                else:
                    lines.append("  [результат: скриншот]")
        else:
            for block in content:
                if block.type == "text" and block.text.strip():
                    lines.append(f"{assistant_name}: {block.text.strip()}")
                elif block.type == "tool_use":
                    args = json.dumps(block.input, ensure_ascii=False)
                    lines.append(f"  [вызов {block.name} {args[:300]}]")
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
