"""SteamVR Quest 2 meshes, expressed in OpenXR aim space and drawn with Qt OpenGL."""
from array import array
import json
import math
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

from PySide6.QtGui import QImage, QMatrix4x4, QQuaternion, QVector3D
from PySide6.QtOpenGL import (QOpenGLBuffer, QOpenGLFunctions_3_3_Core,
    QOpenGLShader, QOpenGLShaderProgram, QOpenGLTexture, QOpenGLVertexArrayObject)

from motion import inverse_rotate, multiply,rotate
from screen import sub, cross, unit,scale,add,pointing_pose,relative_pose

ASSET_ROOT=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[1]))/'assets'
ASSETS=ASSET_ROOT/'controllers'


def model_transform(metadata,component='openxr_aim'):
    pose=metadata['components'][component]['component_local']
    q=[0.,0.,0.,1.]
    for axis,degrees in enumerate(pose['rotate_xyz']):
        r=[0.,0.,0.,math.cos(math.radians(degrees)/2)]
        r[axis]=math.sin(math.radians(degrees)/2)
        q=multiply(r,q)
    return pose['origin'],q


def load_model(hand,component='openxr_aim'):
    name='oculus_quest2_controller_'+hand;folder=ASSETS/name
    origin,q=model_transform(json.loads((folder/(name+'.json')).read_text()),component)
    return load_obj(folder/(name+'.obj'),origin,q),folder/(name+'.png')


GRIP_TO_AIM=[];TOUCH_BOTTOM_IN_GRIP=[]
for _hand in ('right','left'):
    _name='oculus_quest2_controller_'+_hand
    _metadata=json.loads((ASSETS/_name/(_name+'.json')).read_text())
    _gp,_gq=model_transform(_metadata,'openxr_grip');_ap,_aq=model_transform(_metadata)
    GRIP_TO_AIM.append(relative_pose(dict(p=_gp,q=_gq),dict(p=_ap,q=_aq)))
    _bp,_bq=model_transform(_metadata,'base');TOUCH_BOTTOM_IN_GRIP.append(relative_pose(dict(p=_gp,q=_gq),dict(p=_bp,q=_bq))['p'])

_wii_pitch=math.radians(-37.4)/2
WII_IN_GRIP=[math.sin(_wii_pitch),0.,0.,math.cos(_wii_pitch)]


def model_pose(hand,index):
    grip=hand.get('grip')
    if not grip or grip.get('flags',0)&15!=15:return hand
    offset=GRIP_TO_AIM[index]
    return dict(p=add(grip['p'],rotate(grip['q'],offset['p'])),q=multiply(grip['q'],offset['q']))


def display_pose(frame,index,wii=False,wii_name=None):
    hand=frame['hands'][index]
    if wii and wii_name=='motionplus' and index==0 and frame.get('screen',{}).get('ray_mode')=='handle':
        try:return pointing_pose(frame,'handle')
        except ValueError:pass
    if wii:
        grip=hand.get('grip')
        pose=grip if grip and grip.get('flags',0)&15==15 else hand
        p=add(pose['p'],rotate(pose['q'],TOUCH_BOTTOM_IN_GRIP[index])) if wii_name=='motionplus' else pose['p']
        return dict(p=p,q=multiply(pose['q'],WII_IN_GRIP))
    return model_pose(hand,index)


def wii_model_names(swapped=False):return ('nunchuk','motionplus') if swapped else ('motionplus','nunchuk')


