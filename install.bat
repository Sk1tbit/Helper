@echo off
cd /d "%~dp0"
echo === Jarvis setup ===
python --version >nul 2>nul
if errorlevel 1 (
  echo [!] Python not found. Install Python 3.10-3.13 from https://www.python.org
  echo     and tick "Add python.exe to PATH" during install.
  pause
  exit /b 1
)
python --version
if not exist .venv (
  echo Creating virtual environment...
  python -m venv .venv
  if errorlevel 1 (
    echo [!] Could not create .venv
    pause
    exit /b 1
  )
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 (
  echo [!] Failed to install packages, see the error above.
  pause
  exit /b 1
)
python scripts\download_model.py
if not exist config.yaml copy config.example.yaml config.yaml >nul
where ollama >nul 2>nul
if errorlevel 1 (
  echo Ollama not found - that's fine if you use LM Studio.
) else (
  echo Pulling Ollama models, this takes a while...
  ollama pull llama3.1:8b
  ollama pull llama3.2-vision
)
echo.
echo Done. Edit config.yaml if needed and start run.bat
pause
