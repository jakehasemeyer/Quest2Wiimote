"""Generate Quest profiles and back up the selected Dolphin configuration before applying them."""
from pathlib import Path
from paths import ROOT, DOLPHIN_USER, PROFILES

NAME = 'Quest2Wiimote'


def profile(slot=0, nunchuk=True, pointer=False):
    other = 'DSUClient/1/Quest2Wiimote:'
    rows = ['[Profile]', f'Device = DSUClient/{slot}/{NAME}', 'Extension = '+('`PS`' if nunchuk else 'None'),
            'Extension/Attach MotionPlus = True','IR/Vertical Offset = 10',
            f'Rumble/Motor = `XInput/{slot+2}/Gamepad:Motor L` | `XInput/{slot+2}/Gamepad:Motor R`',
            'Buttons/A = `Circle`', 'Buttons/B = `R2`', 'Buttons/1 = `Cross`', 'Buttons/2 = `R1`',
            'Buttons/+ = `L3`', f'Buttons/- = `{other}Options`' if nunchuk else 'Buttons/- = `Options`',
            f'Buttons/Home = `{other}Circle`' if nunchuk else 'Buttons/Home = `Circle` & `L3`',
            'D-Pad/Up = `Pad N`', 'D-Pad/Down = `Pad S`', 'D-Pad/Left = `Pad W`', 'D-Pad/Right = `Pad E`',
            'IMUIR/Enabled = True', f'IMUIR/Recenter = `{other}L3`' if nunchuk else 'IMUIR/Recenter = `L3` & `R1`',
            'IMUGyroscope/Dead Zone = 0.5', 'IMUGyroscope/Calibration Period = 3']
    for direction in ['Up', 'Down', 'Left', 'Right', 'Forward', 'Backward']:
        rows.append(f'IMUAccelerometer/{direction} = `Accel {direction}`')
    for direction in ['Pitch Up', 'Pitch Down', 'Roll Left', 'Roll Right', 'Yaw Left', 'Yaw Right']:
        rows.append(f'IMUGyroscope/{direction} = `Gyro {direction}`')
    if nunchuk:
        rows += [f'Nunchuk/Buttons/C = `{other}R1`', f'Nunchuk/Buttons/Z = `{other}R2`']
        for direction, axis in [('Up','Y+'), ('Down','Y-'), ('Left','X-'), ('Right','X+')]:
            rows.append(f'Nunchuk/Stick/{direction} = `{other}Left {axis}`')
        rows.append('Nunchuk/Stick/Dead Zone = 12')
        for direction in ['Up', 'Down', 'Left', 'Right', 'Forward', 'Backward']:
            rows.append(f'Nunchuk/IMUAccelerometer/{direction} = `{other}Accel {direction}`')
    if pointer:
        rows=[row for row in rows if not row.startswith('IMUIR/Enabled')]
        rows += ['IMUIR/Enabled = False',
                 'IR/Relative Input = False','IR/Horizontal Offset = 0','IR/Dead Zone = 0']
        rows += ['IR/Total Yaw = 25','IR/Total Pitch = 20','IR/Auto-Hide = False','IR/Center = 0 0']
        rows += ['IR/Hide = 1','IRPassthrough/Enabled = True']
        for index,inputs in enumerate((('Square','Left X+','Triangle','Left Y+'),('L1','Right X+','L2','Right Y+')),1):
            for axis,high,low in (('X',inputs[0],inputs[1]),('Y',inputs[2],inputs[3])):
                rows.append(f'IRPassthrough/Object {index} {axis} = (`{high}` * 32640 + `{low}` * 127) / 32767')
            rows.append(f'IRPassthrough/Object {index} Size = `Touch Button` * (2 / 15)')
    return '\n'.join(rows)+'\n'


def generate():
    dest = PROFILES
    dest.mkdir(parents=True, exist_ok=True)
    for name, slot, nunchuk in [('Quest - Wiimote + Nunchuk',0,True), ('Quest - Right Wiimote',0,False), ('Quest - Left Wiimote',1,False)]:
        (dest/(name+'.ini')).write_text(profile(slot,nunchuk), encoding='utf-8')
    (dest/'Quest - Virtual IR + Nunchuk.ini').write_text(profile(pointer=True),encoding='utf-8')
    (dest/'DSUClient.ini.example').write_text('[Server]\nEnabled = True\nEntries = Quest2Wiimote:127.0.0.1:26760;\n', encoding='utf-8')
    return dest


def configure_dolphin(pointer=True):
    import shutil
    config=DOLPHIN_USER/'Config'
    config.mkdir(parents=True,exist_ok=True)
    import hashlib
    backup=ROOT/'data/dolphin-config-backups'/hashlib.sha256(str(config).encode()).hexdigest()[:10]
    if not backup.exists():
        shutil.copytree(config,backup)
        (backup/'original-path.txt').write_text(str(config))
    dest=config/'Profiles/Wiimote';dest.mkdir(parents=True,exist_ok=True)
    for p in generate().glob('*.ini'): shutil.copy2(p,dest/p.name)
    current=config/'WiimoteNew.ini'
    import configparser
    ini=configparser.ConfigParser(interpolation=None);ini.optionxform=str
    if current.exists(): ini.read(current,encoding='utf-8')
    previous=dict(ini['Wiimote1']) if ini.get('Wiimote1','Device',fallback='')=='DSUClient/0/'+NAME else {}
    ini['Wiimote1']=dict(line.split(' = ',1) for line in profile(pointer=pointer).splitlines()[1:])
    for key,value in previous.items():
        if key.startswith(('Buttons/','D-Pad/','Hotkeys/','Nunchuk/Buttons/','Nunchuk/Stick/')):
            ini['Wiimote1'][key]=value
    ini['Wiimote1']['Source']='1'
    with current.open('w',encoding='utf-8') as f: ini.write(f)
    dsu=configparser.ConfigParser(interpolation=None);dsu.optionxform=str
    dsu.read(config/'DSUClient.ini',encoding='utf-8')
    if 'Server' not in dsu:dsu['Server']={}
    entries=[entry for entry in dsu['Server'].get('Entries','').split(';')
             if entry and not entry.endswith((':127.0.0.1:26760',':localhost:26760'))]
    dsu['Server']['Enabled']='True'
    dsu['Server']['Entries']=';'.join(entries+['Quest2Wiimote:127.0.0.1:26760'])+';'
    with (config/'DSUClient.ini').open('w',encoding='utf-8') as f:dsu.write(f)


if __name__ == '__main__':
    print(generate())