def load_dae(path,origin,meters):
    root=ET.parse(path).getroot();namespace=root.tag[1:].split('}')[0];tag=lambda name:'{'+namespace+'}'+name
    vertices=array('f')
    for mesh in root.findall('.//'+tag('library_geometries')+'/'+tag('geometry')+'/'+tag('mesh')):
        sources={}
        for source in mesh.findall(tag('source')):
            values=list(map(float,source.find(tag('float_array')).text.split()))
            accessor=source.find(tag('technique_common')+'/'+tag('accessor'))
            stride=int(accessor.get('stride',len(accessor.findall(tag('param')))))
            offset=int(accessor.get('offset','0'));count=int(accessor.get('count'))
            sources['#'+source.get('id')]=[values[offset+i*stride:offset+(i+1)*stride] for i in range(count)]
        position_sources={}
        for group in mesh.findall(tag('vertices')):
            position_sources['#'+group.get('id')]=next(i.get('source') for i in group.findall(tag('input')) if i.get('semantic')=='POSITION')
        for triangles in mesh.findall(tag('triangles')):
            inputs=[(i.get('semantic'),i.get('source'),int(i.get('offset','0'))) for i in triangles.findall(tag('input'))]
            stride=max(i[2] for i in inputs)+1;indices=list(map(int,triangles.find(tag('p')).text.split()))
            for start in range(0,len(indices),stride):
                values={semantic:sources[position_sources.get(source,source)][indices[start+offset]] for semantic,source,offset in inputs if semantic in ('VERTEX','NORMAL','TEXCOORD')}
                p=values['VERTEX'];n=unit([-values['NORMAL'][0],values['NORMAL'][1],-values['NORMAL'][2]])
                uv=values['TEXCOORD'];vertices.extend([-(p[0]-origin[0])*meters,(p[1]-origin[1])*meters,-(p[2]-origin[2])*meters]+n+uv[:2])
    return vertices


def load_wii_model(name):
    folder=ASSET_ROOT/'wii';source=json.loads((folder/'source.json').read_text());model=source['models'][name]
    return load_dae(folder/model['dae'],model['origin'],model['meters_per_unit']),folder/model['texture']


def load_headset():
    folder=ASSET_ROOT/'motion_sensor';metadata=json.loads((folder/'source.json').read_text())
    return load_obj(folder/'motion_sensor.obj',metadata['origin'],metadata['rotation'],metadata['meters_per_unit']),folder/'motion_sensor.png'


def load_obj(path,origin,q,meters=1.):
    positions=[];normals=[];uvs=[];triangles=array('f')
    for line in path.read_text().splitlines():
        fields=line.split()
        if not fields:continue
        if fields[0]=='v':positions.append(scale(inverse_rotate(q,sub(list(map(float,fields[1:4])),origin)),meters))
        elif fields[0]=='vn':normals.append(inverse_rotate(q,list(map(float,fields[1:4]))))
        elif fields[0]=='vt':uvs.append(list(map(float,fields[1:3])))
        elif fields[0]=='f':
            corners=[tuple(int(n)-1 for n in field.split('/')) for field in fields[1:]]
            for i in range(1,len(corners)-1):
                face=(corners[0],corners[i],corners[i+1])
                for corner in face:
                    # SteamVR's right OBJ omits one corner normal; use its coplanar face normal.
                    v,t=corner[:2];normal=normals[corner[2] if len(corner)==3 else face[0][2]]
                    triangles.extend(positions[v]+normal+uvs[t])
    return triangles


