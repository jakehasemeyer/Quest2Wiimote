"""Bake grip-space SteamVR meshes and textures into simple native APK assets."""
from pathlib import Path
import struct
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'desktop'))
from controller_models import load_model
from PySide6.QtGui import QImage


def model_bytes(hand):
    vertices,path=load_model(hand,'openxr_grip')
    image=QImage(str(path)).convertToFormat(QImage.Format_RGBA8888).mirrored()
    if image.isNull():raise RuntimeError('Cannot load controller texture: '+str(path))
    return (struct.pack('<4sIII',b'Q2CM',len(vertices)//8,image.width(),image.height())+
            vertices.tobytes()+image.constBits().tobytes())


if __name__=='__main__':
    folder=Path(sys.argv[1]);folder.mkdir(parents=True,exist_ok=True)
    for hand in ('right','left'):
        path=folder/(hand+'.q2cm');path.write_bytes(model_bytes(hand))
        print('Packed Quest model:',path,path.stat().st_size,flush=True)
