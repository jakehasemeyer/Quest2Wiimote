import json
import math
from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'desktop'))
import dsu
import server
from hand_tracking import Gestures
from motion import rotate
from screen import pointing_pose
from test_bridge import hand, frame


def tracked(gesture='open', offset=(0,0,0)):
    joints=[[0.,0.,0.] for _ in range(26)]
    joints[1]=[0.,-.04,0.]
    joints[3]=[-.05,0.,0.];joints[5]=[-.09,.02,0.]
    for i,(base,pip,tip) in enumerate(((7,8,10),(12,13,15),(17,18,20),(22,23,25))):
        x=-.03+i*.02
        joints[base]=[x,.025,0.];joints[pip]=[x,.06,0.]
        joints[tip]=[x,.01,.01] if gesture in ('fist','thumbs_up') else [x,.10,0.]
    if gesture=='fist':joints[5]=[-.03,.025,-.01]
    elif gesture=='thumbs_up':joints[5]=[-.05,.08,0.]
    elif isinstance(gesture,int):joints[5]=joints[(10,15,20,25)[gesture]][:]
    return hand(joints=[[a+b for a,b in zip(p,offset)] for p in joints])


class GestureTests(unittest.TestCase):
    def test_every_pinch_maps_through_dsu_to_existing_profile(self):
        for slot, outputs in ((0,((30,255),(34,255),(29,255),(32,255))),(1,((32,255),(34,255)))):
            for finger,(byte,value) in enumerate(outputs):
                with self.subTest(slot=slot,finger=finger):
                    h=tracked(finger);Gestures().update(h,slot,hand())
                    packet=dsu.pad_data(slot,h,server.NEUTRAL,1,1.)
                    self.assertEqual(packet[byte],value)
                    self.assertEqual(sum(packet[i] for i in (29,30,32,34)),255)
                    self.assertEqual(h['dpad'],[0,0])

    def test_pinch_hysteresis_and_release(self):
        g=Gestures();h=tracked(0);g.update(h,0,hand())
        h['joints'][5][2]=.023;g.update(h,0,hand());self.assertEqual(h['buttons'],2)
        h['joints'][5][2]=.03;g.update(h,0,hand());self.assertEqual(h['buttons'],0)
        h['joints'][5][2]=.023;g.update(h,0,hand());self.assertEqual(h['buttons'],0)

    def test_thumbs_up_is_plus_only_on_right(self):
        for slot in (0,1):
            h=tracked('thumbs_up');Gestures().update(h,slot,hand())
            self.assertEqual(h['buttons'],4 if slot==0 else 0)
            self.assertEqual(h['trigger']+h['squeeze'],0)
            self.assertEqual(h['dpad'],[0,0])
        h=tracked('thumbs_up')
        h['joints']=[rotate([0,0,1,0],p) for p in h['joints']]
        Gestures().update(h,0,hand());self.assertEqual(h['buttons'],0)

    def test_fist_anchor_deadzone_diagonals_and_reset(self):
        g=Gestures()
        for offset,expected in (((0,0,0),(0,0)),((.03,0,0),(0,0)),((.06,.06,0),(1,1)),
                                ((.03,.03,0),(1,1)),((.02,.02,0),(0,0)),((-.06,-.06,0),(-1,-1))):
            h=tracked('fist',offset);g.update(h,0,hand())
            self.assertEqual(h['dpad'],list(expected))
            self.assertEqual(h['buttons']+h['trigger']+h['squeeze'],0)
        g.update(tracked(),0,hand())
        h=tracked('fist',(.5,.5,0));g.update(h,0,hand());self.assertEqual(h['dpad'],[0,0])

    def test_fist_axes_follow_head_heading_at_closure(self):
        g=Gestures();head=hand(q=[0,math.sin(math.pi/4),0,math.cos(math.pi/4)])
        g.update(tracked('fist'),0,head)
        h=tracked('fist',(0,0,-.06));g.update(h,0,hand());self.assertEqual(h['dpad'],[1,0])

    def test_tracking_loss_releases_and_reanchors(self):
        g=Gestures();g.update(tracked('fist'),0,hand())
        h=tracked('fist',(.1,0,0));h['flags']=3;g.update(h,0,hand())
        self.assertFalse(h['active']);self.assertEqual(h['dpad'],[0,0])
        h=tracked('fist',(.1,0,0));g.update(h,0,hand());self.assertEqual(h['dpad'],[0,0])