class ControllerRenderer:
    def __init__(self):
        self.gl=QOpenGLFunctions_3_3_Core();self.gl.initializeOpenGLFunctions()
        self.program=QOpenGLShaderProgram()
        vertex='''#version 330 core
layout(location=0) in vec3 position;
layout(location=1) in vec3 normal;
layout(location=2) in vec2 uv;
uniform mat4 vp, model;
out vec3 n; out vec2 texcoord;
void main(){
  vec4 world=model*vec4(position,1);
  gl_Position=vp*world;
  n=mat3(model)*normal; texcoord=uv;
}
'''
        fragment='''#version 330 core
in vec3 n; in vec2 texcoord;
uniform sampler2D image;
uniform bool textured;
uniform vec3 color;
out vec4 outputColor;
void main(){
  vec3 base=textured ? texture(image,texcoord).rgb : color;
  float light=textured ? .6+.4*max(dot(normalize(n),normalize(vec3(-.4,.8,.5))),0) : 1;
  outputColor=vec4(base*light,1);
}
'''
        for kind,source in ((QOpenGLShader.Vertex,vertex),(QOpenGLShader.Fragment,fragment)):
            if not self.program.addShaderFromSourceCode(kind,source):raise RuntimeError(self.program.log())
        if not self.program.link():raise RuntimeError(self.program.log())
        self.vao=QOpenGLVertexArrayObject();self.vao.create()
        self.scene=QOpenGLBuffer();self.scene.create()
        self.touch_meshes=[];self.wii_meshes=[]
        def upload(model):
            vertices,path=model
            buffer=QOpenGLBuffer();buffer.create();buffer.bind();data=vertices.tobytes();buffer.allocate(data,len(data));buffer.release()
            texture=QOpenGLTexture(QImage(str(path)).mirrored())
            texture.setMinificationFilter(QOpenGLTexture.LinearMipMapLinear);texture.setMagnificationFilter(QOpenGLTexture.Linear)
            texture.setWrapMode(QOpenGLTexture.DirectionS,QOpenGLTexture.Repeat)
            texture.setWrapMode(QOpenGLTexture.DirectionT,QOpenGLTexture.MirroredRepeat if path.name=='motionplus.png' else QOpenGLTexture.Repeat)
            return buffer,len(vertices)//8,texture
        self.touch_meshes=[upload(load_model(hand)) for hand in ('right','left')]
        self.wii_meshes=[upload(load_wii_model(name)) for name in wii_model_names()]
        self.headset=upload(load_headset())

    def attributes(self,buffer):
        buffer.bind()
        for location,offset,size in ((0,0,3),(1,12,3),(2,24,2)):
            self.program.enableAttributeArray(location)
            self.program.setAttributeBuffer(location,0x1406,offset,size,32)

    def draw(self,view,queue,hands,wii=False,swapped=False,part='all'):
        gl=self.gl;self.vao.bind();self.program.bind()
        ratio=view.devicePixelRatioF();gl.glViewport(0,0,round(view.width()*ratio),round(view.height()*ratio))
        gl.glEnable(0x0B71);gl.glDepthMask(True);gl.glDepthFunc(0x0203);gl.glDisable(0x0BE2);gl.glDisable(0x0B44)
        gl.glClear(0x0100)
        projection=QMatrix4x4();projection.perspective(43,view.width()/view.height(),.045,100.)
        camera=QMatrix4x4();camera.lookAt(QVector3D(*view.eye),QVector3D(*view.target),QVector3D(*view.camera_up))
        self.program.setUniformValue('vp',projection*camera);gl.glUniform1i(self.program.uniformLocation('image'),0)
        self.program.setUniformValue('model',QMatrix4x4());gl.glUniform1i(self.program.uniformLocation('textured'),0)
        # These few screen/headset faces share the controllers' depth buffer.
        vertices=array('f');colors=[]
        from PySide6.QtGui import QColor
        for _,points,color,_ in queue:
            normal=unit(cross(sub(points[1],points[0]),sub(points[2],points[0])))
            start=len(vertices)//8
            for i in range(1,len(points)-1):
                for p in (points[0],points[i],points[i+1]):vertices.extend(p+normal+[0.,0.])
            colors.append((start,len(vertices)//8-start,QColor(color)))
        self.scene.bind();data=vertices.tobytes();self.scene.allocate(data,len(data));self.attributes(self.scene)
        for start,count,color in colors:
            self.program.setUniformValue('color',QVector3D(color.redF(),color.greenF(),color.blueF()))
            gl.glDrawArrays(0x0004,start,count)
        gl.glUniform1i(self.program.uniformLocation('textured'),1)
        if part in ('all','controllers'):
            hand_meshes=self.wii_meshes[::-1] if wii and swapped else self.wii_meshes if wii else self.touch_meshes
            for (p,q,_,tracked),(buffer,count,texture) in zip(hands[:2],hand_meshes):
                model=QMatrix4x4();model.translate(QVector3D(*p));model.rotate(QQuaternion(q[3],q[0],q[1],q[2]))
                self.program.setUniformValue('model',model)
                texture.bind(0);self.attributes(buffer);gl.glDrawArrays(0x0004,0,count);texture.release()
        if part in ('all','base'):
            p,q,_,_=hands[2];buffer,count,texture=self.headset
            model=QMatrix4x4();model.translate(QVector3D(*p));model.rotate(QQuaternion(q[3],q[0],q[1],q[2]));self.program.setUniformValue('model',model)
            texture.bind(0);self.attributes(buffer);gl.glDrawArrays(0x0004,0,count);texture.release()
        self.scene.release();self.program.release();self.vao.release();gl.glDisable(0x0B71)

    def destroy(self):
        for buffer,_,texture in self.touch_meshes+self.wii_meshes+[self.headset]:buffer.destroy();texture.destroy()
        self.scene.destroy();self.vao.destroy();self.program.removeAllShaders()
