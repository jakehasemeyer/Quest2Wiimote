"""Freeze the native Qt desktop app into a Windows EXE; all build/cache files stay on E:."""
import os
import filecmp
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
os.environ['TEMP']=os.environ['TMP']=str(ROOT/'work')
os.environ['PYINSTALLER_CONFIG_DIR']=str(ROOT/'work/pyinstaller-cache')


def main():
    python=ROOT/'tools/desktop-venv/Scripts/python.exe'
    if not python.exists():
        subprocess.run([sys.executable,'-m','venv',str(python.parents[1])],check=True)
        subprocess.run([str(python),'-m','pip','install','--cache-dir',str(ROOT/'tools/downloads/pip'),'PySide6-Essentials','pyinstaller'],check=True)
    # Resolve Windows ICU from the OS, rather than an unrelated tool's PATH entry.
    os.environ['PATH']=os.pathsep.join([str(python.parent),sys.base_prefix,
        str(Path(os.environ['SystemRoot'])/'System32'),os.environ['SystemRoot']])
    icon_code="""from PySide6.QtGui import QImage,QPainter,QColor
image=QImage(128,128,QImage.Format_ARGB32);image.fill(QColor('#17392f'));p=QPainter(image);p.setRenderHint(QPainter.Antialiasing);p.setBrush(QColor('#70e7bf'));p.drawRoundedRect(18,36,92,54,15,15);p.setBrush(QColor('#17392f'));p.drawEllipse(34,53,20,20);p.drawEllipse(74,53,20,20);p.end();image.save('work/Quest2Wiimote.ico')
"""
    subprocess.run([str(python),'-c',icon_code],cwd=ROOT,check=True)
    subprocess.run([str(python),'-m','PyInstaller','--noconfirm','--clean','--onedir','--windowed',
        '--name','Quest2Wiimote','--icon',str(ROOT/'work/Quest2Wiimote.ico'),
        '--distpath',str(ROOT/'work/desktop-package'),'--workpath',str(ROOT/'work/desktop-build'),'--specpath',str(ROOT/'work'),
        '--paths',str(ROOT/'desktop'),'--add-data',str(ROOT/'assets')+';assets','--exclude-module','tkinter','--exclude-module','unittest',
        str(ROOT/'desktop/native.py')],cwd=ROOT,check=True)
    if '--stage-only' in sys.argv:
        print('Staged Windows app:',ROOT/'work/desktop-package/Quest2Wiimote')
        return
    # Running external apps may still hold an identical runtime DLL from the prior build.
    def copy_changed(source,target):
        if not Path(target).exists() or not filecmp.cmp(source,target,shallow=False):
            shutil.copy2(source,target)
        return target
    shutil.copytree(ROOT/'work/desktop-package/Quest2Wiimote',ROOT/'dist/Quest2Wiimote',
                    dirs_exist_ok=True,copy_function=copy_changed)
    packages=python.parents[1]/'Lib/site-packages'
    notices=ROOT/'dist/Quest2Wiimote/licenses';notices.mkdir(exist_ok=True)
    for dist in packages.glob('*.dist-info'):
        if dist.name.lower().startswith(('pyside6','shiboken6')) and (dist/'licenses').exists():
            shutil.copytree(dist/'licenses',notices/dist.name,dirs_exist_ok=True)
    lock=subprocess.check_output([str(python),'-m','pip','freeze'],text=True)
    (ROOT/'tools/desktop-requirements.lock.txt').write_text(lock)
    print('Windows app:',ROOT/'dist/Quest2Wiimote/Quest2Wiimote.exe')


if __name__=='__main__':main()
