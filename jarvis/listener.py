"""Оффлайн-распознавание речи с микрофона через Vosk."""

from __future__ import annotations

import json
import queue
import threading
import time
from contextlib import contextmanager
from typing import Callable

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
        # Куда идёт звук: listen — обычное распознавание, barge — только стоп-слова (пока
        # помощник говорит или думает, чтобы его можно было перебить), mute — никуда.
        self._mode = "listen"
        self._modes: list[str] = []
        self._modes_lock = threading.Lock()
        self._barge_audio: queue.Queue[bytes] = queue.Queue()
        self._barge_reset = threading.Event()
        self.interrupt_words = [w.lower() for w in cfg.interrupt_words]
        self.interrupt_confidence = cfg.interrupt_confidence
        self.on_interrupt: Callable[[str], None] | None = None
        self._utterance = bytearray()  # звук текущей фразы — для переслушивания Whisper'ом
        self.last_audio = b""          # звук последней распознанной фразы
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
        if self.interrupt_words:
            threading.Thread(target=self._barge_loop, daemon=True).start()

    def stop(self) -> None:
        if self._stream:
            self._stream.stop()
            self._stream.close()

    def _on_audio(self, indata, frames, time_info, status) -> None:
        mode = self._mode
        if mode == "listen":
            self.audio.put(bytes(indata))
        elif mode == "barge":
            self._barge_audio.put(bytes(indata))

    def _set_mode(self, mode: str) -> None:
        if mode == "barge" and not self.interrupt_words:
            mode = "mute"  # перебивать выключено — просто не слушаем себя
        if mode == "barge" and self._mode != "barge":
            self._barge_reset.set()
        if mode == "listen" and self._mode != "listen":
            # Всё, что было сказано, пока мы не слушали (в т.ч. наш собственный голос), выкидываем.
            _drain(self.audio)
            self.rec.Reset()
            self._utterance.clear()
        self._mode = mode

    @contextmanager
    def mode(self, mode: str):
        """with listener.mode("barge"): ... — временно переключить, потом вернуть как было."""
        with self._modes_lock:
            self._modes.append(self._mode)
            self._set_mode(mode)
        try:
            yield
        finally:
            with self._modes_lock:
                self._set_mode(self._modes.pop())

    def mute(self) -> None:
        self._set_mode("mute")

    def unmute(self) -> None:
        self._set_mode("listen")

    def _barge_loop(self) -> None:
        """Ловит «стоп/хватит» во время речи и раздумий помощника."""
        from vosk import KaldiRecognizer

        grammar = json.dumps(self.interrupt_words + ["[unk]"], ensure_ascii=False)
        rec = KaldiRecognizer(self.model, SAMPLE_RATE, grammar)
        rec.SetWords(True)
        while True:
            chunk = self._barge_audio.get()
            if self._barge_reset.is_set():
                self._barge_reset.clear()
                rec.Reset()
                _drain(self._barge_audio)
                continue
            if not rec.AcceptWaveform(chunk):
                continue
            for word in json.loads(rec.Result()).get("result", []):
                # Строгий порог: своё же эхо из колонок не должно нас останавливать.
                if word.get("word") in self.interrupt_words and word.get("conf", 0) >= self.interrupt_confidence:
                    print(f"[stt] перебили: «{word['word']}» ({word.get('conf', 0):.2f})")
                    if self.on_interrupt:
                        self.on_interrupt(word["word"])
                    break

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
            self._utterance += chunk
            if len(self._utterance) > SAMPLE_RATE * 2 * 30:  # держим не больше 30 секунд
                del self._utterance[: len(self._utterance) - SAMPLE_RATE * 2 * 30]
            if self.rec.AcceptWaveform(chunk):
                text = json.loads(self.rec.Result()).get("text", "").strip()
                audio = bytes(self._utterance)
                self._utterance.clear()
                if text:
                    self.last_audio = audio
                    return text
                heard_something = False
            elif json.loads(self.rec.PartialResult()).get("partial"):
                # Человек начал говорить — даём договорить, даже если таймаут вышел.
                heard_something = True


def _drain(q: queue.Queue) -> None:
    while True:
        try:
            q.get_nowait()
        except queue.Empty:
            return


def list_microphones() -> None:
    import sounddevice as sd

    for i, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0:
            print(f"{i}: {dev['name']}")
