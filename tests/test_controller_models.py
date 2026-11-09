import hashlib
import json
import math
from pathlib import Path
import sys
import struct
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'desktop'))
from controller_models import (ASSETS,ASSET_ROOT,TOUCH_BOTTOM_IN_GRIP,WII_IN_GRIP,display_pose,load_headset,load_model,
    load_wii_model,model_pose,model_transform,wii_model_names)
from motion import rotate,inverse_rotate
from screen import add,sub,length
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from quest_models import model_bytes
from PySide6.QtGui import QImage


class ControllerModelTests(unittest.TestCase):
    def test_desktop_pose_matches_native_grip_mesh_registration(self):
        grip=dict(p=[1.,2.,3.],q=[0.,math.sin(.3),0.,math.cos(.3)],flags=15)
        for index,side in enumerate(('right','left')):
            hand=dict(p=[8,9,10],q=[0,0,0,1],grip=grip)
            pose=model_pose(hand,index)
            aim_vertices,_=load_model(side);grip_vertices,_=load_model(side,'openxr_grip')
            for start in (0,80,len(aim_vertices)-8):
                desktop=add(pose['p'],rotate(pose['q'],aim_vertices[start:start+3]))
                native=add(grip['p'],rotate(grip['q'],grip_vertices[start:start+3]))
                self.assertLess(length(sub(desktop,native)),1e-7)
        legacy=dict(p=[1,2,3],q=[0,0,0,1]);self.assertIs(model_pose(legacy,0),legacy)

    def test_quest_asset_preserves_grip_mesh_and_bottom_up_rgba_texture(self):
        for hand in ('right','left'):
            packed=model_bytes(hand);magic,count,width,height=struct.unpack_from('<4sIII',packed)
            vertices,path=load_model(hand,'openxr_grip');image=QImage(str(path))
            self.assertEqual(magic,b'Q2CM');self.assertEqual(count,len(vertices)//8)
            self.assertEqual((width,height),(image.width(),image.height()))
            self.assertEqual(packed[16:16+count*32],vertices.tobytes())
            self.assertEqual(len(packed),16+count*32+width*height*4)
            for x,y in ((0,0),(20,10),(width-1,height-1)):
                pixel=image.pixelColor(x,height-1-y);offset=16+count*32+(y*width+x)*4
                self.assertEqual(packed[offset:offset+4],bytes((pixel.red(),pixel.green(),pixel.blue(),pixel.alpha())))

    def test_assets_match_downloaded_steamvr_files(self):
        source=json.loads((ASSETS/'source.json').read_text())
        for entry in source['files']:
            self.assertEqual(hashlib.sha256((ASSETS/entry['path']).read_bytes()).hexdigest(),entry['sha256'])

    def test_model_origin_axes_and_scale_match_openxr_aim_space(self):
        for hand in ('right','left'):
            folder=ASSETS/('oculus_quest2_controller_'+hand)
            metadata=json.loads((folder/(folder.name+'.json')).read_text())
            origin,q=model_transform(metadata)
            self.assertEqual(q[1:3],[0.,0.])
            self.assertAlmostEqual(q[0],math.sin(math.radians(-39.4)/2))
            self.assertEqual(inverse_rotate(q,sub(origin,origin)),[0.,0.,0.])
            vertices,_=load_model(hand)
            self.assertGreater(len(vertices)//24,9000)
            positions=[vertices[i:i+3] for i in range(0,len(vertices),8)]
            extents=[max(v[i] for v in positions)-min(v[i] for v in positions) for i in range(3)]
            self.assertTrue(all(.08<value<.23 for value in extents))
            # Converting into aim space must preserve meters and recover the supplied mesh.
            raw=[]
            for line in (folder/(folder.name+'.obj')).read_text().splitlines():
                if line.startswith('v '):raw.append(list(map(float,line.split()[1:4])))
            first_face=next(line for line in (folder/(folder.name+'.obj')).read_text().splitlines() if line.startswith('f '))
            raw_index=int(first_face.split()[1].split('/')[0])-1
            recovered=add(origin,rotate(q,positions[0]))
            self.assertLess(length(sub(recovered,raw[raw_index])),1e-7)
            for i in range(0,len(vertices),8):self.assertAlmostEqual(length(vertices[i+3:i+6]),1.,places=4)

    def test_wii_models_use_supplied_assets_at_physical_scale(self):
        folder=ASSET_ROOT/'wii';metadata=json.loads((folder/'source.json').read_text())
        for entry in metadata['files']:
            self.assertEqual(hashlib.sha256((folder/entry['name']).read_bytes()).hexdigest(),entry['sha256'])
        expected={'motionplus':(4302,[.04416212,.03743126,.16399503]),'nunchuk':(3942,[.03366296,.04838945,.11])}
        for name,(count,dimensions) in expected.items():
            vertices,texture=load_wii_model(name);self.assertTrue(texture.exists());self.assertEqual(len(vertices)//8,count)
            positions=[vertices[i:i+3] for i in range(0,len(vertices),8)]
            extents=[max(p[i] for p in positions)-min(p[i] for p in positions) for i in range(3)]
            for actual,wanted in zip(extents,dimensions):self.assertAlmostEqual(actual,wanted,places=5)
            for i in range(0,len(vertices),8):self.assertAlmostEqual(length(vertices[i+3:i+6]),1.,places=4)
        self.assertEqual(wii_model_names(),('motionplus','nunchuk'))
        self.assertEqual(wii_model_names(True),('nunchuk','motionplus'))
        hand=dict(p=[8,9,10],q=[0,0,0,1],grip=dict(p=[1,2,3],q=[0,0,0,1],flags=15))
        sample=dict(hands=[hand,hand],screen={'ray_mode':'face'})
        wii=display_pose(sample,0,True,'motionplus')
        self.assertEqual(wii['p'],add(hand['grip']['p'],TOUCH_BOTTOM_IN_GRIP[0]))
        self.assertEqual(wii['q'],WII_IN_GRIP)
        self.assertEqual(display_pose(sample,0,False),model_pose(hand,0))
        self.assertEqual(metadata['models']['motionplus']['origin'][2],-6.914794)
        self.assertEqual(metadata['models']['nunchuk']['origin'],[0.,0.,-.55])

    def test_motion_sensor_replaces_headset_at_meter_scale(self):
        folder=ASSET_ROOT/'motion_sensor';metadata=json.loads((folder/'source.json').read_text())
        for entry in metadata['files']:
            self.assertEqual(hashlib.sha256((folder/entry['name']).read_bytes()).hexdigest(),entry['sha256'])
        vertices,texture=load_headset()
        self.assertTrue(texture.exists());self.assertEqual(len(vertices)//8,3768)
        positions=[vertices[i:i+3] for i in range(0,len(vertices),8)]
        extents=[max(p[i] for p in positions)-min(p[i] for p in positions) for i in range(3)]
        for actual,wanted in zip(extents,[.455822,.137103,.0762]):self.assertAlmostEqual(actual,wanted,places=6)
