import copy
import json
import math
from pathlib import Path
import socket
import struct
import sys
import time
import unittest
import zlib
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'desktop'))
import dsu
from motion import Motion, rotate, inverse_rotate, G
from server import State, TOKEN
import server as desktop


def hand(**changes):
    result = dict(p=[0,0,0],q=[0,0,0,1],v=[0,0,0],w=[0,0,0],flags=15,lv=True,av=True,active=True,buttons=0,trigger=0.,squeeze=0.,stick=[0.,0.])
    result.update(changes)
    return result


def frame(t=1., **changes):
    result = dict(version=1,token=TOKEN,seq=1,t=t,focused=True,head=hand(),hands=[hand(),hand()])
    result.update(changes)
    return result


def decode_ir(payload):
    values=[(payload[high]*128+payload[low]-128)/32767 for high,low in zip((28,31,33,35),range(20,24))]
    return [values[:2],values[2:]]


class MotionTests(unittest.TestCase):
    def test_stationary_gravity_and_units(self):
        motion = Motion().update(hand(w=[math.pi,math.pi/2,-math.pi/4]),1.)
        self.assertEqual(motion['accel'],[0.,-1.,0.])
        for a,b in zip(motion['gyro'],[180.,-90.,45.]):
            self.assertAlmostEqual(a,b)

    def test_gravity_rotates_with_controller(self):
        q=[0,0,math.sin(math.pi/4),math.cos(math.pi/4)]
        result=Motion().update(hand(q=q),1.)
        self.assertAlmostEqual(result['accel'][0],-1.)
        self.assertAlmostEqual(result['accel'][1],0.)
        self.assertAlmostEqual(sum(x*x for x in result['accel']),1.)

    def test_angular_velocity_converted_from_world_to_body(self):
        q=[0,math.sin(math.pi/4),0,math.cos(math.pi/4)]
        world=rotate(q,[math.pi/2,0,0])
        result=Motion().update(hand(q=q,w=world),1.)
        self.assertAlmostEqual(result['gyro'][0],90.)
        self.assertAlmostEqual(result['gyro'][1],0.)
        self.assertAlmostEqual(result['gyro'][2],0.)

    def test_linear_acceleration_derivative_is_filtered(self):
        m=Motion();m.update(hand(),1.)
        for n in range(1,101):
            result=m.update(hand(v=[G*n*.01,0,0]),1+n*.01)
        self.assertAlmostEqual(result['accel'][0],-1.,places=5)
        self.assertEqual(result['accel'][1],-1.)

    def test_tracking_loss_and_reacquisition_do_not_spike(self):
        m=Motion();m.update(hand(),1.)
        result=m.update(hand(flags=3,v=[100,0,0],w=[10,10,10]),1.01)
        self.assertEqual(result['gyro'],[0.,0.,0.])
        self.assertFalse(result['tracked'])
        result=m.update(hand(v=[100,0,0]),1.02)
        self.assertEqual(result['accel'],[0.,-1.,0.])

    def test_velocity_gap_resets_derivative(self):
        m=Motion();m.update(hand(),1.)
        self.assertEqual(m.update(hand(v=[100,0,0]),2.)['accel'],[0.,-1.,0.])


class StateTests(unittest.TestCase):
    def test_equal_arrival_clock_ticks_do_not_crash_snapshot(self):
        state=State();state.update(frame(),'test','local')
        state.arrivals.extend([state.last,state.last])
        self.assertTrue(math.isfinite(state.snapshot()['hz']))

    def test_stale_inputs_are_neutral(self):
        state=State();state.update(frame(hands=[hand(buttons=1,trigger=1),hand()]),'test','127.0.0.1')
        self.assertTrue(state.snapshot()['live'])
        state.last-=1
        result=state.snapshot()
        self.assertFalse(result['live'])
        self.assertEqual(result['hands'][0]['buttons'],0)
        self.assertEqual(result['hands'][0]['trigger'],0.)
        self.assertEqual(result['motion'][0]['gyro'],[0.,0.,0.])

    def test_focus_loss_clears_inputs(self):
        state=State();state.update(frame(focused=False,hands=[hand(buttons=1),hand()]),'test','local')
        self.assertFalse(state.snapshot()['live'])
        self.assertEqual(state.snapshot()['hands'][0]['buttons'],0)

    def test_unpaired_and_nonfinite_frames_rejected(self):
        state=State()
        with self.assertRaises(ValueError):state.update(frame(token='wrong'),'test','local')
        f=frame();f['hands'][0]['v'][0]=float('nan')
        with self.assertRaises(ValueError):state.update(f,'test','local')

    def test_reconnect_resets_derivative_and_clock(self):
        state=State();state.update(frame(100.),'first','local')
        state.update(frame(1.,hands=[hand(v=[100,0,0]),hand()]),'second','local')
        self.assertEqual(state.snapshot()['t'],1.)
        self.assertEqual(state.motion[0]['accel'],[0.,-1.,0.])


