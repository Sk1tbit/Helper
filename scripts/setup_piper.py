"""Ставит оффлайн-голос Piper в tools/piper — работает на любой версии Python.

    python scripts/setup_piper.py            # голос dmitri (мужской)
    python scripts/setup_piper.py denis      # другие: denis, ruslan (мужские), irina (женский)

После этого в config.yaml: tts_engine: piper, piper_voice: ru_RU-dmitri-medium.
"""

from __future__ import annotations

import io
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "tools" / "piper"
PIPER_ZIP = "https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_windows_amd64.zip"
VOICE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/{name}/medium/ru_RU-{name}-medium.onnx"
VOICES = ("dmitri", "denis", "ruslan", "irina")


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "jarvis-setup"})
    with urllib.request.urlopen(req) as r:
        total = int(r.headers.get("Content-Length") or 0)
        data, chunks = 0, []
        while chunk := r.read(1 << 20):
            chunks.append(chunk)
            data += len(chunk)
            if total:
                print(f"\r  {data * 100 // total}% ({data >> 20} из {total >> 20} МБ)", end="", flush=True)
    print()
    return b"".join(chunks)


def main() -> None:
    name = (sys.argv[1] if len(sys.argv) > 1 else "dmitri").lower()
    if name not in VOICES:
        raise SystemExit(f"Нет такого голоса. Есть: {', '.join(VOICES)}")
    TARGET.mkdir(parents=True, exist_ok=True)

    if not list(TARGET.rglob("piper.exe")):
        if sys.platform != "win32":
            raise SystemExit("Готовая сборка Piper здесь только под Windows. На Linux: pip install piper-tts "
                             "и положи бинарник piper в tools/piper.")
        print("Качаю Piper...")
        with zipfile.ZipFile(io.BytesIO(fetch(PIPER_ZIP))) as z:
            z.extractall(TARGET)

    voices = TARGET / "voices"
    voices.mkdir(exist_ok=True)
    onnx = voices / f"ru_RU-{name}-medium.onnx"
    if not onnx.exists():
        print(f"Качаю голос {name}...")
        url = VOICE_URL.format(name=name)
        (voices / f"{onnx.name}.json").write_bytes(fetch(url + ".json"))
        onnx.write_bytes(fetch(url))
    print(f"Готово. В config.yaml:\n  tts_engine: piper\n  piper_voice: ru_RU-{name}-medium\n  voice_effect: jarvis")


if __name__ == "__main__":
    main()
