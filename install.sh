#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python scripts/download_model.py
[ -f config.yaml ] || cp config.example.yaml config.yaml
if command -v ollama >/dev/null; then ollama pull llama3.1:8b; ollama pull llama3.2-vision;
else echo "Поставь Ollama с https://ollama.com и выполни: ollama pull llama3.1:8b && ollama pull llama3.2-vision"; fi
echo "Готово. Запускай: .venv/bin/python -m jarvis"
