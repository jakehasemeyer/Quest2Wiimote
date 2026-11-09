"""Build the app-local Dolphin output adapter with the installed Windows C compiler."""
import os
from pathlib import Path
import shutil
import subprocess
import hashlib
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'desktop'))
from paths import DOLPHIN


def main():
    msvc = max(Path('C:/Program Files/Microsoft Visual Studio/2022').glob('*/VC/Tools/MSVC/*'))
    sdk = max(Path('C:/Program Files (x86)/Windows Kits/10/Include').iterdir())
    libraries = sdk.parents[1]/'Lib'/sdk.name
    env = os.environ.copy()
    env['TEMP'] = env['TMP'] = str(ROOT/'work')
    env['PATH'] = str(msvc/'bin/Hostx64/x64')+os.pathsep+env['PATH']
    env['INCLUDE'] = os.pathsep.join(map(str, [msvc/'include', sdk/'ucrt', sdk/'shared', sdk/'um']))
    env['LIB'] = os.pathsep.join(map(str, [msvc/'lib/x64', libraries/'ucrt/x64', libraries/'um/x64']))
    build = ROOT/'work/rumble'; build.mkdir(exist_ok=True)
    subprocess.run([str(msvc/'bin/Hostx64/x64/cl.exe'), '/nologo', '/std:c11', '/LD', '/O2', '/MT',
        '/Fo'+str(build/'dolphin_rumble.obj'), str(ROOT/'desktop/dolphin_rumble.c'), 'ws2_32.lib',
        '/link', '/DEF:'+str(ROOT/'desktop/dolphin_rumble.def'), '/OUT:'+str(build/'xinput1_4.dll'),
        '/IMPLIB:'+str(build/'xinput1_4.lib')], env=env, cwd=build, check=True)
    dest = DOLPHIN/'xinput1_4.dll'
    marker=build/'installed.sha256'
    if dest.exists() and (not marker.exists() or hashlib.sha256(dest.read_bytes()).hexdigest()!=marker.read_text()):
        raise RuntimeError('An existing or modified XInput adapter is present; preserve it before replacing it.')
    shutil.copy2(build/'xinput1_4.dll', dest)
    (build/'installed.sha256').write_text(hashlib.sha256(dest.read_bytes()).hexdigest())
    print('Built:', dest)


if __name__ == '__main__': main()
