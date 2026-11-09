"""Desktop bridge: standard-library Python, local web UI, TCP telemetry, DSU output."""
import argparse
from collections import deque
import ipaddress
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import socket
import socketserver
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import webbrowser

from dsu import Server as DSU
from motion import Motion,rotate,multiply
from profiles import generate
from paths import ROOT, DOLPHIN, SETTINGS, preferences, APK, WEB, ADB_DIR, SCRCPY_DIR, WORK
from screen import VirtualScreen,plane_orientation,add
from haptics import Haptics, Receiver as RumbleReceiver
from hand_tracking import Gestures

WORK.mkdir(exist_ok=True)
# Releases ship no platform-tools; fall back to adb on PATH.
ADB = (ADB_DIR/'platform-tools/adb.exe') if (ADB_DIR/'platform-tools/adb.exe').exists() else Path(shutil.which('adb') or 'adb')
PACKAGE = 'com.quest2wiimote.bridge'
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
os.environ['TEMP'] = os.environ['TMP'] = str(WORK)
TOKEN_FILE = WORK/'pairing-token.txt'
if not TOKEN_FILE.exists():
    TOKEN_FILE.write_text(secrets.token_hex(24))
TOKEN = TOKEN_FILE.read_text().strip()
EMPTY = {'p':[0.,0.,0.], 'q':[0.,0.,0.,1.], 'flags':0, 'active':False}
NEUTRAL = {'accel':[0.,-1.,0.], 'gyro':[0.,0.,0.], 'tracked':False, 'orientation':False}