class DSUTests(unittest.TestCase):
    def setUp(self):
        self.state=State();self.state.update(frame(),'test','local')
        self.server=dsu.Server(self.state.snapshot,port=0)
        self.client=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);self.client.settimeout(1)
        self.address=self.server.sock.getsockname()

    def tearDown(self):
        self.client.close();self.server.close()

    def send(self,kind,payload=b''):
        self.client.sendto(dsu.packet(kind,payload,123,b'DSUC'),self.address)

    def receive(self):
        data=self.client.recv(512)
        self.assertEqual(data[:4],b'DSUS')
        self.assertEqual(struct.unpack_from('<H',data,6)[0],len(data)-16)
        self.assertEqual(struct.unpack_from('<I',data,8)[0],zlib.crc32(data[:8]+bytes(4)+data[12:]))
        return data

    def test_version_and_four_slot_discovery(self):
        self.send(dsu.VERSION)
        self.assertEqual(struct.unpack_from('<H',self.receive(),20)[0],1001)
        self.send(dsu.PORTS,struct.pack('<I4B',4,0,1,2,3))
        responses=[self.receive() for _ in range(4)]
        self.assertEqual([r[20] for r in responses],[0,1,2,3])
        self.assertEqual([r[21] for r in responses],[2,2,0,0])

    def test_stream_subscription_and_stale_disconnect(self):
        self.send(dsu.DATA,bytes([1,0])+bytes(6))
        data=self.receive();self.assertEqual(len(data),100)
        self.assertEqual(data[20],0);self.assertEqual(data[31],1)
        self.assertEqual(struct.unpack_from('<3f',data,76),(0.,-1.,0.))
        self.state.last-=1
        for _ in range(50):
            data=self.receive()
            if data[31]==0:break
        self.assertEqual(data[31],0)
        self.assertEqual(data[40:44],bytes([128]*4))

    def test_mac_and_all_slot_subscriptions(self):
        self.send(dsu.DATA,bytes([2,0])+dsu.MACS[1])
        self.assertEqual(self.receive()[20],1)
        self.send(dsu.DATA,bytes(8))
        seen={self.receive()[20] for _ in range(5)}
        self.assertEqual(seen,{0,1})

    def test_payload_layout_buttons_sticks_motion(self):
        h=hand(buttons=15,trigger=.75,squeeze=1.,stick=[1.,-1.])
        payload=dsu.pad_data(0,h,{'accel':[1,2,3],'gyro':[4,5,6]},123,2.)
        self.assertEqual(len(payload),80)
        self.assertEqual(payload[20:24],bytes([255,1,128,128]))
        self.assertEqual(payload[29:31],bytes([255,255]))
        self.assertEqual(payload[32],255);self.assertEqual(payload[34],191)
        self.assertEqual(struct.unpack_from('<Q6f',payload,48),(2000000,1,2,3,4,5,6))


class TelemetryIntegrationTests(unittest.TestCase):
    def test_tcp_sender_to_udp_motion_and_disconnect(self):
        previous = desktop.STATE
        desktop.STATE = State()
        desktop.STATE.screen.calibrate_center(frame())
        tcp = desktop.TCP(('127.0.0.1',0),desktop.Telemetry)
        output = dsu.Server(desktop.STATE.snapshot,port=0)
        threading.Thread(target=tcp.serve_forever,daemon=True).start()
        client = socket.socket(socket.AF_INET,socket.SOCK_DGRAM);client.settimeout(1)
        try:
            with socket.create_connection(tcp.server_address) as sender:
                f=frame(t=42.,hands=[hand(w=[math.pi/2,0,0],buttons=1),hand()])
                sender.sendall((json.dumps(f)+'\n').encode())
                deadline=time.monotonic()+1
                while not desktop.STATE.snapshot()['live'] and time.monotonic()<deadline:
                    time.sleep(.005)
                client.sendto(dsu.packet(dsu.DATA,bytes([1,0])+bytes(6),99,b'DSUC'),output.sock.getsockname())
                data=client.recv(512)
                self.assertEqual(len(data),100)
                self.assertEqual(data[31],1)
                self.assertEqual(data[39],1)  # Calibrated virtual IR visibility.
                for actual,expected in zip(decode_ir(data[20:]),dsu.ir_points(dict(u=.5,v=.5))):
                    for a,b in zip(actual,expected):self.assertLessEqual(abs(a-b),.5/32767)
                self.assertEqual(data[49],255)  # Cross/A analog byte.
                self.assertAlmostEqual(struct.unpack_from('<f',data,88)[0],90.)
                self.assertEqual(struct.unpack_from('<Q',data,68)[0],42000000)
            time.sleep(.35)
            self.assertFalse(desktop.STATE.snapshot()['live'])
            self.assertEqual(desktop.STATE.snapshot()['hands'][0]['buttons'],0)
        finally:
            client.close();output.close();tcp.shutdown();tcp.server_close();desktop.STATE=previous


if __name__=='__main__':
    unittest.main()
