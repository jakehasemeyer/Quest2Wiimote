import json
import copy
import math
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'desktop'))
from screen import VirtualScreen, relative_ray
from motion import rotate, multiply
from dsu import pad_data
from profiles import profile
from test_bridge import frame, hand


class ScreenTests(unittest.TestCase):
    def setUp(self):
        self.screen=VirtualScreen();self.screen.settings(2.,1.,2.)
        self.screen.calibrate_center(frame())

    def ray_at(self,x,y,z=-2.):
        # Translation changes the hit point while the aim remains parallel to -Z.
        return frame(hands=[hand(p=[x,y,0]),hand()])

    def test_center_and_corners_have_absolute_coordinates(self):
        for x,y,u,v in [(0,0,.5,.5),(-1,.5,0,0),(1,-.5,1,1),(.5,.25,.75,.25)]:
            hit=self.screen.pointer(self.ray_at(x,y),allow_outside=True);self.assertTrue(hit['valid'])
            self.assertAlmostEqual(hit['u'],u);self.assertAlmostEqual(hit['v'],v)

    def test_outside_parallel_and_backward_hide_pointer(self):
        self.assertFalse(self.screen.pointer(self.ray_at(2,0))['valid'])
        q=[0,math.sin(math.pi/4),0,math.cos(math.pi/4)]
        self.assertFalse(self.screen.pointer(frame(hands=[hand(q=q),hand()]))['valid'])
        self.assertFalse(self.screen.pointer(frame(hands=[hand(q=[0,1,0,0]),hand()]))['valid'])

    def test_lost_position_and_focus_hide_pointer(self):
        self.assertFalse(self.screen.pointer(frame(hands=[hand(flags=7),hand()]))['valid'])
        self.assertFalse(self.screen.pointer(frame(focused=False))['valid'])

    def test_calibration_is_relative_to_headset(self):
        q=[0,math.sin(.4),0,math.cos(.4)];translation=[3,1,-4]
        f=frame();f['head'].update(p=translation,q=q)
        f['hands'][0].update(p=translation[:],q=q)
        screen=VirtualScreen();screen.settings(2,1,2);screen.calibrate_center(f)
        for a,b in zip(screen.data['center'],[0,0,-2]):self.assertAlmostEqual(a,b)
        f['hands'][0]['p']=[a+b for a,b in zip(translation,rotate(q,[.5,.25,0]))]
        hit=screen.pointer(f);self.assertAlmostEqual(hit['u'],.75);self.assertAlmostEqual(hit['v'],.25)

    def test_three_physical_corners_fit_rectangular_plane(self):
        self.screen.calibrate_corners([-1,.5,-2],[1,.5,-2],[-1,-.5,-2])
        self.assertEqual(self.screen.data['center'],[0.,0.,-2.])
        self.assertEqual(self.screen.data['width'],2.)
        self.assertEqual(self.screen.data['height'],1.)
        self.assertTrue(self.screen.pointer(frame())['valid'])
        with self.assertRaises(ValueError):self.screen.calibrate_corners([0,0,0],[0,0,0],[1,0,0])
        with self.assertRaises(ValueError):self.screen.calibrate_corners([0,0,0],[1,0,0],[2,0,0])

    def test_saved_calibration_roundtrip(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'work') as directory:
            path=Path(directory)/'screen.json';screen=VirtualScreen(path);screen.calibrate_center(frame())
            saved=VirtualScreen(path);self.assertEqual(saved.data,screen.data);self.assertTrue(saved.pointer(frame())['valid'])

    def test_pointer_axis_encoding_preserves_motion_and_other_stick(self):
        payload=pad_data(0,hand(stick=[.5,-.5]),{'accel':[1,2,3],'gyro':[4,5,6]},1,2.,pointer={'valid':True,'u':1.,'v':0.})
        self.assertEqual(payload[19],1)
        self.assertEqual(payload[24:28],bytes([0,128,128,0]))
        self.assertEqual(struct.unpack_from('<6f',payload,56),(1,2,3,4,5,6))
        hidden=pad_data(0,hand(),{'accel':[0,-1,0],'gyro':[0,0,0]},2,3.,pointer={'valid':False})
        self.assertEqual(hidden[19],0);self.assertEqual(hidden[22:24],bytes([128,128]))

    def test_profile_uses_absolute_pointer_and_keeps_motionplus(self):
        config=profile(pointer=True)
        self.assertIn('IMUIR/Enabled = False',config);self.assertIn('IRPassthrough/Object 2 X = (`L1` * 32640 + `Right X+` * 127) / 32767',config)
        self.assertIn('IMUGyroscope/Pitch Up = `Gyro Pitch Up`',config)
        self.assertIn('Extension/Attach MotionPlus = True',config)

    def test_unlocked_base_moves_without_moving_screen_or_stationary_controller_ray(self):
        self.screen.lock_base(False,frame())
        geometry={key:copy.deepcopy(self.screen.data[key]) for key in ('center','right','up','width','height')}
        moved=self.ray_at(.5,.25)
        moved['head'].update(p=[1,.5,.75],q=[0,math.sin(.4),0,math.cos(.4)])
        self.screen.observe(moved)
        self.assertEqual(self.screen.data['base_pose']['p'],[1,.5,.75])
        self.assertEqual(geometry,{key:self.screen.data[key] for key in geometry})
        hit=self.screen.pointer(moved)
        self.assertAlmostEqual(hit['u'],.75);self.assertAlmostEqual(hit['v'],.25)
        self.assertTrue(hit['valid'])

    def test_relocking_holds_only_base_model_and_does_not_freeze_controller_input(self):
        self.screen.lock_base(False,frame())
        moved=frame(head=hand(p=[1,0,0]));self.screen.observe(moved)
        self.screen.lock_base(True,moved)
        captured=copy.deepcopy(self.screen.data['base_pose'])
        moved=frame(head=hand(p=[2,1,0]),hands=[hand(p=[.5,.25,0]),hand()]);self.screen.observe(moved)
        self.assertEqual(self.screen.data['base_pose'],captured)
        hit=self.screen.pointer(moved)
        self.assertAlmostEqual(hit['u'],.75);self.assertAlmostEqual(hit['v'],.25)

    def test_lock_toggles_do_not_rewrite_screen_geometry(self):
        geometry={key:copy.deepcopy(self.screen.data[key]) for key in ('center','right','up','width','height','method')}
        for locked in (False,True,False,True):
            self.screen.lock_base(locked,frame(head=hand(p=[.4,.2,0])))
            self.assertEqual(geometry,{key:self.screen.data[key] for key in geometry})

    def test_cannot_reposition_base_without_live_head_tracking(self):
        for f in (frame(focused=False),frame(head=hand(flags=3))):
            with self.assertRaises(ValueError):self.screen.lock_base(False,f)
            self.assertTrue(self.screen.data['base_locked'])

    def test_restored_base_pose_preserves_room_alignment_when_tracking_resumes(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'work') as directory:
            screen=VirtualScreen(Path(directory)/'screen.json');screen.settings(2,1,2)
            q=[0,math.sin(.3),0,math.cos(.3)];translation=[3,1,-4]
            f=frame(head=hand(p=translation,q=q),hands=[hand(p=translation[:],q=q),hand()])
            screen.calibrate_center(f);screen.lock_base(False,f)
            moved=copy.deepcopy(f)
            moved['head'].update(p=[a+b for a,b in zip(translation,rotate(q,[1,.2,.5]))],q=multiply(q,[math.sin(.2),0,0,math.cos(.2)]))
            screen.observe(moved);screen.lock_base(True,moved)
            saved=VirtualScreen(screen.path);saved.observe(moved)
            self.assertEqual(saved.data,screen.data)
            hit=saved.pointer(moved)
            self.assertAlmostEqual(hit['u'],.5);self.assertAlmostEqual(hit['v'],.5)
            for a,b in zip(saved.reference['p'],translation):self.assertAlmostEqual(a,b)

    def test_state_exposes_fixed_workspace_reference_for_preview_and_pointer(self):
        from server import State
        state=State();state.screen=self.screen;state.screen.lock_base(False,frame())
        state.update(frame(head=hand(p=[1,0,0])),'test','local')
        snap=state.snapshot()
        self.assertEqual(snap['reference']['p'],[0.,0.,0.])
        self.assertEqual(snap['screen']['base_pose']['p'],[1.,0.,0.])
        self.assertAlmostEqual(snap['pointer']['u'],.5)


if __name__=='__main__':unittest.main()