class State:
    def __init__(self):
        self.lock = threading.RLock()
        self.frame = {'seq':0, 't':0., 'focused':False, 'head':dict(EMPTY), 'hands':[dict(EMPTY),dict(EMPTY)]}
        self.motion = [dict(NEUTRAL), dict(NEUTRAL)]
        self.processors = [Motion(), Motion()]
        self.last = 0
        self.arrivals = deque(maxlen=180)
        self.devices = []
        self.device = ''
        self.battery = None
        self.error = ''
        self.transport = 'USB'
        self.peer = ''
        self.session = None
        self.screen = VirtualScreen()
        self.haptics = Haptics()
        self.passthrough_requested = bool(preferences.get('passthrough', False))
        self.hand_tracking_requested = bool(preferences.get('hand_tracking', False))
        self.stick_deadzones = list(preferences.get('stick_deadzones', [.12, .12]))
        self.gestures = [Gestures(), Gestures()]
        self.calibrating = False
        self.aligning_dolphin = False
        self.left_anchor=None
        self.left_motion_at=0.
        self.left_idle=False
        self.left_pressed=False
        self.left_wake_consumed=False
        self.last_tracked=[None,None]
        self.tracking_lost_at=[None,None]
        self.tracking_hold_seconds=[20,15]

    def set_passthrough(self, enabled):
        if not isinstance(enabled,bool):raise ValueError('Passthrough must be true or false.')
        with self.lock:
            saved=json.loads(SETTINGS.read_text()) if SETTINGS.exists() else {}
            saved['passthrough']=enabled
            SETTINGS.parent.mkdir(parents=True,exist_ok=True)
            temp=SETTINGS.with_suffix('.tmp');temp.write_text(json.dumps(saved,indent=2));temp.replace(SETTINGS)
            self.passthrough_requested=enabled

    def update(self, frame, session, peer):
        if frame.get('version') != 1 or frame.get('token') != TOKEN:
            raise ValueError('Unpaired sender')
        if len(frame['hands']) != 2 or not isinstance(frame['focused'], bool):
            raise ValueError('Invalid frame')
        if not isinstance(frame.get('haptics', False), bool):
            raise ValueError('Invalid haptics capability')
        if not isinstance(frame.get('screen_control',False),bool):raise ValueError('Invalid screen capability')
        if not isinstance(frame.get('cursor_control',False),bool):raise ValueError('Invalid cursor capability')
        for name in ('hand_tracking_control','hand_tracking_supported','hand_tracking'):
            if not isinstance(frame.get(name,False),bool):raise ValueError('Invalid hand tracking capability')
        if not isinstance(frame.get('seq'),int) or not 0 <= frame['seq'] < 2**64:
            raise ValueError('Invalid sequence')
        timestamp = float(frame['t'])
        if not math.isfinite(timestamp) or timestamp < 0:
            raise ValueError('Invalid time')
        poses=[frame['head'],*frame['hands']]+[hand['grip'] for hand in frame['hands'] if 'grip' in hand]
        for obj in poses:
            if not isinstance(obj,dict):raise ValueError('Invalid pose')
            for name, size in [('p',3),('q',4)]:
                if len(obj[name]) != size or any(not isinstance(x,(float,int)) or not math.isfinite(x) for x in obj[name]):
                    raise ValueError('Invalid pose')
            norm = math.sqrt(sum(x*x for x in obj['q']))
            if not .5 < norm < 1.5:
                raise ValueError('Invalid orientation')
            obj['q'] = [x/norm for x in obj['q']]
            if not isinstance(obj.get('flags',0), int):
                raise ValueError('Invalid flags')
        for hand in frame['hands']:
            joints=hand.get('joints',[])
            if not isinstance(joints,list) or len(joints) not in (0,26):raise ValueError('Invalid hand joints')
            for joint in joints:
                if not isinstance(joint,list) or len(joint)!=3 or any(not isinstance(v,(int,float)) or not math.isfinite(v) or abs(v)>10000 for v in joint):raise ValueError('Invalid hand joint position')
            for name, size in [('v',3),('w',3),('stick',2)]:
                if len(hand[name]) != size or any(not isinstance(x,(float,int)) or not math.isfinite(x) or abs(x)>10000 for x in hand[name]):
                    raise ValueError('Invalid motion')
            for name in ['trigger','squeeze']:
                if not isinstance(hand[name],(float,int)) or not math.isfinite(hand[name]) or not 0 <= hand[name] <= 1:
                    raise ValueError('Invalid trigger')
            if not isinstance(hand['buttons'],int) or not isinstance(hand['active'],bool):
                raise ValueError('Invalid input')
            if not frame['focused']:
                hand['active'] = False
        frame.pop('token', None)
        with self.lock:
            reset_gestures = self.session != session or time.monotonic()-self.last>.3 or frame.get('hand_tracking',False)!=self.frame.get('hand_tracking',False)
            if self.session != session:
                self.haptics.clear()
                self.session = session
                self.processors = [Motion(),Motion()]
                self.arrivals.clear()
            elif timestamp <= self.frame['t']:
                return
            if reset_gestures or not frame['focused']:
                self.last_tracked=[None,None]
                self.tracking_lost_at=[None,None]
            if reset_gestures:self.gestures = [Gestures(),Gestures()]
            if frame.get('hand_tracking'):
                for slot,hand in enumerate(frame['hands']):self.gestures[slot].update(hand,slot,frame['head'])
            self.frame = frame
            self.screen.observe(frame)
            self.last = time.monotonic()
            left=frame['hands'][1]
            tracked=frame['focused'] and left['active'] and left['flags']&15==15
            lost_at=self.tracking_lost_at[1]
            if tracked and lost_at is not None and self.last-lost_at<self.tracking_hold_seconds[1]:
                self.left_idle=False;self.left_anchor=None
            pressed=tracked and bool(left['buttons'] or left['trigger']>.65 or left['squeeze']>.65)
            if not pressed:self.left_wake_consumed=False
            if tracked:
                moved=self.left_anchor is None or sum((a-b)**2 for a,b in zip(left['p'],self.left_anchor['p']))>.01**2 or abs(sum(a*b for a,b in zip(left['q'],self.left_anchor['q'])))<math.cos(math.radians(1.5))
                if self.left_idle and pressed and not self.left_pressed:
                    self.left_idle=False;self.left_wake_consumed=True;self.left_anchor=None
                    moved=True
                if not self.left_idle:
                    if moved:
                        self.left_anchor=dict(p=left['p'][:],q=left['q'][:]);self.left_motion_at=self.last
            if not self.left_idle and self.left_anchor is not None and self.last-self.left_motion_at>=15:self.left_idle=True
            self.left_pressed=pressed
            if frame.get('hand_tracking'):
                self.left_idle=self.left_wake_consumed=False
                self.left_anchor=None
            for slot,hand in enumerate(frame['hands']):
                if frame.get('hand_tracking') or not frame['focused']:
                    self.last_tracked[slot]=None
                elif hand['active'] and hand['flags']&15==15:
                    self.last_tracked[slot]=dict(hand) if slot==0 or not self.left_idle else None
                    self.tracking_lost_at[slot]=None
                elif self.last_tracked[slot] is not None and self.tracking_lost_at[slot] is None:
                    self.tracking_lost_at[slot]=self.last
                    self.tracking_hold_seconds[slot]=20 if slot==0 else 15
            right=frame['hands'][0]
            if self.tracking_lost_at[1] is not None and self.last-self.tracking_lost_at[1]<self.tracking_hold_seconds[1] and not (right['active'] and right['flags']&15==15):
                self.tracking_hold_seconds[1]=20
            self.arrivals.append(self.last)
            self.peer = peer
            self.motion = [p.update(h,timestamp) for p,h in zip(self.processors,frame['hands'])]

    def snapshot(self):
        with self.lock:
            now=time.monotonic()
            age = now-self.last if self.last else None
            live = age is not None and age < .3 and self.frame['focused']
            result = {**self.frame, 'hands':[dict(h) for h in self.frame['hands']], 'motion':[dict(m) for m in self.motion] if live else [dict(NEUTRAL),dict(NEUTRAL)],
                      'live':live, 'age_ms':round(age*1000) if age is not None else None,
                      'hz':round((len(self.arrivals)-1)/max(1e-6,self.arrivals[-1]-self.arrivals[0]),1) if age is not None and age < 1 and len(self.arrivals)>1 else 0,
                      'devices':self.devices, 'device':self.device, 'battery':self.battery, 'error':self.error, 'transport':self.transport, 'peer':self.peer}
            result['passthrough_desired']=self.passthrough_requested
            result['hand_tracking_desired']=self.hand_tracking_requested
            result['calibrating']=self.calibrating
            result['aligning_dolphin']=self.aligning_dolphin
            result['left_idle']=self.left_idle
            result['stick_deadzones']=self.stick_deadzones[:]
            if live and not self.hand_tracking_requested and not self.frame.get('hand_tracking'):
                for slot,saved in enumerate(self.last_tracked):
                    lost_at=self.tracking_lost_at[slot]
                    timeout=self.tracking_hold_seconds[slot]
                    if saved is not None and lost_at is not None and now-lost_at<timeout:
                        held=dict(saved,held=True,buttons=0,trigger=0.,squeeze=0.,stick=[0.,0.],
                                  v=[0.,0.,0.],w=[0.,0.,0.],lv=True,av=False)
                        result['hands'][slot]=held
                        result['motion'][slot]=Motion().update(held,self.frame['t'])
                        if slot==1:result['left_idle']=False
            if not live or self.hand_tracking_requested != bool(self.frame.get('hand_tracking')):
                for h in result['hands']:
                    h.update(active=False, buttons=0, trigger=0., squeeze=0., stick=[0.,0.], dpad=[0,0])
                result['motion']=[dict(NEUTRAL),dict(NEUTRAL)]
            if result['left_idle'] or self.left_wake_consumed:
                result['hands'][1].update(buttons=0,trigger=0.,squeeze=0.,stick=[0.,0.])
            if result['left_idle']:
                result['hands'][1]['active']=False
                result['motion'][1]=dict(NEUTRAL)
            if self.hand_tracking_requested:
                result['hands'][0]['dpad']=[max(-1,min(1,sum(h.get('dpad',[0,0])[axis] for h in result['hands']))) for axis in (0,1)]
            for hand,deadzone in zip(result['hands'],self.stick_deadzones):
                raw=list(hand.get('stick',[0.,0.])) if hand['active'] else [0.,0.]
                hand['raw_stick']=raw
                magnitude=math.hypot(*raw)
                gain=max(0.,min(1.,(magnitude-deadzone)/(1-deadzone)))/magnitude if magnitude else 0.
                hand['stick']=[v*gain for v in raw]
            result['screen'] = dict(self.screen.data)
            result['reference'] = self.screen.reference or self.frame['head']
            result['pointer'] = self.screen.pointer(result,allow_outside=self.aligning_dolphin)
            return result

    def set_stick_deadzone(self, slot, percent):
        if type(slot) is not int or slot not in (0,1) or type(percent) is not int or not 0<=percent<=50:
            raise ValueError('Stick deadzone must be between 0% and 50% for the right or left controller.')
        with self.lock:
            values=self.stick_deadzones[:];values[slot]=percent/100
            saved=json.loads(SETTINGS.read_text()) if SETTINGS.exists() else {}
            saved['stick_deadzones']=values
            SETTINGS.parent.mkdir(parents=True,exist_ok=True)
            temp=SETTINGS.with_suffix('.tmp');temp.write_text(json.dumps(saved,indent=2));temp.replace(SETTINGS)
            self.stick_deadzones=values

    def set_hand_tracking(self, enabled):
        if not isinstance(enabled,bool):raise ValueError('Hand tracking must be true or false.')
        with self.lock:
            saved=json.loads(SETTINGS.read_text()) if SETTINGS.exists() else {}
            saved['hand_tracking']=enabled
            SETTINGS.parent.mkdir(parents=True,exist_ok=True)
            temp=SETTINGS.with_suffix('.tmp');temp.write_text(json.dumps(saved,indent=2));temp.replace(SETTINGS)
            self.hand_tracking_requested=enabled
            self.gestures=[Gestures(),Gestures()]
            self.processors=[Motion(),Motion()]
            self.haptics.clear()


