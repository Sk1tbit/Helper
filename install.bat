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
echo Готово. Впиши API-ключ в config.yaml (или задай ANTHROPIC_API_KEY) и запускай run.bat
pause
