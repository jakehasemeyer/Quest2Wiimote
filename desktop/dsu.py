"""Cemuhook DSU v1001 server. Two Touch controllers occupy slots 0 (right), 1 (left)."""
import secrets
import math
import socket
import struct
import threading
import time
import zlib

VERSION, PORTS, DATA = 0x100000, 0x100001, 0x100002
MACS = [bytes([2, 81, 50, 87, 0, i+1]) for i in range(2)]


def ir_points(pointer):
    # Dolphin 2609a's neutral Point projection, with one physical roll and no simulated Tilt.
    yaw=math.radians(12.5)*(2*pointer.get('dolphin_u',pointer['u'])-1)
    pitch=math.radians(10)*(1-2*pointer.get('dolphin_v',pointer['v']))
    roll=pointer.get('roll',0.)*math.pi
    distance=2-pointer.get('depth',0.)
    projection=1/math.tan(math.radians(31.5/2))
    points=[]
    for x in (-.1,.1):
        y=-distance*math.cos(pitch)-.1*math.sin(pitch)
        z=-distance*math.sin(pitch)+.1*math.cos(pitch)
        x,y=x*math.cos(yaw)-y*math.sin(yaw),x*math.sin(yaw)+y*math.cos(yaw)
        x,z=x*math.cos(roll)-z*math.sin(roll),x*math.sin(roll)+z*math.cos(roll)
        points.append([.5+x/y*projection*.375,.5-z/y*projection*.5])
    # Keep the pair visible at picture edges without changing its angle or separation.
    for axis in (0,1):
        shift=max(0.,-min(p[axis] for p in points))-max(0.,max(p[axis] for p in points)-1)
        for p in points:p[axis]+=shift
    return points


def packet(kind, payload=b'', uid=1, magic=b'DSUS'):
    data = bytearray(struct.pack('<4sHHIII', magic, 1001, len(payload)+4, 0, uid, kind) + payload)
    struct.pack_into('<I', data, 8, zlib.crc32(data))
    return bytes(data)


def parse(data):
    if len(data) < 20:
        return None
    magic, version, length, crc, uid, kind = struct.unpack_from('<4sHHIII', data)
    if magic != b'DSUC' or version > 1001 or length+16 != len(data):
        return None
    if zlib.crc32(data[:8]+b'\0'*4+data[12:]) != crc:
        return None
    return kind, data[20:]


def info(slot, connected):
    if slot >= 2:
        return bytes([slot])+bytes(10)
    return struct.pack('<BBBB6sB', slot, 2 if connected else 0, 2, 1, MACS[slot], 0)


def pad_data(slot, hand, motion, count, timestamp, connected=True, pointer=None):
    out = bytearray(80)
    out[:11] = info(slot, connected)
    out[11] = int(connected)
    struct.pack_into('<I', out, 12, count & 0xffffffff)
    if not connected:
        hand = {}
        motion = {'accel': [0., -1., 0.], 'gyro': [0., 0., 0.]}
    buttons = int(hand.get('buttons', 0))
    trigger, grip = hand.get('trigger', 0.), hand.get('squeeze', 0.)
    stick = hand.get('stick', [0., 0.])
    dpad = hand.get('dpad', stick)
    byte = lambda v: max(0, min(255, round(v)))
    # Native bits: primary, secondary, stick click, menu.
    out[16] = (2 if buttons & 4 else 0) | (8 if buttons & 8 else 0)
    if 'dpad' in hand:
        out[16] |= (0x80 if dpad[0]<0 else 0) | (0x20 if dpad[0]>0 else 0) | (0x10 if dpad[1]>0 else 0) | (0x40 if dpad[1]<0 else 0)
    out[17] = (0x20 if buttons & 1 else 0) | (0x40 if buttons & 2 else 0) | (8 if grip > .5 else 0) | (2 if trigger > .5 else 0)
    out[18] = int(bool(hand.get('nunchuk_connected')))
    out[20:24] = bytes([byte(128+stick[0]*127), byte(128+stick[1]*127), 128, 128])
    out[24:28] = bytes([byte(max(0,-dpad[0])*255), byte(max(0,-dpad[1])*255), byte(max(0,dpad[0])*255), byte(max(0,dpad[1])*255)])
    out[29] = 255 if buttons & 1 else 0  # Cross
    out[30] = 255 if buttons & 2 else 0  # Circle
    out[32] = byte(grip*255)             # R1
    out[34] = byte(trigger*255)          # R2
    if slot == 0 and pointer is not None and connected:
        out[19] = int(bool(pointer.get('valid')))
        if pointer.get('valid'):
            points=ir_points(pointer)
            # Spare pressure inputs carry the high byte; positive stick halves carry seven low bits.
            for high,low,value in zip((28,31,33,35),range(20,24),points[0]+points[1]):
                value=max(0,min(32767,round(value*32767)))
                out[high]=value>>7;out[low]=128+(value&127)
    struct.pack_into('<Q6f', out, 48, max(0, int(timestamp*1e6)), *motion['accel'], *motion['gyro'])
    return bytes(out)


class Server:
    def __init__(self, snapshot, port=26760):
        self.snapshot = snapshot
        self.uid = secrets.randbits(32)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('127.0.0.1', port))
        self.sock.settimeout(.004)
        self.subscriptions = {}
        self.running = True
        self.sent = 0
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        next_send = 0
        while self.running:
            try:
                raw, address = self.sock.recvfrom(512)
                request = parse(raw)
                if request:
                    kind, payload = request
                    state = self.snapshot()
                    if kind == VERSION:
                        self.sock.sendto(packet(kind, struct.pack('<H2x', 1001), self.uid), address)
                    elif kind == PORTS and len(payload) >= 4:
                        count = struct.unpack_from('<I', payload)[0]
                        if count <= 4 and len(payload) >= 4+count:
                            for slot in payload[4:4+count]:
                                if slot < 4:
                                    connected = state['live'] and slot < 2 and (state['hands'][slot].get('active', False) or slot==0 and any(state['hands'][0].get('dpad',[])))
                                    self.sock.sendto(packet(kind, info(slot, connected)+b'\0', self.uid), address)
                    elif kind == DATA and len(payload) == 8:
                        flags, slot = payload[:2]
                        slots = set(range(2)) if flags == 0 else set()
                        if flags & 1 and slot < 2:
                            slots.add(slot)
                        if flags & 2 and payload[2:] in MACS:
                            slots.add(MACS.index(payload[2:]))
                        for slot in slots:
                            self.subscriptions[(address, slot)] = time.monotonic()+5
            except socket.timeout:
                pass
            except OSError:
                if not self.running:
                    break
            now = time.monotonic()
            if now < next_send:
                continue
            next_send = now+1/100
            state = self.snapshot()
            for key, expiry in list(self.subscriptions.items()):
                if expiry < now:
                    del self.subscriptions[key]
                    continue
                address, slot = key
                hand = state['hands'][slot]
                if state.get('calibrating'):
                    hand={**hand,'buttons':0,'trigger':0.,'squeeze':0.,'stick':[0.,0.],'dpad':[0,0]}
                if slot==0:hand={**hand,'nunchuk_connected':state['live'] and state['hands'][1].get('active',False)}
                connected = state['live'] and (hand.get('active', False) or slot==0 and any(hand.get('dpad',[])))
                payload = pad_data(slot, hand, state['motion'][slot], state['seq'], state['t'], connected, state.get('pointer'))
                try:
                    self.sock.sendto(packet(DATA, payload, self.uid), address)
                    self.sent += 1
                except OSError:
                    pass

    def close(self):
        self.running = False
        self.sock.close()
