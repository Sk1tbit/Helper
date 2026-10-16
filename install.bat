@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul || (echo Установи Python 3.10+ с python.org и поставь галочку "Add to PATH" & pause & exit /b 1)
if not exist .venv python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt || (pause & exit /b 1)
python scripts\download_model.py
if not exist config.yaml copy config.example.yaml config.yaml >nul
echo.
where ollama >nul 2>nul
if errorlevel 1 (
  echo Ollama не найдена. Поставь её с https://ollama.com и выполни:
  echo   ollama pull llama3.1:8b
  echo   ollama pull llama3.2-vision
) else (
  echo Качаю модели для Ollama, это надолго...
  ollama pull llama3.1:8b
  ollama pull llama3.2-vision
)
echo Готово. Запускай run.bat
pause
