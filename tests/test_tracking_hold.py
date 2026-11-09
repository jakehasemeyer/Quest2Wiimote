import math
import socket
import unittest
from unittest.mock import patch

from test_bridge import frame,hand
from server import State
from dsu import Server,packet,DATA


class TrackingHoldTests(unittest.TestCase):
    def setUp(self):
        self.state=State()
        self.state.hand_tracking_requested=False
        self.now=100.
        self.clock=patch('server.time.monotonic',side_effect=lambda:self.now)
        self.clock.start();self.addCleanup(self.clock.stop)
        self.good=hand(p=[0,0,-.3],q=[0,0,math.sin(.2),math.cos(.2)],
                       grip=dict(p=[.1,.2,-.4],q=[0,0,0,1],flags=15),
                       v=[1,2,3],w=[4,5,6],buttons=3,trigger=1,squeeze=1,stick=[1,1])
        self.off=hand(active=False,flags=0,p=[8,-5,2])
        self.state.screen.calibrate_center(frame(hands=[self.good,hand()]))
        self.update([self.good,self.good])

    def update(self,hands,**changes):
        self.now=round(self.now+.1,6)
        self.state.update(frame(t=self.now,hands=hands,**changes),'session','local')
        return self.state.snapshot()

    def run_until(self,target,hands):
        while self.now<target:self.update(hands)
        return self.state.snapshot()

    def test_both_hold_pose_grip_tilt_and_pointer_for_twenty_seconds(self):
        before=self.state.snapshot()
        held=self.update([self.off,self.off])
        for i in (0,1):
            self.assertTrue(held['hands'][i]['active'])
            for key in ('p','q','grip'):self.assertEqual(held['hands'][i][key],before['hands'][i][key])
            for key in ('buttons','trigger','squeeze'):self.assertEqual(held['hands'][i][key],0)
            self.assertEqual(held['hands'][i]['stick'],[0,0])
            self.assertEqual(held['hands'][i]['v'],[0,0,0])
            self.assertEqual(held['motion'][i]['gyro'],[0,0,0])
            self.assertEqual(held['motion'][i]['accel'],before['motion'][i]['accel'])
        self.assertTrue(held['pointer']['valid'])
        for key in ('u','v','roll','depth'):self.assertEqual(held['pointer'][key],before['pointer'][key])
        held=self.run_until(120.1,[self.off,self.off])
        self.assertTrue(all(h.get('held') for h in held['hands']))
        expired=self.update([self.off,self.off])
        self.assertFalse(any(h['active'] for h in expired['hands']))
        self.assertFalse(expired['pointer']['valid'])

    def test_only_nunchuk_expires_at_fifteen_seconds(self):
        self.update([hand(),self.off])
        held=self.run_until(115.1,[hand(),self.off])
        self.assertTrue(held['hands'][1]['active'])
        expired=self.update([hand(),self.off])
        self.assertTrue(expired['hands'][0]['active'])
        self.assertFalse(expired['hands'][1]['active'])

    def test_recovery_is_immediate_without_acceleration_spike(self):
        self.update([self.off,self.off])
        self.run_until(116.,[self.off,self.off])
        resumed=self.update([hand(p=[1,0,0],v=[100,0,0]),hand(p=[2,0,0])])
        self.assertEqual(resumed['hands'][0]['p'],[1,0,0])
        self.assertEqual(resumed['hands'][1]['p'],[2,0,0])
        self.assertFalse(resumed['left_idle'])
        self.assertTrue(resumed['hands'][1]['active'])
        self.assertEqual(resumed['motion'][0]['accel'],[0,-1,0])

    def test_remote_loss_extends_only_an_unexpired_nunchuk_hold(self):
        self.update([hand(),self.off])
        self.run_until(114.,[hand(),self.off])
        self.update([self.off,self.off])
        self.assertTrue(self.run_until(120.1,[self.off,self.off])['hands'][1]['active'])
        self.assertFalse(self.update([self.off,self.off])['hands'][1]['active'])
        self.assertFalse(self.update([hand(),self.off])['hands'][1]['active'])
        self.assertFalse(self.update([self.off,self.off])['hands'][1]['active'])

    def test_untracked_pose_jump_is_held_but_fast_tracked_motion_is_not(self):
        moving=self.update([hand(p=[5,0,0]),hand()])
        self.assertEqual(moving['hands'][0]['p'],[5,0,0])
        held=self.update([hand(flags=3,p=[-10,0,0]),hand()])
        self.assertEqual(held['hands'][0]['p'],[5,0,0])
        self.assertTrue(held['hands'][0]['held'])

    def test_stale_focus_loss_and_new_session_cancel_hold(self):
        self.update([self.off,self.off]);self.now+=.31
        self.assertFalse(self.state.snapshot()['hands'][0]['active'])
        self.update([self.off,self.off])
        self.assertFalse(self.state.snapshot()['hands'][0]['active'])
        self.update([self.good,self.good])
        self.assertFalse(self.update([self.off,self.off],focused=False)['hands'][0]['active'])
        self.assertFalse(self.update([self.off,self.off])['hands'][0]['active'])
        self.update([self.good,self.good]);self.now+=.1
        self.state.update(frame(t=1,hands=[self.off,self.off]),'new','local')
        self.assertFalse(self.state.snapshot()['hands'][0]['active'])

    def test_hand_tracking_does_not_hold_controller_pose(self):
        result=self.update([self.off,self.off],hand_tracking=True)
        self.assertFalse(any(h['active'] for h in result['hands']))

    def test_dsu_keeps_both_slots_and_nunchuk_attachment_connected(self):
        self.update([self.off,self.off])
        server=Server(self.state.snapshot,port=0)
        try:
            with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as client:
                client.settimeout(1)
                client.sendto(packet(DATA,bytes(8),99,b'DSUC'),server.sock.getsockname())
                packets={}
                while len(packets)<2:
                    data=client.recv(512);packets[data[20]]=data
                self.assertEqual(packets[0][21],2)
                self.assertEqual(packets[1][21],2)
                self.assertEqual(packets[0][38],1)
        finally:server.close()


if __name__=='__main__':unittest.main()
