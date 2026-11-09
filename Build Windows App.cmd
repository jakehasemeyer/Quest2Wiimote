@echo off
cd /d "%~dp0"
set "TEMP=%~dp0work"
set "TMP=%~dp0work"
python tools\build_rumble.py && python tools\build_desktop.py
pause
