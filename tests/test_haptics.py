import ctypes
import json
from pathlib import Path
import socket
import struct
import sys
import threading
import time
import unittest
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'desktop'))
from haptics import Haptics, Receiver
from profiles import profile
from paths import DOLPHIN
import profiles
from test_bridge import frame, hand
import server


def output(slot, low=0, high=0):
    return struct.pack('<4sBHH', b'Q2RH', slot, low, high)


class HapticTests(unittest.TestCase):
    def setUp(self):
        self.haptics = Haptics()
        self.state = dict(live=True, haptics=True, hands=[hand(), hand()])

    def test_motor_mixing_routing_expiry_and_stop(self):
        self.haptics.receive(output(0, 32768, 0), 10.)
        self.haptics.receive(output(1, 0, 65535), 10.)
        values = self.haptics.values(self.state, 10.1)
        self.assertAlmostEqual(values[0], .5, places=4)
        self.assertEqual(values[1], 1.)
        self.haptics.receive(output(0), 10.2)
        self.assertEqual(self.haptics.values(self.state, 10.2), [0., 1.])
        self.assertEqual(self.haptics.values(self.state, 10.36), [0., 0.])

    def test_focus_controller_and_sender_capability_gates(self):
        self.haptics.receive(output(0, 65535), 10.)
        for key in ('live', 'haptics'):
            self.state[key] = False
            self.assertEqual(self.haptics.values(self.state, 10.1), [0., 0.])
            self.state[key] = True
        self.state['hands'][0]['active'] = False
        self.assertEqual(self.haptics.values(self.state, 10.1), [0., 0.])

    def test_bad_packets_ignored_and_clear_stops_tests(self):
        for data in (b'', output(0)+b'0', output(2), b'BAD!'+output(0)[4:]):
            self.assertFalse(self.haptics.receive(data))
        self.haptics.pulse(1)
        self.assertEqual(self.haptics.values(self.state), [0., .6])
        self.haptics.clear()
        self.assertEqual(self.haptics.values(self.state), [0., 0.])

    def test_each_profile_routes_to_its_own_output(self):
        for slot in (0, 1):
            self.assertIn(f'XInput/{slot+2}/Gamepad:Motor L', profile(slot, False))
        self.assertIn('XInput/2/Gamepad:Motor R', profile(pointer=True))

    def test_udp_output_returns_over_paired_tcp_and_clears_on_disconnect(self):
        previous = server.STATE
        state = server.STATE = server.State()
        tcp = server.TCP(('127.0.0.1', 0), server.Telemetry)
        receiver = Receiver(state.haptics, port=0)
        threading.Thread(target=tcp.serve_forever, daemon=True).start()
        try:
            with socket.create_connection(tcp.server_address) as quest, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as dolphin:
                quest.settimeout(1)
                quest.sendall((json.dumps(frame(haptics=True))+'\n').encode())
                reader = quest.makefile('rb')
                self.assertEqual(struct.unpack('<4sff', reader.read(12)), (b'Q2HB', 0., 0.))
                dolphin.sendto(output(1, 0, 65535), receiver.sock.getsockname())
                deadline = time.monotonic()+1
                while not state.haptics.received and time.monotonic() < deadline: time.sleep(.002)
                quest.sendall((json.dumps(frame(t=1.1, haptics=True))+'\n').encode())
                self.assertEqual(struct.unpack('<4sff', reader.read(12)), (b'Q2HB', 0., 1.))
                quest.sendall((json.dumps(frame(t=1.2, haptics=True, focused=False))+'\n').encode())
                self.assertEqual(struct.unpack('<4sff', reader.read(12)), (b'Q2HB', 0., 0.))
                reader.close()
            deadline = time.monotonic()+1
            while state.haptics.motors[1][0] and time.monotonic() < deadline: time.sleep(.002)
            self.assertEqual(state.haptics.motors, [(0., 0.), (0., 0.)])
        finally:
            receiver.close(); tcp.shutdown(); tcp.server_close(); server.STATE = previous

    @unittest.skipUnless(sys.platform == 'win32', 'Windows adapter')
    def test_compiled_xinput_adapter_sends_real_udp_and_exports_ordinal(self):
        dll = DOLPHIN/'xinput1_4.dll'
        if not dll.exists(): self.skipTest('Build tools/build_rumble.py first')
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receive:
            try: receive.bind(('127.0.0.1', 26762))
            except OSError: self.skipTest('Close running desktop app for adapter test')
            receive.settimeout(1)
            adapter = ctypes.WinDLL(str(dll))
            value = (ctypes.c_ushort*2)(100, 60000)
            self.assertEqual(adapter.XInputSetState(2, value), 0)
            self.assertEqual(receive.recv(64), output(0, 100, 60000))
            self.assertEqual(adapter.XInputSetState(3, value), 0)
            self.assertEqual(receive.recv(64), output(1, 100, 60000))
            state = (ctypes.c_byte*16)()
            get_state = adapter[100]
            self.assertEqual(get_state(2, state), 0)
            adapter.XInputEnable(False)
            self.assertEqual({receive.recv(64), receive.recv(64)}, {output(0), output(1)})
            adapter.XInputEnable(True)

    def test_configuration_keeps_other_remotes_and_servers_and_backs_up_originals(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); user=root/'existing-user'; config=user/'Config';config.mkdir(parents=True)
            original='[Wiimote1]\nDevice = old\n[Wiimote2]\nSource = 2\nDevice = keep me\n'
            (config/'WiimoteNew.ini').write_text(original)
            (config/'DSUClient.ini').write_text('[Server]\nEntries = Phone:192.0.2.1:26760;old-name:127.0.0.1:26760;\n')
            with patch.object(profiles,'ROOT',root),patch.object(profiles,'DOLPHIN_USER',user):
                profiles.configure_dolphin()
            result=(config/'WiimoteNew.ini').read_text()
            self.assertIn('Device = keep me',result)
            self.assertIn('Rumble/Motor = `XInput/2/Gamepad:Motor L`',result)
            dsu=(config/'DSUClient.ini').read_text()
            self.assertIn('Phone:192.0.2.1:26760;',dsu)
            self.assertIn('Quest2Wiimote:127.0.0.1:26760;',dsu)
            self.assertNotIn('old-name:',dsu)
            backup=next((root/'data/dolphin-config-backups').glob('*/WiimoteNew.ini'))
            self.assertEqual(backup.read_text(),original)

    def test_reapplying_quest_configuration_keeps_user_button_stick_and_hotkey_bindings(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);user=root/'user';config=user/'Config';config.mkdir(parents=True)
            (config/'WiimoteNew.ini').write_text('[Wiimote1]\nDevice = DSUClient/0/Quest2Wiimote\n'
                'Buttons/A = Circle\nButtons/1 = Cross\nButtons/2 = `R1`\n'
                'D-Pad/Up = custom up\nHotkeys/Sideways Toggle = custom toggle\n'
                'Nunchuk/Buttons/C = custom C\nNunchuk/Stick/Up = custom stick\n'
                'IRPassthrough/Object 1 X = obsolete transport\n')
            with patch.object(profiles,'ROOT',root),patch.object(profiles,'DOLPHIN_USER',user):
                profiles.configure_dolphin()
            result=(config/'WiimoteNew.ini').read_text()
            for binding in ('Buttons/A = Circle','Buttons/1 = Cross','Buttons/2 = `R1`',
                            'D-Pad/Up = custom up','Hotkeys/Sideways Toggle = custom toggle',
                            'Nunchuk/Buttons/C = custom C','Nunchuk/Stick/Up = custom stick'):
                self.assertIn(binding,result)
            self.assertNotIn('obsolete transport',result)
            self.assertIn('IR/Vertical Offset = 10',result)


if __name__ == '__main__': unittest.main()
