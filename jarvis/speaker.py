"""Озвучка ответов.

Движки:
  edge  — нейросетевые голоса Microsoft (нужен интернет). Можно опустить тон — звучит солиднее.
  piper — оффлайн-голоса Piper (tools/piper, ставит scripts/setup_piper.py), плюс «эффект Джарвиса»:
          ниже тон, металлический отзвук и лёгкое эхо, как у ИИ из фильма.
  sapi  — системный голос Windows (оффлайн, запасной вариант).

Длинный ответ режется на предложения: первое уже звучит, пока готовится следующее.
Любую фразу можно оборвать из другого потока (stop()), когда пользователь перебивает.
"""

from __future__ import annotations

import array
import asyncio
import platform
import re
import subprocess
import tempfile
import threading
import time
import wave
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from .config import ROOT, Config

PIPER_DIR = ROOT / "tools" / "piper"
IS_WINDOWS = platform.system() == "Windows"


def clean_for_speech(text: str) -> str:
    """Убирает markdown и прочий мусор, который глупо зачитывать вслух."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"[*_#`>|]", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", "ссылка", text)
    return re.sub(r"\s+", " ", text).strip()


def split_sentences(text: str, max_len: int = 220) -> list[str]:
    """Режет текст на куски по предложениям, склеивая слишком короткие."""
    parts = [p.strip() for p in re.split(r"(?<=[.!?…])\s+", text) if p.strip()]
    out: list[str] = []
    for p in parts:
        if out and (len(out[-1]) < 40 or len(out[-1]) + len(p) < 90) and len(out[-1]) + len(p) < max_len:
            out[-1] = f"{out[-1]} {p}"
        else:
            out.append(p)
    return out or [text]


def find_piper(voice: str) -> tuple[Path, Path] | None:
    """(piper.exe, голос .onnx) из tools/piper."""
    exes = [e for e in list(PIPER_DIR.rglob("piper.exe")) + list(PIPER_DIR.rglob("piper")) if e.is_file()]
    voices_dir = PIPER_DIR / "voices"
    voices = list(voices_dir.glob(f"{voice}.onnx")) or list(voices_dir.glob("*.onnx"))
    return (exes[0], voices[0]) if exes and voices else None


def beep() -> None:
    """Короткий сигнал «слушаю»."""
    if IS_WINDOWS:
        import winsound

        winsound.Beep(988, 70)
        winsound.Beep(1319, 90)
    else:
        print("\a", end="", flush=True)


class Speaker:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.engine = cfg.tts_engine
        self._dir = Path(tempfile.gettempdir())
        self._stop = threading.Event()  # выставляется, когда пользователь перебил
        self._pool = ThreadPoolExecutor(max_workers=1)  # готовит следующее предложение, пока играет текущее
        self._n = 0
        self._fails = 0  # сбои подряд: после трёх переходим на системный голос насовсем
        self._piper = find_piper(cfg.piper_voice) if self.engine == "piper" else None
        if self.engine == "piper" and not self._piper:
            print("[tts] Piper не найден (python scripts\\setup_piper.py) — говорю голосом edge")
            self.engine = "edge"

    def stop(self) -> None:
        """Оборвать текущую фразу (можно звать из другого потока)."""
        self._stop.set()

    # --- главное -------------------------------------------------------------

    def say(self, text: str) -> None:
        text = clean_for_speech(text)
        self._stop.clear()
        if not text:
            return
        if self.engine == "sapi":
            self._say_sapi(text)
            return
        sentences = split_sentences(text)
        pending: Future = self._pool.submit(self._synth, sentences[0])
        for i in range(len(sentences)):
            try:
                path = pending.result()
            except Exception as e:  # нет интернета, сломался Piper и т.п. — дальше системным голосом
                self._fails += 1
                print(f"[tts] {self.engine} не сработал ({e}), договариваю системным голосом")
                if self._fails >= 3:
                    print("[tts] три сбоя подряд — дальше только системный голос")
                    self.engine = "sapi"
                self._say_sapi(" ".join(sentences[i:]))
                return
            self._fails = 0
            if i + 1 < len(sentences):
                pending = self._pool.submit(self._synth, sentences[i + 1])
            if self._stop.is_set():
                return
            self._play(path)
            if self._stop.is_set():
                return

    def _synth(self, text: str) -> Path:
        self._n += 1
        if self.engine == "piper":
            return self._synth_piper(text, self._dir / f"jarvis_tts_{self._n % 3}.wav")
        return self._synth_edge(text, self._dir / f"jarvis_tts_{self._n % 3}.mp3")

    def _play(self, path: Path) -> None:
        if path.suffix == ".wav":
            _play_wav(path, self._stop)
        else:
            _play_mp3(path, self._stop)

    # --- движки --------------------------------------------------------------

    def _synth_edge(self, text: str, out: Path) -> Path:
        import edge_tts

        rate = f"{int(self.cfg.tts_rate):+d}%"
        pitch = f"{int(self.cfg.voice_pitch):+d}Hz"
        asyncio.run(edge_tts.Communicate(text, self.cfg.edge_voice, rate=rate, pitch=pitch).save(str(out)))
        return out

    def _synth_piper(self, text: str, out: Path) -> Path:
        exe, voice = self._piper
        effect = self.cfg.voice_effect
        # Тон понижаем, проигрывая чуть медленнее; чтобы речь не стала тягучей, Piper говорит быстрее.
        shift = 0.93 if effect == "jarvis" else 1.0
        speed = 1.0 - int(self.cfg.tts_rate) / 100
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        r = subprocess.run(
            [str(exe), "--model", str(voice), "--output_file", str(out),
             "--length_scale", f"{max(0.5, speed * shift):.3f}", "--sentence_silence", "0.15"],
            input=text.encode("utf-8"), capture_output=True, timeout=60, creationflags=flags,
        )
        if r.returncode != 0 or not out.exists():
            raise RuntimeError(r.stderr.decode("utf-8", "replace")[-300:] or f"код {r.returncode}")
        if effect == "jarvis":
            jarvis_effect(out, pitch_factor=shift)
        return out

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
            engine.setProperty("rate", 180 + int(self.cfg.tts_rate))
        # Каждое слово проверяем, не перебили ли нас.
        engine.connect("started-word", lambda *a, **k: self._stop.is_set() and engine.stop())
        engine.say(text)
        engine.runAndWait()
        engine.stop()


def jarvis_effect(path: Path, pitch_factor: float = 0.93) -> None:
    """«Голос ИИ»: металлический отзвук (гребенчатый фильтр), лёгкое эхо и тон ниже.

    Без numpy — обычный Python, на фразу в пару секунд уходит десятки миллисекунд."""
    with wave.open(str(path), "rb") as w:
        rate, width, channels = w.getframerate(), w.getsampwidth(), w.getnchannels()
        frames = w.readframes(w.getnframes())
    if width != 2 or channels != 1:
        return
    x = array.array("h", frames)
    n = len(x)
    comb = int(rate * 0.0065)   # ~6.5 мс — «металл»
    echo = int(rate * 0.085)    # ~85 мс — «зал»
    y = [0.0] * n
    for i in range(n):
        v = float(x[i])
        if i >= comb:
            v += 0.32 * y[i - comb]
        if i >= echo:
            v += 0.16 * x[i - echo]
        y[i] = v
    peak = max(1.0, max(abs(v) for v in y)) if y else 1.0
    k = min(1.0, 30000.0 / peak)
    out = array.array("h", (int(v * k) for v in y))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(rate * pitch_factor))  # играем чуть медленнее — голос ниже
        w.writeframes(out.tobytes())


def _play_wav(path: Path, stop: threading.Event) -> None:
    with wave.open(str(path), "rb") as w:
        duration = w.getnframes() / float(w.getframerate())
    if IS_WINDOWS:
        import winsound

        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        end = time.time() + duration + 0.05
        while time.time() < end and not stop.is_set():
            time.sleep(0.03)
        if stop.is_set():
            winsound.PlaySound(None, 0)
        return
    _play_pygame(path, stop)


def _play_mp3(path: Path, stop: threading.Event) -> None:
    """Проигрывает mp3 до конца или пока не выставят stop."""
    if IS_WINDOWS:
        # Встроенный в Windows MCI — не нужен pygame, который не всегда ставится на свежий Python.
        import ctypes

        mci = ctypes.windll.winmm.mciSendStringW
        buf = ctypes.create_unicode_buffer(256)
        mci("close jarvis_tts", None, 0, None)
        err = mci(f'open "{path}" type mpegvideo alias jarvis_tts', None, 0, None)
        if err:
            ctypes.windll.winmm.mciGetErrorStringW(err, buf, 256)
            raise RuntimeError(f"MCI: {buf.value}")
        try:
            mci("play jarvis_tts", None, 0, None)
            while not stop.is_set():
                mci("status jarvis_tts mode", buf, 256, None)
                if buf.value in ("stopped", ""):  # доиграло (или файл закрылся)
                    break
                time.sleep(0.03)
        finally:
            mci("stop jarvis_tts", None, 0, None)
            mci("close jarvis_tts", None, 0, None)
        return
    _play_pygame(path, stop)


def _play_pygame(path: Path, stop: threading.Event) -> None:
    import pygame

    if not pygame.mixer.get_init():
        pygame.mixer.init()
    pygame.mixer.music.load(str(path))
    pygame.mixer.music.play()
    while pygame.mixer.music.get_busy() and not stop.is_set():
        time.sleep(0.03)
    pygame.mixer.music.stop()
    pygame.mixer.music.unload()
