import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'desktop'))
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from native import DisplayGuides,STYLE


class DisplayGuidesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        cls.app.setStyleSheet(STYLE)

    def render(self,mode):
        guide=DisplayGuides();guide.resize(640,360);guide.mode=mode
        image=QImage(640,360,QImage.Format_ARGB32);image.fill(Qt.transparent);guide.render(image)
        return guide,image

    def test_overlay_keeps_game_visible_and_accepts_no_input(self):
        guide,image=self.render('16:9')
        self.assertEqual(image.pixelColor(10,10).alpha(),0)
        for x,y in [(0,180),(639,180),(320,0),(320,359)]:self.assertGreater(image.pixelColor(x,y).alpha(),0)
        self.assertTrue(guide.windowFlags()&Qt.WindowTransparentForInput)
        self.assertTrue(guide.windowFlags()&Qt.WindowDoesNotAcceptFocus)
        guide.close()

    def test_standard_picture_crosshair_excludes_side_bars(self):
        guide,image=self.render('4:3')
        self.assertEqual(image.pixelColor(20,180).alpha(),0)
        self.assertGreater(image.pixelColor(80,180).alpha(),0)
        self.assertGreater(image.pixelColor(560,180).alpha(),0)
        guide.close()


if __name__=='__main__':unittest.main()
