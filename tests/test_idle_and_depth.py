import math
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'desktop'))
from dsu import Server,packet,DATA,pad_data,ir_points
from profiles import profile
from screen import VirtualScreen
from server import State
from test_bridge import frame,hand,decode_ir


class IdleTests(unittest.TestCase):
    def setUp(self):
        self.state=State()

    def update(self,now,**left):
        with patch('server.time.monotonic',return_value=now):
            self.state.update(frame(t=now,hands=[hand(),hand(**left)]),'session','local')
            return self.state.snapshot()

    def test_idle_is_latched_until_a_new_button_press(self):
        self.assertTrue(self.update(100)['hands'][1]['active'])
        self.assertTrue(self.update(114.9)['hands'][1]['active'])
        idle=self.update(115,p=[.003,0,0])
        self.assertTrue(idle['left_idle']);self.assertFalse(idle['hands'][1]['active'])
        self.assertTrue(idle['hands'][0]['active'])
        self.assertFalse(self.update(116,p=[1,0,0])['hands'][1]['active'])
        self.assertFalse(self.update(117,stick=[1,1])['hands'][1]['active'])
        wake=self.update(118,buttons=1)
        self.assertFalse(wake['left_idle']);self.assertTrue(wake['hands'][1]['active'])
        self.assertEqual(wake['hands'][1]['buttons'],0)
        self.update(119)
        self.assertEqual(self.update(120,buttons=1)['hands'][1]['buttons'],1)

    def test_real_motion_resets_timer_but_held_button_does_not_wake_after_timeout(self):
        self.update(100,buttons=1)
        self.update(114,p=[.03,0,0],buttons=1)
        self.assertFalse(self.update(128,p=[.03,0,0],buttons=1)['left_idle'])
        self.assertTrue(self.update(129,p=[.03,0,0],buttons=1)['left_idle'])
        self.assertTrue(self.update(130,buttons=1)['left_idle'])
        self.update(131)
        self.assertFalse(self.update(132,trigger=1)['left_idle'])

    def test_rotation_counts_as_motion_and_quaternion_sign_does_not(self):
        self.update(100)
        q=[0,math.sin(math.radians(3)),0,math.cos(math.radians(3))]
        self.update(114,q=q)
        self.assertFalse(self.update(128,q=[-x for x in q])['left_idle'])
        self.assertTrue(self.update(129,q=q)['left_idle'])

    def test_tracking_loss_does_not_prevent_idle_latching(self):
        self.update(100)
        self.assertTrue(self.update(115,flags=3)['left_idle'])

    def test_real_dsu_marks_left_disconnected_and_detaches_nunchuk_on_right(self):
        self.update(100);self.update(115)
        server=Server(self.state.snapshot,port=0)
        try:
            with patch('server.time.monotonic',return_value=115),socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as client:
                client.settimeout(1);client.sendto(packet(DATA,bytes(8),99,b'DSUC'),server.sock.getsockname())
                packets={}
                while len(packets)<2:
                    data=client.recv(512);packets[data[20]]=data
                self.assertEqual(packets[0][21],2)
                self.assertEqual(packets[0][38],0)
                self.assertEqual(packets[1][21],0)
                self.assertEqual(packets[1][36:40],bytes(4))
        finally:server.close()