STATE = State()
COMMAND_LOCK = threading.Lock()


def adb(*args, serial=None, timeout=30):
    argv = [str(ADB)] + (['-s',serial] if serial else []) + list(map(str,args))
    done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW)
    if done.returncode:
        raise RuntimeError((done.stderr or done.stdout).strip())
    return done.stdout.strip()


def devices():
    result = []
    for line in adb('devices','-l').splitlines()[1:]:
        fields = line.split()
        if len(fields)>1:
            result.append({'serial':fields[0], 'status':fields[1], 'details':' '.join(fields[2:])})
    with STATE.lock:
        STATE.devices = result
        if not STATE.device or not any(d['serial']==STATE.device and d['status']=='device' for d in result):
            STATE.device = next((d['serial'] for d in result if d['status']=='device'),'')
        serial=STATE.device
    battery=None
    if serial:
        try:
            values=dict(line.strip().split(':',1) for line in adb('shell','dumpsys','battery',serial=serial,timeout=3).splitlines() if ':' in line)
            level=int(values['level']);maximum=int(values['scale'])
            if 0<=level<=maximum and maximum>0:battery=round(level*100/maximum)
        except (OSError,subprocess.SubprocessError,RuntimeError,ValueError,KeyError):pass
    with STATE.lock:
        if STATE.device==serial:STATE.battery=battery
    return result


