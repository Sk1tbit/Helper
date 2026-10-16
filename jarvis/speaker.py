"""Озвучка ответов: нейросетевой голос edge-tts или системный SAPI (pyttsx3)."""

from __future__ import annotations

import asyncio
import re
import tempfile
import time
from pathlib import Path

from .config import Config


def clean_for_speech(text: str) -> str:
    """Убирает markdown и прочий мусор, который глупо зачитывать вслух."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"[*_#`>|]", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", "ссылка", text)
    return re.sub(r"\s+", " ", text).strip()


class Speaker:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.engine = cfg.tts_engine
        self._tmp = Path(tempfile.gettempdir()) / "jarvis_tts.mp3"

    def say(self, text: str) -> None:
        text = clean_for_speech(text)
        if not text:
            return
        if self.engine == "edge":
            try:
                self._say_edge(text)
                return
            except Exception as e:  # нет интернета, нет pygame и т.п.
                print(f"[tts] edge-tts не сработал ({e}), переключаюсь на системный голос")
                self.engine = "sapi"
        self._say_sapi(text)

    def _say_edge(self, text: str) -> None:
        import edge_tts
        import pygame

        rate = f"{int(self.cfg.tts_rate):+d}%"
        communicate = edge_tts.Communicate(text, self.cfg.edge_voice, rate=rate)
        asyncio.run(communicate.save(str(self._tmp)))

        if not pygame.mixer.get_init():
            pygame.mixer.init()
        pygame.mixer.music.load(str(self._tmp))
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            time.sleep(0.05)
        pygame.mixer.music.unload()

    def _say_sapi(self, text: str) -> None:
        import pyttsx3

        # Движок пересоздаём каждый раз: повторный runAndWait() на Windows часто зависает.
        engine = pyttsx3.init()
        for voice in engine.getProperty("voices"):
            meta = f"{voice.id} {voice.name} {getattr(voice, 'languages', '')}".lower()
            if "ru" in meta or "russian" in meta or "irina" in meta or "pavel" in meta:
                engine.setProperty("voice", voice.id)
                break
        if self.cfg.tts_rate:
            engine.setProperty("rate", int(self.cfg.tts_rate))
        engine.say(text)
        engine.runAndWait()
        engine.stop()
