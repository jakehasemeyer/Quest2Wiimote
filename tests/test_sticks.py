import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'desktop'))
import server
from dsu import pad_data
from test_bridge import frame, hand, decode_ir


class StickTests(unittest.TestCase):
    def setUp(self):
        self.state=server.State();self.state.hand_tracking_requested=False
        self.state.stick_deadzones=[.2,.4]

    def sample(self,right,left):
        self.state.update(frame(hands=[hand(stick=right),hand(stick=left)]),'sticks','local')
        return self.state.snapshot()

    def test_drift_is_neutral_in_both_dsu_paths_and_raw_input_survives(self):
        state=self.sample([.1,-.1],[.2,.2])
        for slot in (0,1):
            h=state['hands'][slot]
            self.assertEqual(h['stick'],[0.,0.])
            self.assertNotEqual(h['raw_stick'],[0.,0.])
            packet=pad_data(slot,h,server.NEUTRAL,1,1.)
            self.assertEqual(packet[20:24],bytes([128]*4))
            self.assertEqual(packet[24:28],bytes(4))

    def test_radial_filter_preserves_direction_and_full_travel(self):
        state=self.sample([.36,.48],[-1.,0.])
        for actual,expected in zip(state['hands'][0]['stick'],[.3,.4]):self.assertAlmostEqual(actual,expected)
        self.assertEqual(state['hands'][1]['stick'],[-1.,0.])
        self.assertEqual(self.state.frame['hands'][0]['stick'],[.36,.48])
        self.assertEqual(self.state.snapshot()['hands'][0]['stick'],state['hands'][0]['stick'])

    def test_disabled_boundary_and_diagonal_saturation(self):
        self.state.stick_deadzones=[0.,.5]
        state=self.sample([.01,-.02],[1.,1.])
        self.assertEqual(state['hands'][0]['stick'],[.01,-.02])
        self.assertAlmostEqual(math.hypot(*state['hands'][1]['stick']),1.)
        self.state.frame['hands'][1]['stick']=[.5,0.]
        self.assertEqual(self.state.snapshot()['hands'][1]['stick'],[0.,0.])

    def test_stale_inactive_and_idle_sticks_are_neutral(self):
        self.sample([1.,0.],[0.,1.])
        self.state.frame['hands'][0]['active']=False;self.state.left_idle=True
        for h in self.state.snapshot()['hands']:
            self.assertEqual(h['stick'],[0.,0.]);self.assertEqual(h['raw_stick'],[0.,0.])
        self.state.frame['hands'][0]['active']=True;self.state.last-=1
        self.assertEqual(self.state.snapshot()['hands'][0]['raw_stick'],[0.,0.])

    def test_saved_independent_settings_apply_without_a_new_frame(self):
        self.sample([.3,0.],[.3,0.])
        with tempfile.TemporaryDirectory() as directory:
            settings=Path(directory)/'desktop.json';settings.write_text('{"passthrough":true}')
            with patch.object(server,'SETTINGS',settings):
                self.state.set_stick_deadzone(0,35)
                self.assertEqual(self.state.snapshot()['hands'][0]['stick'],[0.,0.])
                saved=json.loads(settings.read_text())
                self.assertEqual(saved,dict(passthrough=True,stick_deadzones=[.35,.4]))
                with patch.object(server,'preferences',saved):self.assertEqual(server.State().stick_deadzones,[.35,.4])
                for slot,value in ((2,12),(0,-1),(1,51),(0,True)):
                    with self.assertRaises(ValueError):self.state.set_stick_deadzone(slot,value)

    def test_hand_dpad_and_ir_coordinates_are_not_filtered(self):
        self.sample([0.,0.],[0.,0.])
        self.state.hand_tracking_requested=True;self.state.frame['hand_tracking']=True
        self.state.frame['hands'][1]['dpad']=[1,0]
        state=self.state.snapshot();h=state['hands'][0]
        self.assertEqual(h['dpad'],[1,0])
        pointer=dict(valid=True,u=.3,v=.7)
        packet=pad_data(0,h,server.NEUTRAL,1,1.,pointer=pointer)
        baseline=pad_data(0,hand(),server.NEUTRAL,1,1.,pointer=pointer)
        self.assertEqual(decode_ir(packet),decode_ir(baseline))
        self.assertEqual(packet[26],255)


if __name__=='__main__':unittest.main()
