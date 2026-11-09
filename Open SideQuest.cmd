@echo off
cd /d "%~dp0"
set "TEMP=%~dp0work"
set "TMP=%~dp0work"
set "APPDATA=%~dp0tools\SideQuest-data\Roaming"
set "LOCALAPPDATA=%~dp0tools\SideQuest-data\Local"
if not exist "%APPDATA%" mkdir "%APPDATA%"
if not exist "%LOCALAPPDATA%" mkdir "%LOCALAPPDATA%"
start "" "%~dp0tools\SideQuest\SideQuest.exe" --user-data-dir="%~dp0tools\SideQuest-data\UserData" --disk-cache-dir="%~dp0tools\SideQuest-data\Cache"
