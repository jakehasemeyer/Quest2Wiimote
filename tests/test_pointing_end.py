import copy
import json
import math
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'desktop'))
from screen import VirtualScreen,HANDLE_IN_GRIP,relative_pose,relative_ray,pointing_pose,sub
from motion import rotate,multiply
from controller_models import ASSETS,WII_IN_GRIP,display_pose,model_pose,model_transform
from test_bridge import frame,hand
import server


def handle_frame(x=0.,y=0.,angle=0.):
    target=[0,0,math.sin(angle/2),math.cos(angle/2)]
    q=multiply(HANDLE_IN_GRIP['q'],[0,1,0,0]);inverse=[-q[0],-q[1],-q[2],q[3]]
    gripq=multiply(target,inverse)
    grip=dict(p=sub([x,y,0],rotate(gripq,HANDLE_IN_GRIP['p'])),q=gripq,flags=15)
    return frame(hands=[hand(grip=grip),hand()])


class PointingEndTests(unittest.TestCase):
    def test_handle_ray_starts_at_same_base_and_points_opposite_its_outward_normal(self):
        for gripq in ([0,0,0,1],[0,math.sin(.3),0,math.cos(.3)]):
            grip=dict(p=[1.,2.,3.],q=gripq,flags=15)
            f=frame(hands=[hand(grip=grip),hand()]);pose=pointing_pose(f,'handle')
            expected_origin=[a+b for a,b in zip(grip['p'],rotate(gripq,HANDLE_IN_GRIP['p']))]
            self.assertEqual(pose['p'],expected_origin)
            inward=rotate(pose['q'],[0,0,-1]);outward=rotate(multiply(gripq,HANDLE_IN_GRIP['q']),[0,0,-1])
            for a,b in zip(inward,outward):self.assertAlmostEqual(a,-b)

    def test_handle_mode_only_overrides_the_right_wiimote_model(self):
        f=handle_frame(.2,.1,.3);f['screen']={'ray_mode':'handle'}
        expected=pointing_pose(f,'handle')
        actual=display_pose(f,0,True,'motionplus')
        for key in ('p','q'):
            for a,b in zip(actual[key],expected[key]):self.assertAlmostEqual(a,b)
        self.assertEqual(display_pose(f,0,False),model_pose(f['hands'][0],0))
        nunchuk=display_pose(f,0,True,'nunchuk')
        self.assertEqual(nunchuk['p'],f['hands'][0]['grip']['p'])
        self.assertEqual(nunchuk['q'],multiply(f['hands'][0]['grip']['q'],WII_IN_GRIP))

    def test_handle_transform_matches_steamvr_authored_base(self):
        folder=ASSETS/'oculus_quest2_controller_right'
        metadata=json.loads((folder/(folder.name+'.json')).read_text())
        gp,gq=model_transform(metadata,'openxr_grip');bp,bq=model_transform(metadata,'base')
        expected=relative_pose(dict(p=gp,q=gq),dict(p=bp,q=bq))
        for key in ('p','q'):
            for a,b in zip(HANDLE_IN_GRIP[key],expected[key]):self.assertAlmostEqual(a,b)

    def test_handle_ray_hit_and_roll_follow_selected_end(self):
        screen=VirtualScreen();screen.settings(2,1,2);screen.calibrate_center(frame())
        screen.set_ray_mode('handle')
        for angle in (-math.pi/3,0,math.pi/3):
            f=handle_frame(.2,.1,angle)
            pose=pointing_pose(f,'handle')
            self.assertAlmostEqual(pose['p'][0],.2);self.assertAlmostEqual(pose['p'][1],.1)
            hit=screen.pointer(f)
            self.assertTrue(hit['valid']);self.assertAlmostEqual(hit['u'],.6);self.assertAlmostEqual(hit['v'],.4)
            self.assertAlmostEqual(hit['roll'],angle/math.pi)
            f['screen']=screen.data
            origin,direction=relative_ray(f)
            for a,b in zip(origin,[.2,.1,0.]):self.assertAlmostEqual(a,b)
            for a,b in zip(direction,[0,0,-1.]):self.assertAlmostEqual(a,b)

    def test_switch_persists_and_keeps_screen_base_and_edge_calibration(self):
        with tempfile.TemporaryDirectory() as folder:
            screen=VirtualScreen(Path(folder)/'screen.json');screen.calibrate_center(frame())
            before=copy.deepcopy(screen.data);screen.depth_reference=2
            screen.set_ray_mode('handle')
            self.assertIsNone(screen.depth_reference)
            before['ray_mode']='handle';self.assertEqual(screen.data,before)
            self.assertEqual(VirtualScreen(screen.path).data,before)
            screen.set_ray_mode('face');self.assertTrue(screen.pointer(frame())['valid'])
            with self.assertRaises(ValueError):screen.set_ray_mode('invalid')
            self.assertEqual(screen.data['ray_mode'],'face')

    def test_handle_calibration_uses_selected_ray_origin(self):
        screen=VirtualScreen();screen.settings(2,1,2);screen.set_ray_mode('handle')
        f=handle_frame(.2,.1);screen.calibrate_center(f)
        for a,b in zip(screen.data['center'],[.2,.1,-2]):self.assertAlmostEqual(a,b)
        hit=screen.pointer(f);self.assertAlmostEqual(hit['u'],.5);self.assertAlmostEqual(hit['v'],.5)

    def test_missing_or_lost_grip_hides_handle_pointer_but_keeps_face_mode(self):
        screen=VirtualScreen();screen.calibrate_center(frame())
        for f in (frame(),frame(hands=[hand(grip=dict(p=[0,0,0],q=[0,0,0,1],flags=7)),hand()])):
            self.assertTrue(screen.pointer(f)['valid'])
            screen.set_ray_mode('handle');hit=screen.pointer(f)
            self.assertFalse(hit['valid']);self.assertIn('tracked grip pose',hit['reason'])
            screen.set_ray_mode('face')

    def test_server_uses_handle_hit_for_quest_crosshair_and_validates_grip_pose(self):
        state=server.State();state.screen.settings(2,1,2);state.screen.calibrate_center(frame())
        state.screen.set_ray_mode('handle');state.update(handle_frame(.2,.1,.3),'quest','test')
        packet=struct.unpack('<4s4f',server.cursor_packet(state.snapshot()))
        self.assertEqual(packet[:2],(b'Q2CR',1.));self.assertAlmostEqual(packet[2],.6);self.assertAlmostEqual(packet[3],.4)
        for grip in (None,dict(p=[math.nan,0,0],q=[0,0,0,1],flags=15),dict(p=[0,0,0],q=[0,0,0,0],flags=15)):
            with self.assertRaises(ValueError):state.update(frame(hands=[hand(grip=grip),hand()]),'quest','test')