class HandTrackingStateTests(unittest.TestCase):
    def setUp(self):
        self.state=server.State();self.state.hand_tracking_requested=True

    def update(self,t=1.,**changes):
        f=frame(t=t,hand_tracking=True,hands=[tracked(),tracked()]);f.update(changes)
        self.state.update(f,'test','local')
        return self.state.snapshot()

    def test_left_fist_joins_right_dpad_without_nunchuk_stick(self):
        self.update(hands=[tracked('fist'),tracked('fist')])
        s=self.update(1.01,hands=[tracked('fist',(0,.06,0)),tracked('fist',(-.06,0,0))])
        self.assertEqual(s['hands'][0]['dpad'],[-1,1])
        self.assertEqual(s['hands'][1]['stick'],[0,0])
        p=dsu.pad_data(0,s['hands'][0],server.NEUTRAL,1,1.,pointer={'valid':False})
        self.assertEqual(p[24:28],bytes([255,0,0,255]));self.assertEqual(p[16]&0xf0,0x90)
        self.state.last-=1
        self.assertEqual(self.state.snapshot()['hands'][0]['dpad'],[0,0])

    def test_focus_loss_mode_switch_and_reconnect_clear_inputs(self):
        self.assertEqual(self.update(hands=[tracked(0),tracked(0)])['hands'][0]['buttons'],2)
        self.assertEqual(self.update(1.01,focused=False,hands=[tracked(0),tracked(0)])['hands'][0]['buttons'],0)
        self.assertFalse(self.update(1.02,hand_tracking=False)['hands'][0]['active'])
        self.update(1.03,hands=[tracked('fist'),tracked()])
        self.state.last-=1
        self.assertEqual(self.update(1.5,hands=[tracked('fist',(.5,0,0)),tracked()])['hands'][0]['dpad'],[0,0])

    def test_hand_mode_does_not_idle_left_hand(self):
        self.state.left_idle=True;self.state.left_wake_consumed=True
        s=self.update(hands=[tracked(),tracked(0)])
        self.assertFalse(s['left_idle']);self.assertEqual(s['hands'][1]['squeeze'],1)

    def test_bad_joint_payload_is_rejected(self):
        for joints in ([[0,0,0]],[[0,0,float('nan')]]*26,None):
            with self.assertRaises(ValueError):self.update(hands=[hand(joints=joints),tracked()])

    def test_hand_aim_ignores_controller_handle_offset(self):
        f=frame(hand_tracking=True);self.assertIs(pointing_pose(f,'handle'),f['hands'][0])

    def test_saved_setting_preserves_other_preferences_and_neutralizes_switch(self):
        self.update(hands=[tracked(0),tracked()])
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'desktop.json';path.write_text('{"passthrough": true}')
            with patch.object(server,'SETTINGS',path):
                self.state.set_hand_tracking(False)
                self.assertEqual(json.loads(path.read_text()),dict(passthrough=True,hand_tracking=False))
                self.assertEqual(self.state.snapshot()['hands'][0]['buttons'],0)
                with self.assertRaises(ValueError):self.state.set_hand_tracking('false')

    def test_toggle_feedback_over_existing_connection(self):
        with patch.object(server,'STATE',self.state):
            tcp=server.TCP(('127.0.0.1',0),server.Telemetry)
            threading.Thread(target=tcp.serve_forever,daemon=True).start()
            try:
                with socket.create_connection(tcp.server_address) as quest:
                    quest.settimeout(1)
                    with quest.makefile('rb') as reader:
                        for t,enabled in ((1.,True),(1.1,False),(1.2,True)):
                            self.state.hand_tracking_requested=enabled
                            quest.sendall((json.dumps(frame(t=t,hand_tracking_control=True))+'\n').encode())
                            self.assertEqual(struct.unpack('<4sff',reader.read(12)),(b'Q2HT',float(enabled),0.))
            finally:tcp.shutdown();tcp.server_close()


if __name__=='__main__':unittest.main()
