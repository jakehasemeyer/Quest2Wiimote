from pathlib import Path
import sys
import json

FROZEN = getattr(sys, 'frozen', False)
# Frozen release layout: everything lives next to the EXE. Dev checkout: project tree.
ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parents[1]
SETTINGS = ROOT/'data/desktop.json'
preferences = json.loads(SETTINGS.read_text()) if SETTINGS.exists() else {}
DOLPHIN = Path(preferences.get('dolphin_dir', ROOT/'tools/dolphin/Dolphin-x64'))
DOLPHIN_USER = Path(preferences.get('dolphin_user', DOLPHIN/'User'))
APK = ROOT/('Quest2Wiimote.apk' if FROZEN else 'dist/Quest2Wiimote.apk')
PROFILES = ROOT/('DolphinProfiles' if FROZEN else 'dist/DolphinProfiles')
WEB = ROOT/('web' if FROZEN else 'desktop/web')
ADB_DIR = ROOT/('adb' if FROZEN else 'tools/adb')
SCRCPY_DIR = ROOT/('scrcpy' if FROZEN else 'tools/scrcpy')
WORK = ROOT/'work'
