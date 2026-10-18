"""Конспект встречи, звонка или видео: слушаем речь, потом нейросеть выжимает итог, решения и задачи.

«Джарвис, веди конспект» — дальше всё услышанное записывается (без имени). «Джарвис, закончи конспект» —
модель делает выжимку, задачи падают в органайзер, полный текст — в memory/summaries/*.md.
Чтобы конспектировать звук самого компьютера (видео, созвон), выбери микрофоном «Стерео микшер»
(mic_device) или включи звук на колонки — микрофон поймает.
Ещё можно «сделай конспект из буфера» — выжимка скопированного текста (статья, субтитры, расшифровка).
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path

from . import learning

START_RE = re.compile(r"^(?:начни|веди|включи|запусти|делай|начинай)(?: вести)? (?:конспект|протокол|запись встречи)\w*"
                      r"|^(?:законспектируй|конспектируй|запиши) (?:встречу|созвон|звонок|разговор|видео|лекцию)$")
STOP_RE = re.compile(r"^(?:закончи|заверши|останови|стоп|хватит|выключи|сделай|подведи)(?: вести)? "
                     r"(?:конспект|протокол|запись встречи|итоги?)\w*$")
CLIPBOARD_RE = re.compile(r"(?:конспект|выжимк|перескажи|суммаризируй|кратко перескажи)\w*.*буфер")

CHUNK = 6000  # символов на кусок — влезает в контекст маленьких моделей

MAP_PROMPT = ("Это кусок расшифровки разговора, видео или текста (распознавание речи с ошибками). "
              "Коротко по-русски перечисли главное: о чём говорили, что решили, кто что должен сделать. "
              "Только факты из текста.")
REDUCE_PROMPT = """Сделай конспект по-русски строго в таком виде:
Итог: одно-два предложения, о чём это было.
Решения:
- ...
Задачи:
- ...
Если решений или задач нет — напиши «- нет». Без вступлений и markdown-заголовков."""


def _dir() -> Path:
    return learning.MEMORY_DIR / "summaries"


def chunks(text: str, size: int = CHUNK) -> list[str]:
    out, cur = [], ""
    for part in re.split(r"(?<=[.!?\n])\s+", text):
        if len(cur) + len(part) > size and cur:
            out.append(cur.strip())
            cur = ""
        while len(part) > size:  # одно огромное «предложение» без точек
            out.append(part[:size])
            part = part[size:]
        cur += part + " "
    if cur.strip():
        out.append(cur.strip())
    return out


def summarize(chat, text: str) -> str:
    """chat(messages) -> {"content"}. Длинный текст — по кускам, потом общая выжимка."""
    parts = chunks(text)
    if len(parts) > 1:
        notes = [chat([{"role": "system", "content": MAP_PROMPT}, {"role": "user", "content": p}])["content"]
                 for p in parts]
        text = "\n\n".join(notes)
    return chat([{"role": "system", "content": REDUCE_PROMPT}, {"role": "user", "content": text}])["content"].strip()


def parse_summary(summary: str) -> dict:
    """{"итог": str, "решения": [...], "задачи": [...]} из ответа модели."""
    out: dict = {"итог": "", "решения": [], "задачи": []}
    section = None
    for raw in summary.splitlines():
        line = re.sub(r"[*#_`]+", "", raw).strip()
        m = re.match(r"^(итог|решения|задачи)\s*:\s*(.*)$", line, re.I)
        if m:
            section = m.group(1).lower()
            if section == "итог":
                out["итог"] = m.group(2).strip()
            elif m.group(2).strip():
                out[section].append(m.group(2).strip())
            continue
        item = re.sub(r"^[-•*\d.)]+\s*", "", line).strip()
        if not item or section is None:
            continue
        if section == "итог":
            out["итог"] = (out["итог"] + " " + item).strip()
        elif not re.fullmatch(r"нет\.?|не было\.?|-", item, re.I):
            out[section].append(item)
    return out


def save(transcript: str, summary: str, title: str = "Конспект") -> Path:
    folder = _dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{datetime.now():%Y-%m-%d_%H%M}.md"
    path.write_text(f"# {title} {datetime.now():%d.%m.%Y %H:%M}\n\n{summary}\n\n## Расшифровка\n\n{transcript}\n",
                    encoding="utf-8")
    return path


class Recorder:
    """Копит фразы, пока идёт конспект."""

    def __init__(self):
        self.lines: list[str] = []
        self.started = 0.0
        self.active = False

    def start(self) -> None:
        self.lines, self.started, self.active = [], time.time(), True

    def add(self, text: str) -> None:
        if self.active and text.strip():
            self.lines.append(f"[{datetime.now():%H:%M}] {text.strip()}")

    def stop(self) -> str:
        self.active = False
        return "\n".join(self.lines)