class DepthTests(unittest.TestCase):
    def setUp(self):
        self.screen=VirtualScreen();self.screen.settings(16/9,1,2);self.screen.calibrate_center(frame())
        self.screen.pointer(frame())

    def test_forward_backward_change_depth_without_moving_center_aim(self):
        for z,expected in [(-.4,.4),(.4,-.4),(-1.8,.9),(3,-.9)]:
            pointer=self.screen.pointer(frame(hands=[hand(p=[0,0,z]),hand()]))
            self.assertTrue(pointer['valid'])
            self.assertAlmostEqual(pointer['dolphin_u'],.5);self.assertAlmostEqual(pointer['dolphin_v'],.5)
            self.assertAlmostEqual(pointer['depth'],expected)

    def test_roll_uses_absolute_controller_orientation(self):
        for angle in (-90,0,90,180):
            radians=math.radians(angle)
            pointer=self.screen.pointer(frame(hands=[hand(q=[0,0,math.sin(radians/2),math.cos(radians/2)]),hand()]))
            self.assertAlmostEqual(pointer['roll'],angle/180)
            self.assertAlmostEqual(pointer['dolphin_u'],.5)

    def test_dsu_depth_roll_and_attachment_do_not_replace_game_buttons(self):
        pointer=dict(valid=True,u=.5,v=.5,depth=.4,roll=-.5)
        payload=pad_data(0,hand(buttons=3,trigger=.7,squeeze=.8,nunchuk_connected=True),dict(accel=[1,2,3],gyro=[4,5,6]),1,2.,pointer=pointer)
        self.assertEqual(payload[18],1)
        for actual,expected in zip(decode_ir(payload),ir_points(pointer)):
            for a,b in zip(actual,expected):self.assertLessEqual(abs(a-b),.5/32767)
        self.assertEqual(payload[29:31],bytes([255,255]))
        self.assertEqual(payload[32],204);self.assertEqual(payload[34],178)
        hidden=pad_data(0,hand(),dict(accel=[0,-1,0],gyro=[0,0,0]),1,2.,pointer=dict(pointer,valid=False))
        self.assertEqual((hidden[28],hidden[31],hidden[33],hidden[35]),(0,0,0,0))

    def test_profile_has_dynamic_attachment_and_preserves_ten_cm_offset(self):
        config=profile(pointer=True)
        self.assertIn('Extension = `PS`',config)
        self.assertIn('IR/Vertical Offset = 10',config)
        self.assertIn('IRPassthrough/Enabled = True',config)
        self.assertIn('IRPassthrough/Object 1 X = (`Square` * 32640 + `Left X+` * 127) / 32767',config)
        self.assertIn('IRPassthrough/Object 2 Y = (`L2` * 32640 + `Right Y+` * 127) / 32767',config)
        self.assertIn('IR/Hide = 1',config)
        self.assertNotIn('Swing/',config);self.assertNotIn('Tilt/',config)

    def test_ir_roll_matches_physical_roll_without_transforming_motion_twice(self):
        for angle in (-179,-120,-90,-60,-31,-30,0,30,31,60,90,120,179):
            radians=math.radians(angle)
            pointer=self.screen.pointer(frame(hands=[hand(q=[0,0,math.sin(radians/2),math.cos(radians/2)]),hand()]))
            self.assertTrue(pointer['valid'])
            points=ir_points(pointer)
            # Undo the pixel aspect ratio: IR pixels have equal scale on both axes.
            dx=(points[1][0]-points[0][0])*1024
            dy=(points[1][1]-points[0][1])*768
            self.assertAlmostEqual(math.atan2(dy,-dx),radians,places=6)
            payload=pad_data(0,hand(),dict(accel=[1,2,3],gyro=[4,5,6]),1,2.,pointer=pointer)
            self.assertEqual(payload[19],1)
            import struct
            self.assertEqual(struct.unpack_from('<6f',payload,56),(1,2,3,4,5,6))

    def test_ir_pair_stays_visible_at_edges_for_full_roll_and_depth_range(self):
        for u in (0.,.5,1.):
            for v in (0.,.5,1.):
                for depth in (-.9,0.,.9):
                    for degrees in range(-180,181,5):
                        points=ir_points(dict(u=u,v=v,roll=degrees/180,depth=depth))
                        self.assertTrue(all(-1e-9<=value<=1+1e-9 for p in points for value in p))
                        self.assertGreater(math.dist(points[0],points[1]),.02)

    def test_ir_neutral_offset_and_depth_preserve_dolphin_projection(self):
        neutral=ir_points(dict(u=.5,v=.5))
        projection=1/math.tan(math.radians(15.75))
        self.assertAlmostEqual(neutral[0][0],.5+.1/2*projection*.375)
        self.assertAlmostEqual(neutral[0][1],.5+.1/2*projection*.5)
        near=ir_points(dict(u=.5,v=.5,depth=.5))
        far=ir_points(dict(u=.5,v=.5,depth=-.5))
        self.assertGreater(math.dist(*near),math.dist(*neutral))
        self.assertLess(math.dist(*far),math.dist(*neutral))

    def test_ir_axes_match_dolphins_point_camera_projection(self):
        center=ir_points(dict(u=.5,v=.5))
        left=ir_points(dict(u=.25,v=.5));right=ir_points(dict(u=.75,v=.5))
        up=ir_points(dict(u=.5,v=.25));down=ir_points(dict(u=.5,v=.75))
        for index in (0,1):
            self.assertLess(right[index][0],center[index][0]);self.assertGreater(left[index][0],center[index][0])
            self.assertLess(up[index][1],center[index][1]);self.assertGreater(down[index][1],center[index][1])

    def test_subpixel_ir_precision_and_high_byte_boundary_continuity(self):
        motion=dict(accel=[0,-1,0],gyro=[0,0,0]);previous=None
        for n in range(1001):
            pointer=dict(valid=True,u=.45+n/10000,v=.5)
            payload=pad_data(0,hand(stick=[.7,-.6]),motion,n,2.,pointer=pointer)
            actual=decode_ir(payload);expected=ir_points(pointer)
            for a,b in zip(actual,expected):
                self.assertLess(abs(a[0]-b[0])*1023,.016)
                self.assertLess(abs(a[1]-b[1])*767,.012)
            if previous:
                self.assertLessEqual(actual[0][0],previous)
                self.assertLess(previous-actual[0][0],.001)
            previous=actual[0][0]
            self.assertEqual(payload[24:28],bytes([0,153,178,0]))


if __name__=='__main__':unittest.main()
