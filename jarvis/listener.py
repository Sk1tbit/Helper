"""Оффлайн-распознавание речи с микрофона через Vosk."""

from __future__ import annotations

import json
import queue
import threading
import time

from .config import Config

SAMPLE_RATE = 16000


class Listener:
    def __init__(self, cfg: Config):
        from vosk import KaldiRecognizer, Model, SetLogLevel

        SetLogLevel(-1)
        model_path = cfg.vosk_model_path
        if not model_path.exists():
            raise FileNotFoundError(
                f"Нет модели Vosk в {model_path}. Запусти: python scripts/download_model.py"
            )
        self.model = Model(str(model_path))
        self.rec = KaldiRecognizer(self.model, SAMPLE_RATE)
        self.device = cfg.mic_device
        self.audio: queue.Queue[bytes] = queue.Queue()
        self._muted = threading.Event()
        self._stream = None

    def start(self) -> None:
        import sounddevice as sd

        self._stream = sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            blocksize=4000,
            device=self.device,
            dtype="int16",
            channels=1,
            callback=self._on_audio,
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream:
            self._stream.stop()
            self._stream.close()

    def _on_audio(self, indata, frames, time_info, status) -> None:
        if not self._muted.is_set():
            self.audio.put(bytes(indata))

    def mute(self) -> None:
        """Глушим микрофон, пока помощник говорит, чтобы он не слушал сам себя."""
        self._muted.set()

    def unmute(self) -> None:
        while not self.audio.empty():
            try:
                self.audio.get_nowait()
            except queue.Empty:
                break
        self.rec.Reset()
        self._muted.clear()

    def next_utterance(self, timeout: float | None = None) -> str | None:
        """Ждёт законченную фразу. None — если за timeout секунд ничего не сказали."""
        deadline = time.monotonic() + timeout if timeout else None
        heard_something = False
        while True:
            if deadline and not heard_something and time.monotonic() > deadline:
                return None
            try:
                chunk = self.audio.get(timeout=0.25)
            except queue.Empty:
                continue
            if self.rec.AcceptWaveform(chunk):
                text = json.loads(self.rec.Result()).get("text", "").strip()
                if text:
                    return text
                heard_something = False
            elif json.loads(self.rec.PartialResult()).get("partial"):
                # Человек начал говорить — даём договорить, даже если таймаут вышел.
                heard_something = True


def list_microphones() -> None:
    import sounddevice as sd

    for i, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0:
            print(f"{i}: {dev['name']}")
