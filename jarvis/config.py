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
    idle_reset_minutes: float = 10
    learning: bool = True

    @property
    def vosk_model_path(self) -> Path:
        path = Path(self.vosk_model)
        return path if path.is_absolute() else ROOT / path


def load_config(path: str | os.PathLike | None = None) -> Config:
    cfg = Config()
    path = Path(path) if path else ROOT / "config.yaml"
    if path.exists():
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        known = {f.name for f in fields(Config)}
        for key, value in data.items():
            if key in known and value is not None:
                setattr(cfg, key, value)
    cfg.wake_words = [w.lower() for w in cfg.wake_words]
    cfg.vision_model = cfg.vision_model or ""
    return cfg
