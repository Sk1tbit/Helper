"""Точное распознавание команд через Whisper.

Vosk лёгкий и постоянно слушает, чтобы поймать «Джарвис». Но маленькая модель часто
коверкает слова. Поэтому уже записанную фразу с командой переслушиваем Whisper'ом —
он распознаёт русский заметно лучше.

Два способа запустить Whisper:
  1. whisper.cpp (tools/whisper, ставит scripts/setup_whisper.py) — готовая программа,
     работает на любой версии Python, в том числе на самой свежей. Основной вариант.
  2. faster-whisper (pip) — если он установился (обычно на Python 3.10–3.13).
Если ни один не доступен — остаёмся на тексте от Vosk.
"""

from __future__ import annotations

import atexit
import io
import os
import subprocess
import threading
import time
import wave
from pathlib import Path

import requests

from .config import ROOT, Config

WHISPER_DIR = ROOT / "tools" / "whisper"
SAMPLE_RATE = 16000

# Подсказка для Whisper: слова, которые он должен знать (названия, имена, водоёмы РР4).
PROMPT = ("{name}, открой Телеграм. Яндекс Музыка, Яндекс Браузер, Дискорд, Стим, ютуб. Громче, тише, пауза. "
          "Напомни через пятнадцать минут. Какая погода. Русская Рыбалка 4, Вьюнок, Старый Острог, Ахтуба, "
          "Медное, перловица.")

# Whisper иногда «слышит» в шуме фразы из субтитров — такое выкидываем.
HALLUCINATIONS = ("субтитр", "продолжение следует", "спасибо за просмотр", "редактор субтитров",
                  "подписывайтесь", "dimatorzok", "amara.org", "[музыка]", "(музыка)")


def find_whisper_cpp() -> tuple[Path, Path] | None:
    """(whisper-server.exe, модель .bin), если whisper.cpp поставлен скриптом setup_whisper.py."""
    if not WHISPER_DIR.exists():
        return None
    names = ("whisper-server.exe", "whisper-server", "server.exe")
    servers = [p for n in names for p in WHISPER_DIR.rglob(n)]
    models = sorted(WHISPER_DIR.glob("ggml-*.bin"), key=lambda p: p.stat().st_size)
    if not servers or not models:
        return None
    return servers[0], models[-1]


def _wav(audio: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(audio)
    return buf.getvalue()


def _add_cuda_dll_dirs() -> None:
    """Windows: подхватить cuBLAS/cuDNN, поставленные через pip (nvidia-cublas-cu12, nvidia-cudnn-cu12)."""
    import site

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
        self.backend: str | None = None   # "cpp" или "faster"
        self.model = None                 # faster-whisper
        self.server: subprocess.Popen | None = None
        self.url = f"http://127.0.0.1:{cfg.whisper_port}"
        self.http = requests.Session()
        self.http.trust_env = False  # локальный сервер — мимо системного прокси/VPN
        self.error: str | None = None
        self.prompt = PROMPT.format(name=cfg.assistant_name)
        self._ready = threading.Event()
        threading.Thread(target=self._load, daemon=True).start()

    # --- запуск --------------------------------------------------------------

    def _load(self) -> None:
        try:
            if self.cfg.whisper_backend in ("auto", "cpp"):
                if self._start_cpp():
                    return
                if self.error:
                    print(f"[stt] {self.error}")
            if self.cfg.whisper_backend in ("auto", "faster") and self._load_faster():
                return
            if not self.error:
                self.error = "Whisper не установлен: запусти python scripts\\setup_whisper.py"
            print(f"[stt] {self.error}. Работаю на Vosk.")
        finally:
            self._ready.set()

    def _start_cpp(self) -> bool:
        found = find_whisper_cpp()
        if not found:
            return False
        exe, model = found
        threads = str(self.cfg.whisper_threads or max(2, (os.cpu_count() or 4) - 2))
        base = [str(exe), "-m", str(model), "--host", "127.0.0.1", "--port", str(self.cfg.whisper_port), "-t", threads]
        # Язык передаём и при запуске, и в каждом запросе; если сборка не знает флаг -l — запускаем без него.
        for cmd in (base + ["-l", "ru"], base):
            if self._run_server(cmd):
                print(f"[stt] Whisper (whisper.cpp, {model.name}) готов")
                return True
        return False

    def _run_server(self, cmd: list[str]) -> bool:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self.server = subprocess.Popen(cmd, cwd=str(Path(cmd[0]).parent), stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL, creationflags=flags)
        except OSError as e:
            self.error = f"whisper.cpp не запустился: {e}"
            return False
        atexit.register(self.close)
        deadline = time.time() + 90  # модель грузится несколько секунд
        while time.time() < deadline:
            if self.server.poll() is not None:
                self.error = f"whisper.cpp завершился с кодом {self.server.returncode}"
                return False
            try:
                if self.http.get(self.url, timeout=1).status_code < 500:  # 503 — модель ещё грузится
                    self.backend = "cpp"
                    return True
            except requests.RequestException:
                pass
            time.sleep(0.5)
        self.error = "whisper.cpp не ответил за полторы минуты"
        self.close()
        return False

    def _load_faster(self) -> bool:
        _add_cuda_dll_dirs()
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            return False
        attempts = [(self.cfg.whisper_device, self.cfg.whisper_compute)]
        if self.cfg.whisper_device != "cpu":
            attempts.append(("cpu", "int8"))  # нет CUDA-библиотек — работаем на процессоре
        for device, compute in attempts:
            try:
                self.model = WhisperModel(self.cfg.whisper_model, device=device, compute_type=compute)
                self.backend = "faster"
                print(f"[stt] Whisper (faster-whisper «{self.cfg.whisper_model}», {device}) готов")
                return True
            except Exception as e:  # noqa: BLE001 — любая ошибка загрузки = пробуем следующий вариант
                self.error = f"faster-whisper на {device}: {e}"
                print(f"[stt] {self.error}")
        return False

    def close(self) -> None:
        if self.server and self.server.poll() is None:
            self.server.terminate()

    # --- распознавание -------------------------------------------------------

    @property
    def ready(self) -> bool:
        return self.backend is not None

    def transcribe(self, audio: bytes, wait: float = 0) -> str | None:
        """Распознаёт фразу (16 кГц, 16 бит, моно). None — если Whisper недоступен или ничего не понял."""
        if wait:
            self._ready.wait(wait)
        if not self.ready or len(audio) < SAMPLE_RATE * 2 * 0.3:  # короче 0.3 с — нечего слушать
            return None
        try:
            text = self._cpp(audio) if self.backend == "cpp" else self._faster(audio)
        except Exception as e:  # noqa: BLE001
            print(f"[stt] ошибка Whisper: {e}")
            return None
        text = " ".join(text.split())
        low = text.lower()
        if not text or any(h in low for h in HALLUCINATIONS):
            return None
        return text

    def _cpp(self, audio: bytes) -> str:
        r = self.http.post(
            f"{self.url}/inference",
            files={"file": ("speech.wav", _wav(audio), "audio/wav")},
            data={"response_format": "json", "temperature": "0.0", "language": "ru", "prompt": self.prompt},
            timeout=30,
        )
        r.raise_for_status()
        return r.json().get("text", "")

    def _faster(self, audio: bytes) -> str:
        import numpy as np

        samples = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _ = self.model.transcribe(
            samples, language="ru", beam_size=5, initial_prompt=self.prompt,
            condition_on_previous_text=False, vad_filter=False,
        )
        return " ".join(s.text.strip() for s in segments)
