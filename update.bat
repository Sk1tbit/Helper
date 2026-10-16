@echo off
cd /d "%~dp0"
rem Downloads the latest Jarvis from GitHub and updates the code.
rem Keeps: config.yaml, memory, models, tools, .venv, logs.
set REPO=https://github.com/sk1tbit/helper/archive/refs/heads/claude/voice-pc-assistant-8q9fwh.zip
set TMP_DIR=%TEMP%\jarvis_update
echo === Jarvis update ===
if exist "%TMP_DIR%" rmdir /s /q "%TMP_DIR%"
mkdir "%TMP_DIR%"
echo Downloading...
powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol='Tls12'; Invoke-WebRequest -UseBasicParsing '%REPO%' -OutFile '%TMP_DIR%\jarvis.zip'"
if errorlevel 1 (
  echo [!] Download failed. Check the internet connection.
  pause
  exit /b 1
)
powershell -NoProfile -Command "Expand-Archive -Force '%TMP_DIR%\jarvis.zip' '%TMP_DIR%'"
for /d %%D in ("%TMP_DIR%\helper-*" "%TMP_DIR%\Helper-*") do set SRC=%%D
if not defined SRC (
  echo [!] Unexpected archive layout.
  pause
  exit /b 1
)
echo Updating files...
robocopy "%SRC%" "%~dp0." /E /NFL /NDL /NJH /NJS /NP /XD .venv memory models tools logs .git /XF config.yaml >nul
if errorlevel 8 (
  echo [!] Copy failed.
  pause
  exit /b 1
)
rmdir /s /q "%TMP_DIR%"
if exist .venv\Scripts\python.exe (
  echo Updating packages...
  .venv\Scripts\python.exe -m pip install -q -r requirements.txt
)
echo.
echo Done. New settings are listed in config.example.yaml - copy the ones you want into config.yaml.
pause
