import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'desktop'))
from motion import rotate
from preview import floor_basis
from screen import VirtualScreen,sub,dot,unit
from test_bridge import frame


class FloorTests(unittest.TestCase):
    def check_floor(self,screen):
        saved=copy.deepcopy(screen.data)
        origin,right,depth=floor_basis(screen.data)
        up=unit(screen.data['up'])
        for bottom in screen.corners()[2:]:
            self.assertAlmostEqual(dot(sub(bottom,origin),up),1.524)
            self.assertAlmostEqual(dot(sub(bottom,origin),depth),0.)
        self.assertAlmostEqual(dot(up,right),0.)
        self.assertAlmostEqual(dot(up,depth),0.)
        self.assertEqual(screen.data,saved)
        return origin

    def test_floor_follows_center_and_corner_calibration(self):
        screen=VirtualScreen();screen.settings(2,1,2);screen.calibrate_center(frame())
        first=self.check_floor(screen)
        self.assertAlmostEqual(first[1],-2.024)
        screen.calibrate_corners([-1,2,-3],[1,2,-3],[-1,1,-3])
        second=self.check_floor(screen)
        self.assertAlmostEqual(second[1],1-1.524)
        self.assertNotEqual(first,second)

    def test_floor_is_aligned_to_a_translated_and_rotated_monitor(self):
        screen=VirtualScreen()
        q=[.2,.3,.1,.9273618495495703]
        def point(p):return [a+b for a,b in zip(rotate(q,p),[3,2,-4])]
        screen.calibrate_corners(point([-1,.5,0]),point([1,.5,0]),point([-1,-.5,0]))
        self.check_floor(screen)


if __name__=='__main__':unittest.main()
