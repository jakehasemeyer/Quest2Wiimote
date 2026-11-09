import json
import math
from pathlib import Path
import socket
import struct
import sys
import threading
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'desktop'))
from motion import rotate
from screen import VirtualScreen, plane_orientation
from test_bridge import frame, hand
import server


class QuestScreenTests(unittest.TestCase):
    def test_plane_orientation_preserves_axes_including_half_turns(self):
        for axis,angle in [([1,0,0],.4),([0,1,0],math.pi),([0,0,1],math.pi),
                           ([1,0,0],math.pi),([-.6,.8,0],math.pi),([.6,0,-.8],2.7)]:
            q=[v*math.sin(angle/2) for v in axis]+[math.cos(angle/2)]
            right,up=rotate(q,[1,0,0]),rotate(q,[0,1,0])
            result=plane_orientation(right,up)
            for basis in ([1,0,0],[0,1,0],[0,0,1]):
                for actual,expected in zip(rotate(result,basis),rotate(q,basis)):
                    self.assertAlmostEqual(actual,expected)

    def test_packet_matches_all_four_desktop_corners_in_quest_space(self):
        screen=VirtualScreen();q=[0,math.sin(.6),0,math.cos(.6)]
        screen.data.update(calibrated=True,center=[.4,.7,-2],width=1.6,height=.9,
                           right=rotate(q,[1,0,0]),up=rotate(q,[0,1,0]))
        reference=dict(p=[3,2,1],q=[math.sin(.2),0,0,math.cos(.2)])
        values=struct.unpack('<4s11f',server.screen_packet(dict(screen=screen.data,reference=reference)))
        self.assertEqual(values[:2],(b'Q2SC',1.))
        for corner,(x,y) in zip(screen.corners(),[(-.8,.45),(.8,.45),(.8,-.45),(-.8,-.45)]):
            expected=[a+b for a,b in zip(reference['p'],rotate(reference['q'],corner))]
            actual=[a+b for a,b in zip(values[4:7],rotate(values[7:11],[x,y,0]))]
            for a,b in zip(actual,expected):self.assertAlmostEqual(a,b,places=6)
        screen.data.update(calibrated=False,display_mode='4:3')
        values=struct.unpack('<4s11f',server.screen_packet(dict(screen=screen.data,reference=reference)))
        self.assertEqual(values[1],0.);self.assertAlmostEqual(values[-1],4/3)

    def test_unlocking_and_moving_headset_keeps_world_screen_fixed(self):
        state=server.State();state.screen.data.update(calibrated=True,base_locked=False)
        state.update(frame(), 'quest','test');before=server.screen_packet(state.snapshot())
        moved=frame(t=2);moved['head'].update(p=[1,2,3],q=[0,1,0,0])
        state.update(moved,'quest','test')
        self.assertEqual(server.screen_packet(state.snapshot()),before)
        self.assertEqual(state.screen.data['base_pose']['p'],[1.,2.,3.])

    def test_feedback_interleaves_haptics_passthrough_and_changed_screen_only(self):
        previous=server.STATE;state=server.STATE=server.State()
        tcp=server.TCP(('127.0.0.1',0),server.Telemetry)
        threading.Thread(target=tcp.serve_forever,daemon=True).start()
        try:
            with socket.create_connection(tcp.server_address) as quest:
                quest.settimeout(1);reader=quest.makefile('rb')
                def send(t,**changes):
                    quest.sendall((json.dumps(frame(t=t,haptics=True,passthrough_control=True,screen_control=True,**changes))+'\n').encode())
                send(1)
                self.assertEqual(reader.read(12)[:4],b'Q2HB')
                self.assertEqual(reader.read(12)[:4],b'Q2PT')
                first=reader.read(48);self.assertEqual(first,server.screen_packet(state.snapshot()))
                send(2);send(3)
                # No unchanged 48-byte screen packet between successive motor packets.
                motors=reader.read(24)
                self.assertEqual(motors[:4],b'Q2HB');self.assertEqual(motors[12:16],b'Q2HB')
                with state.lock:state.screen.data.update(calibrated=True,display_mode='4:3')
                send(4)
                self.assertEqual(reader.read(12)[:4],b'Q2HB')
                changed=reader.read(48)
                self.assertNotEqual(first,changed);self.assertEqual(changed,server.screen_packet(state.snapshot()))
                reader.close()
        finally:
            tcp.shutdown();tcp.server_close();server.STATE=previous

    def test_cursor_matches_physical_screen_hit_and_roll_in_both_aspect_modes(self):
        state=server.State();state.screen.settings(2.,1.,2.);state.screen.calibrate_center(frame())
        for mode in ('4:3','16:9'):
            state.screen.set_display_mode(mode)
            for angle in (-math.pi/3,0,math.pi/3):
                q=[0,0,math.sin(angle/2),math.cos(angle/2)]
                state.update(frame(t=2+angle,hands=[hand(p=[.2,.1,0],q=q),hand()]),str(angle)+mode,'test')
                values=struct.unpack('<4s4f',server.cursor_packet(state.snapshot()))
                self.assertEqual(values[:2],(b'Q2CR',1.))
                self.assertAlmostEqual(values[2],.6);self.assertAlmostEqual(values[3],.4)
                self.assertAlmostEqual(values[4],angle/math.pi)
        for f in (frame(focused=False),frame(hands=[hand(flags=3),hand()]),frame(hands=[hand(p=[3,0,0]),hand()])):
            state.update(f,object(),'test')
            self.assertEqual(struct.unpack('<4s4f',server.cursor_packet(state.snapshot())),(b'Q2CR',0.,.5,.5,0.))

    def test_cursor_feedback_streams_with_motor_packets_and_hides_on_lost_focus(self):
        previous=server.STATE;state=server.STATE=server.State()
        state.screen.calibrate_center(frame())
        tcp=server.TCP(('127.0.0.1',0),server.Telemetry)
        threading.Thread(target=tcp.serve_forever,daemon=True).start()
        try:
            with socket.create_connection(tcp.server_address) as quest:
                quest.settimeout(1);reader=quest.makefile('rb')
                for t,focused in [(1,True),(2,True),(3,False)]:
                    quest.sendall((json.dumps(frame(t=t,haptics=True,cursor_control=True,focused=focused))+'\n').encode())
                    self.assertEqual(reader.read(12)[:4],b'Q2HB')
                    values=struct.unpack('<4s4f',reader.read(20))
                    self.assertEqual(values[:2],(b'Q2CR',float(focused)))
                    self.assertEqual(values[2:],(.5,.5,0.))
                reader.close()
        finally:
            tcp.shutdown();tcp.server_close();server.STATE=previous


if __name__=='__main__':unittest.main()
