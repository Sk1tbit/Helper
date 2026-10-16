#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python scripts/download_model.py
[ -f config.yaml ] || cp config.example.yaml config.yaml
echo "Готово. Впиши API-ключ в config.yaml и запускай: .venv/bin/python -m jarvis"
