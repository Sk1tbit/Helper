"""Загрузка настроек из config.yaml поверх значений по умолчанию."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    # Локальная нейросеть
    llm_backend: str = "ollama"            # ollama | openai (LM Studio, llama.cpp и т.п.)
    llm_url: str = "http://localhost:11434"
    llm_api_key: str = ""                  # обычно не нужен
    model: str = "llama3.1:8b"
    vision_model: str = "llama3.2-vision"  # пусто = без зрения
    num_ctx: int = 8192
    temperature: float = 0.3
    keep_alive: str = "30m"
    llm_timeout: float = 180
    wake_words: list[str] = field(
        default_factory=lambda: ["джарвис", "джарвиз", "жарвис", "джервис", "джарвес", "ярвис"]
    )
    assistant_name: str = "Джарвис"
    user_name: str = "сэр"
    vosk_model: str = "models/vosk-model-small-ru-0.22"
    mic_device: int | str | None = None
    follow_up_seconds: float = 6
    tts_engine: str = "edge"
    edge_voice: str = "ru-RU-DmitryNeural"
    tts_rate: int = 0
    confirm_dangerous: bool = True
    confirm_power: bool = True
    idle_reset_minutes: float = 10
    learning: bool = True
    quick_commands: bool = True
    disable_thinking: bool = True
    game_name: str = ""
    game_model: str = ""
    game_vision_model: str = ""
    fishing_tg_channels: list[str] = field(default_factory=list)
    open_tg_in_app: bool = False
    tg_open_mode: str = "web"
    swearing: bool = False
    discord_bot_token: str = ""
    discord_channels: list = field(default_factory=list)
    discord_open_mode: str = "web"
    # Режим «слушай»: без имени, каждая фраза — команда
    listen_on_phrases: list = field(default_factory=lambda: ["слушай", "слушай меня", "слушай постоянно", "будь на связи"])
    listen_off_phrases: list = field(default_factory=lambda: ["стоп", "хватит", "хватит слушать", "не слушай", "перестань слушать", "отбой"])
    always_listen_minutes: float = 0     # через сколько минут тишины выйти из режима (0 — никогда)
    start_listening: bool = False        # сразу после запуска слушать без имени
    # Перебить помощника, пока он говорит или думает
    interrupt_words: list = field(default_factory=lambda: ["стоп", "хватит", "заткнись", "замолчи", "отмена", "джарвис"])
    interrupt_confidence: float = 0.9
    stt_engine: str = "whisper"          # whisper — точнее (если установлен), vosk — только Vosk
    whisper_model: str = "small"         # tiny / base / small / medium / large-v3-turbo
    whisper_device: str = "cpu"          # cpu — процессор (не мешает игре), cuda — видеокарта
    whisper_compute: str = "int8"        # для cuda лучше int8_float16

    @property
    def vosk_model_path(self) -> Path:
        path = Path(self.vosk_model)
        return path if path.is_absolute() else ROOT / path


def load_config(path: str | os.PathLike | None = None) -> Config:
    cfg = Config()
    path = Path(path) if path else ROOT / "config.yaml"
    if path.exists():
        data = parse_simple_yaml(path.read_text(encoding="utf-8-sig"))
        known = {f.name for f in fields(Config)}
        for key, value in data.items():
            if key in known and value is not None:
                setattr(cfg, key, value)
    cfg.wake_words = [w.lower() for w in cfg.wake_words]
    cfg.vision_model = cfg.vision_model or ""
    return cfg


def parse_simple_yaml(text: str) -> dict:
    """Читает плоский YAML вида `ключ: значение` — ровно то, что лежит в config.yaml.

    Свой парсер вместо PyYAML: у того бывают не собраны пакеты под свежий Python.
    Понимает комментарии, строки в кавычках, числа, true/false, null и списки [a, b].
    """
    data: dict = {}
    for raw in text.splitlines():
        line = _strip_comment(raw).strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = _parse_value(value.strip())
    return data


def _strip_comment(line: str) -> str:
    quote = None
    for i, ch in enumerate(line):
        if ch in "\"'" and quote in (None, ch):
            quote = None if quote else ch
        elif ch == "#" and quote is None and (i == 0 or line[i - 1].isspace()):
            return line[:i]
    return line


def _parse_value(value: str):
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        return [_parse_value(v.strip()) for v in inner.split(",") if v.strip()] if inner else []
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    low = value.lower()
    if low in ("", "null", "~", "none"):
        return None
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    for cast in (int, float):
        try:
            return cast(value)
        except ValueError:
            pass
    return value
