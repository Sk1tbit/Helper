@echo off
cd /d "%~dp0"
echo === Jarvis setup ===
rem Prefer a well-supported Python if several are installed.
set PY=
for %%V in (3.12 3.13 3.11 3.10) do (
  if not defined PY (
    py -%%V --version >nul 2>nul && set "PY=py -%%V"
  )
)
if not defined PY set PY=python
%PY% --version >nul 2>nul
if errorlevel 1 set PY=py -3
%PY% --version >nul 2>nul
if errorlevel 1 (
  echo [!] Python not found. Install Python 3.12 from https://www.python.org
  echo     and tick "Add python.exe to PATH" during install.
  echo     Then open a NEW cmd window and run install.bat again.
  pause
  exit /b 1
)
%PY% --version
if not exist .venv (
  echo Creating virtual environment...
  %PY% -m venv .venv
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
echo.
echo Installing whisper.cpp for accurate speech recognition (~0.5 GB)...
python scripts\setup_whisper.py
if errorlevel 1 echo [i] whisper.cpp was not installed - Jarvis will use Vosk only. Retry: python scripts\setup_whisper.py
echo.
echo Installing offline Piper voice (~70 MB)...
python scripts\setup_piper.py dmitri
if errorlevel 1 echo [i] Piper was not installed - Jarvis will use the edge voice. Retry: python scripts\setup_piper.py
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
