@echo off
cd /d "%~dp0"
set "TEMP=%~dp0work"
set "TMP=%~dp0work"
python tools\build.py
pause
