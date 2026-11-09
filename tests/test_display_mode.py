import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'desktop'))
from screen import VirtualScreen
from test_bridge import frame,hand
import server


class DisplayModeTests(unittest.TestCase):
    def setUp(self):
        self.screen=VirtualScreen();self.screen.settings(16/9,1,2);self.screen.calibrate_center(frame())

    def aim(self,x,y=0):
        return self.screen.pointer(frame(hands=[hand(p=[x,y,0]),hand()]))

    def test_standard_picture_edges_use_middle_three_quarters_of_wide_screen(self):
        self.screen.set_display_mode('4:3')
        for x,u in [(-2/3,0),(0,.5),(2/3,1)]:
            hit=self.aim(x);self.assertTrue(hit['valid'])
            self.assertAlmostEqual(hit['dolphin_u'],u)
        self.assertFalse(self.aim(.8)['valid'])
        self.assertAlmostEqual(self.aim(0,.25)['dolphin_v'],.25)

    def test_switch_to_wide_restores_full_width_and_preserves_center_and_alignment(self):
        self.screen.align_dolphin(.1,.9,.2,.8)
        geometry=copy.deepcopy(self.screen.data)
        self.screen.set_display_mode('4:3');standard=self.aim(1/3)
        self.screen.set_display_mode('16:9');wide=self.aim(1/3)
        self.assertAlmostEqual((standard['dolphin_u']-.5)/(wide['dolphin_u']-.5),4/3)
        self.assertAlmostEqual(self.aim(0)['dolphin_u'],.5)
        self.assertTrue(self.aim(8/9)['valid'])
        self.assertEqual(self.screen.data,geometry)

    def test_wide_picture_on_standard_screen_excludes_top_and_bottom_bars(self):
        self.screen.settings(4/3,1,2)
        self.assertAlmostEqual(self.aim(0,.375)['dolphin_v'],0)
        self.assertAlmostEqual(self.aim(0,-.375)['dolphin_v'],1)
        self.assertFalse(self.aim(0,.4)['valid'])

    def test_display_mode_is_saved_and_invalid_mode_does_not_change_it(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'work') as directory:
            path=Path(directory)/'screen.json';screen=VirtualScreen(path);screen.set_display_mode('4:3')
            self.assertEqual(VirtualScreen(path).data['display_mode'],'4:3')
            with self.assertRaises(ValueError):screen.set_display_mode('stretch')
            self.assertEqual(VirtualScreen(path).data['display_mode'],'4:3')

    def test_dolphin_launch_matches_selected_wii_and_graphics_mode(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'work') as directory:
            dolphin=Path(directory);(dolphin/'Dolphin.exe').touch()
            state=server.State()
            with patch.object(server,'STATE',state),patch.object(server,'DOLPHIN',dolphin),patch.object(server.subprocess,'Popen') as launch:
                for mode,aspect,wide in [('4:3','2','False'),('16:9','1','True')]:
                    state.screen.set_display_mode(mode);server.command('dolphin',{})
                    self.assertEqual(launch.call_args.args[0],[str(dolphin/'Dolphin.exe'),'-C','GFX.Settings.AspectRatio='+aspect,'-C','SYSCONF.IPL.AR='+wide])


if __name__=='__main__':unittest.main()