def launch(serial, host='127.0.0.1', passthrough=None):
    if passthrough is None:passthrough=STATE.passthrough_requested
    adb('shell','am','force-stop',PACKAGE,serial=serial)
    adb('shell','am','broadcast','-a','com.oculus.vrpowermanager.prox_close',serial=serial)
    if host == '127.0.0.1':
        adb('reverse','tcp:26761','tcp:26761',serial=serial)
    output = adb('shell','am','start','-n',PACKAGE+'/android.app.NativeActivity','--es','host',host,
                 '--es','token',TOKEN,'--es','passthrough','true' if passthrough else 'false',
                 '--es','hand_tracking','true' if STATE.hand_tracking_requested else 'false',serial=serial)
    if 'Error' in output or 'Exception' in output:
        raise RuntimeError(output)
    return output


def command(action, data):
    with COMMAND_LOCK:
        STATE.error = ''
        if action == 'refresh':
            return {'devices':devices()}
        if action == 'passthrough':
            STATE.set_passthrough(data.get('enabled'))
            return {'message':'Passthrough preference saved and sent to the Quest.'}
        if action == 'hand-tracking':
            STATE.set_hand_tracking(data.get('enabled'))
            return {'message':'Hand tracking preference saved and sent to the Quest.'}
        if action == 'profiles':
            dest = generate()
            if os.name == 'nt':
                os.startfile(dest)
            return {'message':str(dest)}
        if action == 'ray-mode':
            with STATE.lock:STATE.screen.set_ray_mode(data.get('mode'))
            return {'message':'Pointing direction applied.'}
        if action == 'rumble':
            state = STATE.snapshot()
            slot = data.get('slot', 0)
            if type(slot) is not int or slot not in (0, 1):
                raise ValueError('Invalid controller')
            if not state['live'] or not state['hands'][slot]['active']:
                raise RuntimeError('Wake the controller and keep the Quest app focused first.')
            if not state.get('haptics'):
                raise RuntimeError('Install & start the updated Quest app to enable rumble.')
            STATE.haptics.pulse(slot)
            return {'message':('Right' if slot == 0 else 'Left')+' rumble test sent (200 ms).'}
        if action == 'dolphin':
            exe = DOLPHIN/'Dolphin.exe'
            if not exe.is_file():raise RuntimeError('Dolphin was not found: '+str(exe))
            # PyInstaller's DLL directory must not leak into the child emulator.
            if getattr(sys,'frozen',False):
                import ctypes
                ctypes.windll.kernel32.SetDllDirectoryW(None)
            mode=STATE.screen.data['display_mode']
            args=[str(exe),'-C','GFX.Settings.AspectRatio='+('2' if mode=='4:3' else '1'),
                  '-C','SYSCONF.IPL.AR='+('False' if mode=='4:3' else 'True')]
            try:subprocess.Popen(args,cwd=exe.parent)
            finally:
                if getattr(sys,'frozen',False):ctypes.windll.kernel32.SetDllDirectoryW(sys._MEIPASS)
            return {'message':'Dolphin opened from '+str(DOLPHIN)+' in '+mode+' Wii mode. Wii Remote 1 uses Quest + Nunchuk with rumble.'}
        if action == 'logs':
            serial = data.get('serial') or STATE.device
            text = adb('logcat','-d','-s','Quest2Wiimote:V','OpenXR-Loader:V','AndroidRuntime:E',serial=serial)
            dest = WORK/'quest-log.txt'; dest.write_text(text,encoding='utf-8')
            return {'message':text[-12000:] or 'No app logs yet.'}
        serial = data.get('serial') or STATE.device
        if not serial or not any(d['serial']==serial and d['status']=='device' for d in devices()):
            raise RuntimeError('Connect and authorize the Quest in ADB first.')
        STATE.device = serial
        if action in ('install','usb','wifi','lan') and 'passthrough' in data:
            STATE.set_passthrough(data['passthrough'])
        if action in ('install','usb'):
            if action == 'install':
                adb('install','-r',APK,serial=serial,timeout=120)
            launch(serial,passthrough=bool(data.get('passthrough')))
            STATE.transport = 'USB' if ':' not in serial else 'Wireless ADB tunnel'
            return {'message':'App started. Off-head keep-awake enabled for this session.'}
        if action == 'wifi':
            # Initial pairing requires USB; only enable TCP ADB on explicit request.
            address = data.get('ip','').strip()
            if not address:
                output = adb('shell','ip','-f','inet','addr','show','wlan0',serial=serial)
                words = output.split()
                address = words[words.index('inet')+1].split('/')[0]
            ipaddress.IPv4Address(address)
            if ':' not in serial:
                adb('tcpip','5555',serial=serial)
                time.sleep(1)
            target = address+':5555'
            output = adb('connect',target)
            if 'connected' not in output:
                raise RuntimeError(output)
            STATE.device = target
            launch(target,passthrough=bool(data.get('passthrough')))
            STATE.transport = 'Wireless ADB tunnel'
            devices()
            return {'message':'Wireless ADB connected. The USB cable can be disconnected. Use only on a trusted LAN.'}
        if action == 'lan':
            host = str(ipaddress.IPv4Address(data.get('host','')))
            launch(serial,host,passthrough=bool(data.get('passthrough')))
            STATE.transport = 'Wi-Fi TCP'
            return {'message':'App started over direct Wi-Fi. Allow private-network TCP 26761 in Windows Firewall.'}
        if action == 'mirror':
            exe = next((SCRCPY_DIR).glob('*/scrcpy.exe'))
            log = (WORK/'mirror.log').open('a',encoding='utf-8')
            subprocess.Popen([str(exe),'--serial',serial,'--window-title','Quest2Wiimote - Headset view','--max-size','1024','--video-bit-rate','4M','--no-audio','--no-control'],
                             cwd=exe.parent,stdout=log,stderr=log,creationflags=NO_WINDOW)
            log.close()
            return {'message':'Headset view opened. Quest 2 may omit passthrough pixels; check work/mirror.log if the window closes.'}
        if action == 'stop':
            adb('shell','am','force-stop',PACKAGE,serial=serial)
            adb('shell','am','broadcast','-a','com.oculus.vrpowermanager.automation_disable',serial=serial)
            adb('reverse','--remove','tcp:26761',serial=serial)
            return {'message':'App stopped. Physical proximity-sensor control restored.'}
        if action == 'wifi-off':
            adb('usb',serial=serial)
            STATE.device = ''
            return {'message':'Wireless ADB disabled; reconnect the USB cable.'}
        raise ValueError('Unknown action')


