import copy
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'desktop'))
from PySide6.QtWidgets import QApplication
from motion import rotate
from preview import TrackingView,orbit_direction
from screen import VirtualScreen,add,scale,sub,dot,cross,unit
from server import State


class CameraTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def screen(self):
        q=[.2,.3,.1,.9273618495495703]
        screen=VirtualScreen()
        screen.calibrate_corners(rotate(q,[-1,.5,0]),rotate(q,[1,.5,0]),rotate(q,[-1,-.5,0]))
        return screen,q

    def test_orbit_uses_calibrated_world_axes_without_banking(self):
        screen,q=self.screen();saved=copy.deepcopy(screen.data)
        for azimuth,elevation in [(0,0),(.6,.43),(-1.2,-.7),(2,1.3)]:
            direction,up=orbit_direction(screen.data,azimuth,elevation)
            old=[math.cos(elevation)*math.sin(azimuth),math.sin(elevation),math.cos(elevation)*math.cos(azimuth)]
            for actual,expected in zip(direction,rotate(q,old)):self.assertAlmostEqual(actual,expected)
            self.assertAlmostEqual(dot(direction,up),math.sin(elevation))
            camera_right=unit(cross(scale(direction,-1),up))
            self.assertAlmostEqual(dot(camera_right,up),0.)
        self.assertEqual(screen.data,saved)

    def test_legacy_view_preserves_eye_position_and_migrates_only_once(self):
        screen,_=self.screen();state=State();state.screen=screen
        old=dict(azimuth=.6,elevation=.1,distance=3.2,target=[.2,-.3,-1.])
        expected=add(old['target'],scale([math.cos(.1)*math.sin(.6),math.sin(.1),math.cos(.1)*math.cos(.6)],3.2))
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'work') as directory:
            path=Path(directory)/'view.json';path.write_text(json.dumps(old))
            view=TrackingView(state.snapshot,view_path=path)
            direction,_=orbit_direction(screen.data,view.azimuth,view.elevation)
            for actual,wanted in zip(add(view.target,scale(direction,view.distance)),expected):self.assertAlmostEqual(actual,wanted)
            view.use_wii_models=True;view.swap_wii_models=True
            view.save_view();restored=TrackingView(state.snapshot,view_path=path)
            self.assertEqual((restored.azimuth,restored.elevation,restored.distance,restored.target),(view.azimuth,view.elevation,view.distance,view.target))
            self.assertTrue(restored.use_wii_models);self.assertTrue(restored.swap_wii_models)
            view.close();restored.close()

    def test_reset_view_centers_on_the_monitor_and_floor(self):
        screen,_=self.screen();state=State();state.screen=screen
        view=TrackingView(state.snapshot);view.target=[100,100,100];view.reset_view()
        self.assertAlmostEqual(dot(sub(screen.data['center'],view.target),screen.data['up']),screen.data['height']/2+.762)
        self.assertAlmostEqual(dot(sub(screen.data['center'],view.target),screen.data['right']),0.)
        view.close()


if __name__=='__main__':unittest.main()
