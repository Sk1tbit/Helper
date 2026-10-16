"""Ставит whisper.cpp (точное распознавание речи) в tools/whisper — работает на любой версии Python.

    python scripts/setup_whisper.py              # процессор, модель small (~0.5 ГБ)
    python scripts/setup_whisper.py --model turbo  # лучшее качество (~0.6 ГБ), лучше с --cuda
    python scripts/setup_whisper.py --cuda       # сборка для видеокарт NVIDIA (быстрее)

Модели: tiny, base, small, medium, turbo (large-v3-turbo, сжатая q5_0).
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "tools" / "whisper"
RELEASES = "https://api.github.com/repos/ggml-org/whisper.cpp/releases"
MODELS = {
    "tiny": "ggml-tiny.bin",
    "base": "ggml-base.bin",
    "small": "ggml-small.bin",
    "medium": "ggml-medium.bin",
    "turbo": "ggml-large-v3-turbo-q5_0.bin",
}
MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{}"


def download(url: str, dest: Path | None = None) -> bytes | None:
    req = urllib.request.Request(url, headers={"User-Agent": "jarvis-setup"})
    with urllib.request.urlopen(req) as r:
        total = int(r.headers.get("Content-Length") or 0)
        chunks, done = [], 0
        out = open(dest, "wb") if dest else None
        try:
            while chunk := r.read(1 << 20):
                done += len(chunk)
                if out:
                    out.write(chunk)
                else:
                    chunks.append(chunk)
                if total:
                    print(f"\r  {done * 100 // total}% ({done >> 20} из {total >> 20} МБ)", end="", flush=True)
        finally:
            if out:
                out.close()
        print()
        return None if dest else b"".join(chunks)


def pick_asset(cuda: bool) -> tuple[str, str]:
    req = urllib.request.Request(RELEASES, headers={"User-Agent": "jarvis-setup"})
    releases = json.load(urllib.request.urlopen(req))
    pattern = r"whisper-cublas-12[\d.]*-bin-x64\.zip" if cuda else r"whisper-bin-x64\.zip"
    for rel in releases:  # в некоторых релизах нет сборок под Windows — берём последний, где есть
        for asset in rel.get("assets", []):
            if re.fullmatch(pattern, asset["name"]):
                return rel["tag_name"], asset["browser_download_url"]
    raise SystemExit("Не нашёл готовую сборку whisper.cpp для Windows. Скачай вручную с "
                     "https://github.com/ggml-org/whisper.cpp/releases и распакуй в tools/whisper")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="small", choices=sorted(MODELS))
    ap.add_argument("--cuda", action="store_true", help="сборка для видеокарт NVIDIA")
    args = ap.parse_args()

    if sys.platform != "win32":
        print("Готовые сборки есть только под Windows. На Linux/Mac собери whisper.cpp сам "
              "и положи whisper-server и модель в tools/whisper.")
    TARGET.mkdir(parents=True, exist_ok=True)

    exe = [p for n in ("whisper-server.exe", "server.exe") for p in TARGET.rglob(n)]
    if not exe:
        tag, url = pick_asset(args.cuda)
        print(f"Качаю whisper.cpp {tag}{' (CUDA)' if args.cuda else ''}...")
        data = download(url)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            z.extractall(TARGET)
        exe = [p for n in ("whisper-server.exe", "server.exe") for p in TARGET.rglob(n)]
        if not exe:
            raise SystemExit("В архиве нет whisper-server.exe — скачай другую сборку вручную.")
    print(f"Программа: {exe[0]}")

    name = MODELS[args.model]
    model = TARGET / name
    if not model.exists():
        for old in TARGET.glob("ggml-*.bin"):  # Джарвис берёт самую большую модель — уберём лишние
            old.unlink()
        print(f"Качаю модель {name}...")
        tmp = model.with_suffix(".part")
        download(MODEL_URL.format(name), tmp)
        tmp.rename(model)
    print(f"Модель: {model}\nГотово. В config.yaml: stt_engine: whisper")


if __name__ == "__main__":
    main()