def screen_packet(state):
    screen=state['screen'];reference=state['reference']
    center=add(reference['p'],rotate(reference['q'],screen['center']))
    orientation=multiply(reference['q'],plane_orientation(screen['right'],screen['up']))
    return struct.pack('<4s11f',b'Q2SC',float(screen['calibrated']),screen['width'],screen['height'],
                       *center,*orientation,4/3 if screen['display_mode']=='4:3' else 16/9)


def cursor_packet(state):
    pointer=state['pointer'];valid=state['live'] and pointer.get('valid',False)
    return struct.pack('<4s4f',b'Q2CR',float(valid),pointer['u'] if valid else .5,
                       pointer['v'] if valid else .5,pointer.get('roll',0.) if valid else 0.)


class Telemetry(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(5)
        session = secrets.token_hex(8)
        last_passthrough = None
        last_hand_tracking = None
        last_screen = None
        try:
            while line := self.rfile.readline(16385):
                if len(line)>16384 or not line.endswith(b'\n'):
                    break
                STATE.update(json.loads(line),session,self.client_address[0])
                state = STATE.snapshot()
                if state.get('hand_tracking_control') and last_hand_tracking != state['hand_tracking_desired']:
                    last_hand_tracking=state['hand_tracking_desired']
                    self.connection.sendall(struct.pack('<4sff',b'Q2HT',float(last_hand_tracking),0.))
                if state.get('haptics'):
                    self.connection.sendall(struct.pack('<4sff', b'Q2HB', *STATE.haptics.values(state)))
                if state.get('passthrough_control') and last_passthrough != state['passthrough_desired']:
                    last_passthrough = state['passthrough_desired']
                    self.connection.sendall(struct.pack('<4sff', b'Q2PT', float(last_passthrough), 0.))
                if state.get('screen_control'):
                    packet=screen_packet(state)
                    if packet!=last_screen:
                        self.connection.sendall(packet);last_screen=packet
                if state.get('cursor_control'):
                    self.connection.sendall(cursor_packet(state))
        except (OSError, ValueError, KeyError, TypeError, IndexError, OverflowError):
            return
        finally:
            with STATE.lock:
                if STATE.session == session:
                    STATE.haptics.clear()


class TCP(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class UI(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,directory=str(WEB),**kwargs)

    def log_message(self,*args):
        pass

    def json(self, data, status=200):
        body = json.dumps(data,allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json')
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        if self.path == '/api/state':
            self.json({**STATE.snapshot(),'dsu_clients':len(DSU_SERVER.subscriptions),'dsu_packets':DSU_SERVER.sent})
        elif self.path == '/api/info':
            self.json({'path':str(ROOT), 'dsu':'127.0.0.1:26760', 'telemetry':26761})
        else:
            super().do_GET()

    def do_POST(self):
        host = self.headers.get('Host','')
        origin = self.headers.get('Origin')
        if host not in ('127.0.0.1:8765','localhost:8765') or (origin and origin not in ('http://127.0.0.1:8765','http://localhost:8765')):
            self.json({'error':'Local requests only'},403); return
        if self.headers.get('Content-Type','').split(';')[0] != 'application/json':
            self.json({'error':'JSON required'},415); return
        try:
            length = int(self.headers.get('Content-Length','0'))
            if not 0 < length <= 4096:
                raise ValueError('Invalid request length')
            data = json.loads(self.rfile.read(length))
            self.json(command(urllib.parse.urlparse(self.path).path.removeprefix('/api/'),data))
        except (ValueError, RuntimeError, OSError, subprocess.SubprocessError, StopIteration) as e:
            STATE.error = str(e)
            self.json({'error':str(e)},400)


def monitor():
    while True:
        try:
            devices()
        except (OSError,subprocess.SubprocessError,RuntimeError) as e:
            STATE.error = str(e)
        time.sleep(4)


def start_services(lan=False):
    global DSU_SERVER
    generate()
    tcp = TCP(('0.0.0.0' if lan else '127.0.0.1',26761),Telemetry)
    DSU_SERVER = DSU(STATE.snapshot)
    rumble = RumbleReceiver(STATE.haptics)
    http = ThreadingHTTPServer(('127.0.0.1',8765),UI)
    threading.Thread(target=tcp.serve_forever,daemon=True).start()
    threading.Thread(target=http.serve_forever,daemon=True).start()
    threading.Thread(target=monitor,daemon=True).start()
    return tcp,http,DSU_SERVER,rumble


def stop_services(services):
    tcp,http,dsu,rumble=services
    rumble.close()
    http.shutdown();http.server_close();tcp.shutdown();tcp.server_close();dsu.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--lan',action='store_true',help='Allow direct Quest telemetry over LAN TCP 26761. ADB tunnels work without this.')
    args = parser.parse_args()
    try:
        with urllib.request.urlopen('http://127.0.0.1:8765/api/info',timeout=.4) as response:
            existing = json.load(response)
        if existing.get('path') == str(ROOT):
            print('Quest2Wiimote is already running.',flush=True)
            if not args.no_browser:
                webbrowser.open('http://127.0.0.1:8765')
            return
    except (OSError,ValueError):
        pass
    services=start_services(args.lan)
    print('Quest2Wiimote: http://127.0.0.1:8765 | DSU 127.0.0.1:26760 | telemetry TCP 26761',flush=True)
    if not args.no_browser:
        webbrowser.open('http://127.0.0.1:8765')
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        stop_services(services)


if __name__ == '__main__':
    main()
