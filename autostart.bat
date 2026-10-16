@echo off
cd /d "%~dp0"
rem Adds Jarvis to Windows startup (shortcut in the Startup folder). Run again with "off" to remove.
if /i "%1"=="off" (
  del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Jarvis.lnk" 2>nul
  echo Jarvis removed from startup.
  pause
  exit /b 0
)
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup')+'\Jarvis.lnk'); $s.TargetPath='%~dp0run.bat'; $s.WorkingDirectory='%~dp0'; $s.WindowStyle=7; $s.Save()"
if errorlevel 1 (
  echo [!] Could not create the shortcut.
) else (
  echo Jarvis will start with Windows. To undo: autostart.bat off
)
pause
