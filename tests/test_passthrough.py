import json
from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'desktop'))
import server
from test_bridge import frame


class PassthroughTests(unittest.TestCase):
    def test_meta_required_manifest_feature(self):
        manifest=ET.parse(Path(__file__).resolve().parents[1]/'quest/AndroidManifest.xml')
        android='{http://schemas.android.com/apk/res/android}'
        features={node.get(android+'name'):node for node in manifest.findall('uses-feature')}
        self.assertIn('com.oculus.feature.PASSTHROUGH',features)
        self.assertEqual(features['com.oculus.feature.PASSTHROUGH'].get(android+'required'),'true')

    def test_preference_keeps_existing_dolphin_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            settings=Path(directory)/'desktop.json'
            settings.write_text(json.dumps(dict(dolphin_dir='F:/Dolphin-x64',dolphin_user='existing')))
            state=server.State()
            with patch.object(server,'SETTINGS',settings):
                state.set_passthrough(True)
                with self.assertRaises(ValueError):state.set_passthrough('false')
                saved=json.loads(settings.read_text())
                self.assertEqual(saved,dict(dolphin_dir='F:/Dolphin-x64',dolphin_user='existing',passthrough=True))
                self.assertTrue(state.snapshot()['passthrough_desired'])

    def test_live_toggle_shares_tcp_with_haptics_without_reconnect(self):
        previous=server.STATE;state=server.STATE=server.State();state.passthrough_requested=False
        tcp=server.TCP(('127.0.0.1',0),server.Telemetry)
        threading.Thread(target=tcp.serve_forever,daemon=True).start()
        try:
            with socket.create_connection(tcp.server_address) as quest:
                quest.settimeout(1);reader=quest.makefile('rb')
                def update(t):quest.sendall((json.dumps(frame(t=t,haptics=True,passthrough_control=True))+'\n').encode())
                update(1.)
                self.assertEqual(struct.unpack('<4sff',reader.read(12)),(b'Q2HB',0.,0.))
                self.assertEqual(struct.unpack('<4sff',reader.read(12)),(b'Q2PT',0.,0.))
                session=state.session;state.passthrough_requested=True
                state.haptics.pulse(0);update(1.1)
                magic,right,left=struct.unpack('<4sff',reader.read(12))
                self.assertEqual(magic,b'Q2HB');self.assertAlmostEqual(right,.6,places=6)
                self.assertEqual(struct.unpack('<4sff',reader.read(12)),(b'Q2PT',1.,0.))
                self.assertEqual(state.session,session)
                state.passthrough_requested=False;update(1.2)
                self.assertEqual(struct.unpack('<4sff',reader.read(12))[0],b'Q2HB')
                self.assertEqual(struct.unpack('<4sff',reader.read(12)),(b'Q2PT',0.,0.))
                reader.close()
        finally:
            tcp.shutdown();tcp.server_close();server.STATE=previous


if __name__=='__main__':unittest.main()
