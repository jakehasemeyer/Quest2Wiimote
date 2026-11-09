"""Loopback Dolphin rumble input; finite pulses travel over the paired Quest stream."""
import socket
import struct
import threading
import time


class Haptics:
    def __init__(self):
        self.lock = threading.Lock()
        self.motors = [(0., 0.), (0., 0.)]
        self.tests = [(0., 0.), (0., 0.)]
        self.received = 0

    def receive(self, data, now=None):
        if len(data) != 9 or data[:4] != b'Q2RH' or data[4] > 1:
            return False
        _, slot, low, high = struct.unpack('<4sBHH', data)
        now = time.monotonic() if now is None else now
        with self.lock:
            self.motors[slot] = (max(low, high)/65535, now+.35)
            self.received += 1
        return True

    def pulse(self, slot):
        if slot not in (0, 1):
            raise ValueError('Invalid controller')
        with self.lock:
            self.tests[slot] = (.6, time.monotonic()+.2)

    def values(self, state, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            return [max((amp for amp, expiry in (self.motors[i], self.tests[i]) if expiry > now), default=0.)
                    if state['live'] and state.get('haptics', False) and state['hands'][i]['active'] else 0.
                    for i in range(2)]

    def clear(self):
        with self.lock:
            self.motors = [(0., 0.), (0., 0.)]
            self.tests = [(0., 0.), (0., 0.)]


class Receiver:
    def __init__(self, haptics, port=26762):
        self.haptics = haptics
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('127.0.0.1', port))
        self.sock.settimeout(.1)
        self.running = True
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        while self.running:
            try:
                data, _ = self.sock.recvfrom(64)
                self.haptics.receive(data)
            except socket.timeout:
                pass
            except OSError:
                break

    def close(self):
        self.running = False
        self.sock.close()
        self.haptics.clear()
