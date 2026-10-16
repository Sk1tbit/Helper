"""Точное распознавание команд через Whisper (faster-whisper).

Vosk лёгкий и постоянно слушает, чтобы поймать «Джарвис». Но маленькая модель часто
коверкает слова. Поэтому уже записанную фразу с командой переслушиваем Whisper'ом —
он распознаёт русский заметно лучше. Если faster-whisper не установлен или не завёлся,
остаёмся на тексте от Vosk.
"""

from __future__ import annotations

import threading

from .config import Config

# Подсказка для Whisper: слова, которые он должен знать (названия, имена, водоёмы РР4).
PROMPT = ("{name}, открой Телеграм. Яндекс Музыка, Яндекс Браузер, Дискорд, Стим, ютуб. Громче, тише, пауза. "
          "Напомни через пятнадцать минут. Русская Рыбалка 4, Вьюнок, Старый Острог, Ахтуба, Медное, перловица.")

# Whisper иногда «слышит» в шуме фразы из субтитров — такое выкидываем.
HALLUCINATIONS = ("субтитр", "продолжение следует", "спасибо за просмотр", "редактор субтитров",
                  "подписывайтесь", "dimatorzok", "amara.org")


def _add_cuda_dll_dirs() -> None:
    """Windows: подхватить cuBLAS/cuDNN, поставленные через pip (nvidia-cublas-cu12, nvidia-cudnn-cu12)."""
    import os
    import site
    from pathlib import Path

    if os.name != "nt" or not hasattr(os, "add_dll_directory"):
        return
    for base in site.getsitepackages():
        for bin_dir in Path(base, "nvidia").glob("*/bin"):
            try:
                os.add_dll_directory(str(bin_dir))
                os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
            except OSError:
                pass


class WhisperSTT:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.model = None
        self.error: str | None = None
        self.prompt = PROMPT.format(name=cfg.assistant_name)
        self._ready = threading.Event()
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self) -> None:
        _add_cuda_dll_dirs()
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            self.error = "faster-whisper не установлен (pip install faster-whisper)"
            self._ready.set()
            return
        attempts = [(self.cfg.whisper_device, self.cfg.whisper_compute)]
        if self.cfg.whisper_device != "cpu":
            attempts.append(("cpu", "int8"))  # нет CUDA-библиотек — работаем на процессоре
        for device, compute in attempts:
            try:
                self.model = WhisperModel(self.cfg.whisper_model, device=device, compute_type=compute)
                print(f"[stt] Whisper «{self.cfg.whisper_model}» готов ({device}, {compute})")
                break
            except Exception as e:  # noqa: BLE001 — любая ошибка загрузки = пробуем следующий вариант
                self.error = f"{device}: {e}"
                print(f"[stt] Whisper на {device} не завёлся: {e}")
        self._ready.set()

    @property
    def ready(self) -> bool:
        return self.model is not None

    def transcribe(self, audio: bytes, wait: float = 0) -> str | None:
        """Распознаёт фразу (16 кГц, 16 бит, моно). None — если Whisper недоступен или ничего не понял."""
        if wait:
            self._ready.wait(wait)
        if self.model is None or len(audio) < 16000 * 2 * 0.3:  # короче 0.3 с — нечего слушать
            return None
        import numpy as np

        samples = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
        try:
            segments, _ = self.model.transcribe(
                samples, language="ru", beam_size=5, initial_prompt=self.prompt,
                condition_on_previous_text=False, vad_filter=False,
            )
            text = " ".join(s.text.strip() for s in segments).strip()
        except Exception as e:  # noqa: BLE001
            print(f"[stt] ошибка Whisper: {e}")
            return None
        low = text.lower()
        if not text or any(h in low for h in HALLUCINATIONS):
            return None
        return text
