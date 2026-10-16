"""Скачивает русскую модель Vosk для оффлайн-распознавания речи в папку models/."""

import sys
import urllib.request
import zipfile
from pathlib import Path

MODELS = {
    "small": "vosk-model-small-ru-0.22",  # ~45 МБ, быстро, хватает для команд
    "big": "vosk-model-ru-0.42",          # ~1.8 ГБ, точнее, нужно много RAM
}

def main() -> None:
    size = sys.argv[1] if len(sys.argv) > 1 else "small"
    name = MODELS[size]
    models_dir = Path(__file__).resolve().parent.parent / "models"
    target = models_dir / name
    if target.exists():
        print(f"Модель уже есть: {target}")
        return
    models_dir.mkdir(exist_ok=True)
    url = f"https://alphacephei.com/vosk/models/{name}.zip"
    archive = models_dir / f"{name}.zip"
    print(f"Качаю {url} ...")

    def progress(blocks, block_size, total):
        if total > 0:
            print(f"\r{min(100, blocks * block_size * 100 // total)}%", end="", flush=True)

    urllib.request.urlretrieve(url, archive, progress)
    print("\nРаспаковываю...")
    with zipfile.ZipFile(archive) as z:
        z.extractall(models_dir)
    archive.unlink()
    print(f"Готово: {target}")
    if size == "big":
        print(f"Не забудь прописать в config.yaml: vosk_model: models/{name}")

if __name__ == "__main__":
    main()
