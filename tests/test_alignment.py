import copy
from pathlib import Path
import socket
import struct
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'desktop'))
from screen import VirtualScreen
from dsu import Server,packet,DATA,pad_data
from server import State
from test_bridge import frame,hand,decode_ir


class AlignmentTests(unittest.TestCase):
    def setUp(self):
        self.screen=VirtualScreen();self.screen.settings(2,1,2);self.screen.calibrate_center(frame())

    def test_reduced_output_range_corrects_early_game_edges(self):
        geometry=copy.deepcopy(self.screen.data)
        self.screen.align_dolphin(.25,.75,.2,.8)
        width,height=self.screen.image_size()
        for u,v in [(0,0),(.25,.75),(.5,.5),(1,1)]:
            hit=self.screen.pointer(frame(hands=[hand(p=[(u-.5)*width,(.5-v)*height,0]),hand()]))
            self.assertTrue(hit['valid'])
            self.assertAlmostEqual(hit['image_u'],u);self.assertAlmostEqual(hit['image_v'],v)
            self.assertAlmostEqual((hit['dolphin_u']-.25)/.5,u)
            self.assertAlmostEqual((hit['dolphin_v']-.2)/.6,v)
        for key in ('center','right','up','width','height','base_pose'):
            self.assertEqual(self.screen.data[key],geometry[key])

    def test_invalid_edges_do_not_replace_previous_alignment(self):
        self.screen.align_dolphin(.25,.75,.2,.8)
        previous=copy.deepcopy(self.screen.data)
        for values in [(.8,.2,.2,.8),(.25,.75,.8,.2),(0,0,0,1),(float('nan'),1,0,1)]:
            with self.assertRaises(ValueError):self.screen.align_dolphin(*values)
            self.assertEqual(self.screen.data,previous)

    def test_dolphin_alignment_is_reused_for_new_physical_screens(self):
        self.screen.align_dolphin(.12,.88,.24,.98)
        bounds=list(self.screen.data['dolphin_bounds'])
        self.screen.settings(1.4,.8,1.5)
        self.screen.calibrate_center(frame())
        self.assertEqual(self.screen.data['dolphin_bounds'],bounds)
        self.screen.calibrate_corners([-.3,.2,-1],[.3,.2,-1],[-.3,-.2,-1])
        self.assertEqual(self.screen.data['dolphin_bounds'],bounds)
        self.assertTrue(self.screen.data['dolphin_aligned'])

    def test_existing_alignment_can_be_refined_using_current_output_coordinates(self):
        self.screen.align_dolphin(.1,.9,.2,.8)
        first=self.screen.pointer(frame(hands=[hand(p=[-.5,.25,0]),hand()]))
        last=self.screen.pointer(frame(hands=[hand(p=[.5,-.25,0]),hand()]))
        self.screen.align_dolphin(first['dolphin_u'],last['dolphin_u'],first['dolphin_v'],last['dolphin_v'])
        for a,b in zip(self.screen.data['dolphin_bounds'],[.275,.35,.725,.65]):self.assertAlmostEqual(a,b)
        self.screen.reset_dolphin()
        self.assertEqual(self.screen.data['dolphin_bounds'],[0,0,1,1])
        self.assertFalse(self.screen.data['dolphin_aligned'])

    def test_dsu_uses_adjusted_pointer_but_keeps_motion(self):
        pointer=dict(valid=True,u=1.,v=0.,dolphin_u=.75,dolphin_v=.2)
        motion=dict(accel=[1,2,3],gyro=[4,5,6])
        payload=pad_data(0,hand(),motion,1,2.,pointer=pointer)
        from dsu import ir_points
        for a,b in zip(decode_ir(payload),ir_points(pointer)):
            for x,y in zip(a,b):self.assertLessEqual(abs(x-y),.5/32767)
        self.assertEqual(struct.unpack_from('<6f',payload,56),(1,2,3,4,5,6))

    def test_alignment_keeps_pointer_visible_below_physical_screen(self):
        state=State();state.screen=self.screen
        self.screen.align_dolphin(.12,.88,.24,.98)
        state.update(frame(hands=[hand(p=[0,-.52,0]),hand()]),'test','local')
        self.assertFalse(state.snapshot()['pointer']['valid'])
        state.calibrating=True;state.aligning_dolphin=True
        snapshot=state.snapshot();pointer=snapshot['pointer']
        self.assertTrue(pointer['valid']);self.assertGreater(pointer['v'],1)
        self.assertAlmostEqual(pointer['dolphin_v'],.9948)
        payload=pad_data(0,snapshot['hands'][0],snapshot['motion'][0],1,2.,pointer=pointer)
        self.assertEqual(payload[19],1)
        state.calibrating=False;state.aligning_dolphin=False
        self.assertFalse(state.snapshot()['pointer']['valid'])

    def test_alignment_does_not_bypass_tracking_or_forward_ray_checks(self):
        for kwargs in ({'focused':False},{'hands':[hand(active=False),hand()]},
                       {'hands':[hand(flags=3),hand()]},
                       {'hands':[hand(q=[0,1,0,0]),hand()]}):
            self.assertFalse(self.screen.pointer(frame(**kwargs),allow_outside=True)['valid'])

    def test_calibration_blocks_game_buttons_but_keeps_pointer_live(self):
        state=State();state.screen=self.screen;state.calibrating=True
        state.update(frame(hands=[hand(p=[.5,.25,0],buttons=15,trigger=1,squeeze=1),hand()]),'test','local')
        server=Server(state.snapshot,port=0)
        try:
            with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as client:
                client.settimeout(1);client.sendto(packet(DATA,bytes([1,0])+bytes(6),99,b'DSUC'),server.sock.getsockname())
                data=client.recv(512)
                self.assertEqual(data[36:39],bytes([0,0,1]))
                self.assertEqual(bytes(data[i] for i in (49,50,52,54)),bytes(4))
                self.assertEqual(data[39],1)
                self.assertTrue(any(data[high] for high in (48,51,53,55)))
        finally:server.close()


if __name__=='__main__':unittest.main()
