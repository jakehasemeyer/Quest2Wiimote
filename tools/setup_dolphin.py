"""Install the official portable Dolphin release and configure only its local User folder."""
from pathlib import Path
import subprocess
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    version = '2609a'
    archive = ROOT/f'tools/downloads/dolphin-{version}-x64.7z'
    if not archive.exists():
        urllib.request.urlretrieve(f'https://dl.dolphin-emu.org/releases/{version}/{archive.name}',archive)
    target = ROOT/'tools/dolphin'
    target.mkdir(exist_ok=True)
    if not (target/'Dolphin-x64/Dolphin.exe').exists():
        subprocess.run(['tar','-xf',str(archive),'-C',str(target)],check=True)
    exe = next(target.rglob('Dolphin.exe'))
    (exe.parent/'portable.txt').touch()
    config = exe.parent/'User/Config'
    config.mkdir(parents=True,exist_ok=True)
    import sys
    sys.path.insert(0,str(ROOT/'desktop'))
    from profiles import generate, profile
    import shutil
    dest=config/'Profiles/Wiimote';dest.mkdir(parents=True,exist_ok=True)
    for p in generate().glob('*.ini'):
        shutil.copy2(p,dest/p.name)
    (config/'DSUClient.ini').write_text('[Server]\nEnabled = True\nEntries = Quest2Wiimote:127.0.0.1:26760;\n')
    (config/'WiimoteNew.ini').write_text(profile().replace('[Profile]','[Wiimote1]')+'Source = 1\n\n[Wiimote2]\nSource = 0\n[Wiimote3]\nSource = 0\n[Wiimote4]\nSource = 0\n')
    (ROOT/'dist/Dolphin-path.txt').write_text(str(exe))
    print('Configured portable Dolphin:',exe)


if __name__=='__main__':
    main()
