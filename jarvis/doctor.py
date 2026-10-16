"""Проверка всего хозяйства: python -m jarvis --doctor.

Нейросеть (и умеет ли модель вызывать инструменты), микрофон (и слышит ли он тебя),
Vosk, Whisper, голос. Пишет, что не так и что делать.
"""

from __future__ import annotations

import platform
import sys
import time

from .config import Config

OK, BAD, WARN = "[OK] ", "[!!] ", "[??] "


def run_doctor(cfg: Config) -> None:
    print(f"=== Проверка Джарвиса ===\nPython {platform.python_version()} на {platform.system()} {platform.release()}\n")
    _llm(cfg)
    _vosk(cfg)
    _mic(cfg)
    _whisper(cfg)
    _voice(cfg)
    print("\nГотово. Если что-то с [!!] — кинь этот вывод (или файл logs\\jarvis.log).")


def _llm(cfg: Config) -> None:
    from .llm import LLM, LLMError

    llm = LLM(cfg)
    problem = llm.check()
    if problem:
        print(f"{BAD}Нейросеть: {problem}")
        return
    print(f"{OK}Нейросеть: {llm.backend} {llm.url}, модель {cfg.model}")
    tool = {"name": "ping", "description": "Проверка связи. Вызови этот инструмент.",
            "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}
    try:
        t = time.time()
        reply = llm.chat([{"role": "user", "content": "Вызови инструмент ping с текстом «привет». Ничего не пиши."}],
                         [tool])
        dt = time.time() - t
    except LLMError as e:
        print(f"{BAD}Запрос к модели не прошёл: {e}")
        return
    if reply["tool_calls"]:
        print(f"{OK}Модель умеет вызывать инструменты (ответ за {dt:.1f} с)")
    else:
        print(f"{WARN}Модель НЕ вызвала инструмент (ответила текстом: {reply['content'][:60]!r}). Простые команды "
              f"будут работать, сложные — плохо. Возьми модель со значком 🔨 в LM Studio.")
    if dt > 10:
        print(f"{WARN}Ответ медленный ({dt:.0f} с): модель не влезает в видеопамять? Возьми поменьше.")


def _vosk(cfg: Config) -> None:
    if cfg.vosk_model_path.exists():
        print(f"{OK}Vosk: {cfg.vosk_model_path.name}")
    else:
        print(f"{BAD}Vosk: нет модели {cfg.vosk_model_path}. Запусти: python scripts\\download_model.py")


def _mic(cfg: Config) -> None:
    try:
        import sounddevice as sd
    except Exception as e:  # noqa: BLE001
        print(f"{BAD}Микрофон: sounddevice не работает ({e})")
        return
    try:
        dev = sd.query_devices(cfg.mic_device, "input")
    except Exception as e:  # noqa: BLE001
        print(f"{BAD}Микрофон: {e}. Список: python -m jarvis --list-mics")
        return
    print(f"{OK}Микрофон: {dev['name']}")
    print("     Скажи что-нибудь секунды три...")
    import array

    chunks: list[bytes] = []
    try:
        # RawInputStream — без numpy (его может не быть на свежем Python).
        with sd.RawInputStream(samplerate=16000, channels=1, dtype="int16", device=cfg.mic_device,
                               callback=lambda data, *a: chunks.append(bytes(data))):
            time.sleep(3)
    except Exception as e:  # noqa: BLE001
        print(f"{BAD}Не получилось записать звук: {e}")
        return
    samples = array.array("h", b"".join(chunks))
    peak = max((abs(v) for v in samples), default=0)
    if peak < 300:
        print(f"{BAD}Микрофон почти ничего не слышит (пик {peak}). Проверь, тот ли выбран (mic_device) "
              f"и громкость микрофона в Windows.")
    elif peak > 32000:
        print(f"{WARN}Звук перегружен (пик {peak}) — убавь усиление микрофона в Windows.")
    else:
        print(f"{OK}Микрофон слышит (пик {peak}).")


def _whisper(cfg: Config) -> None:
    if cfg.stt_engine != "whisper":
        print(f"{WARN}Whisper выключен (stt_engine: {cfg.stt_engine}) — распознавание только Vosk.")
        return
    from .stt import WhisperSTT

    print("     Запускаю Whisper (до минуты)...")
    w = WhisperSTT(cfg)
    w._ready.wait(100)
    if w.ready:
        print(f"{OK}Whisper: {w.backend}")
    else:
        print(f"{BAD}Whisper: {w.error}. Поставь: python scripts\\setup_whisper.py")
    w.close()


def _voice(cfg: Config) -> None:
    from .speaker import Speaker

    sp = Speaker(cfg)
    print(f"     Говорю тестовую фразу голосом «{sp.engine}»...")
    try:
        sp.say("Проверка связи. Джарвис на месте.")
        print(f"{OK}Голос: {sp.engine}. Если ничего не услышал — проверь звук в Windows.")
    except Exception as e:  # noqa: BLE001
        print(f"{BAD}Голос: {e}")
    if cfg.tts_engine == "piper" and sp.engine != "piper":
        print(f"{WARN}Piper не найден, говорю голосом {sp.engine}. Поставь: python scripts\\setup_piper.py")


if __name__ == "__main__":  # pragma: no cover
    from .config import load_config

    run_doctor(load_config(sys.argv[1] if len(sys.argv) > 1 else None))
