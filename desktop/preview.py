"""Native, GPU-backed Qt preview. Rendering runs independently of Quest samples."""
from collections import deque
import math
import time
import json
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QPointF, QRectF
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF, QSurfaceFormat
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QWidget

from motion import inverse_rotate,rotate
from screen import add, sub, scale, dot, cross, unit, length, relative_ray, relative_pose
from controller_models import ControllerRenderer,WII_IN_GRIP,display_pose,wii_model_names


def floor_basis(screen):
    if not screen['calibrated']:return [0,-.75,0],[1,0,0],[0,0,1]
    up=unit(screen['up']);right=unit(screen['right'])
    origin=sub(screen['center'],scale(up,screen['height']/2+1.524))
    return origin,right,unit(cross(right,up))


def orbit_direction(screen,azimuth,elevation):
    _,right,depth=floor_basis(screen)
    up=unit(cross(depth,right))
    direction=add(scale(right,math.cos(elevation)*math.sin(azimuth)),
                  add(scale(up,math.sin(elevation)),scale(depth,math.cos(elevation)*math.cos(azimuth))))
    return direction,up


class TrackingView(QOpenGLWidget):
    def __init__(self, snapshot, parent=None, view_path=None):
        super().__init__(parent)
        fmt=QSurfaceFormat();fmt.setVersion(3,3);fmt.setProfile(QSurfaceFormat.CoreProfile)
        fmt.setSwapInterval(0);fmt.setSamples(4);self.setFormat(fmt)
        self.snapshot=snapshot;self.target_fps=72;self.show_frusta=True;self.range=1.2
        self.use_wii_models=False;self.swap_wii_models=False
        self.controllers=None
        self.azimuth=.6;self.elevation=.43;self.distance=3.1;self.target=[0,-.12,-.6]
        screen=self.snapshot()['screen']
        if screen['calibrated']:self.target=sub(screen['center'],scale(unit(screen['up']),screen['height']/2+.762))
        self.view_path=Path(view_path) if view_path else None
        if self.view_path and self.view_path.exists():
            saved=json.loads(self.view_path.read_text())
            for name in ('azimuth','elevation','distance','target','use_wii_models','swap_wii_models'):
                if name in saved:setattr(self,name,saved[name])
            if saved.get('orbit_basis')!='display':
                direction=[math.cos(self.elevation)*math.sin(self.azimuth),math.sin(self.elevation),math.cos(self.elevation)*math.cos(self.azimuth)]
                _,right,depth=floor_basis(screen);up=unit(cross(depth,right))
                self.azimuth=math.atan2(dot(direction,right),dot(direction,depth))
                self.elevation=max(-1.3,min(1.3,math.asin(max(-1.,min(1.,dot(direction,up))))))
        self.last_mouse=None;self.frame_times=deque(maxlen=240);self.paint_cost=deque(maxlen=240)
        self.setMinimumSize(580,380);self.setMouseTracking(True)
        self.timer=QTimer(self);self.timer.setTimerType(Qt.PreciseTimer);self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.tick);self.next_frame=time.perf_counter();self.timer.start(1)

    def tick(self):
        now=time.perf_counter()
        if now >= self.next_frame-.0002:
            self.update();self.next_frame+=1/self.target_fps
            if self.next_frame<now-.03:self.next_frame=now+1/self.target_fps
        self.timer.start(max(1,math.ceil((self.next_frame-time.perf_counter())*1000)))

    def initializeGL(self):
        self.controllers=ControllerRenderer()
        self.context().aboutToBeDestroyed.connect(self.release_models)

    def release_models(self):
        if not self.controllers:return
        self.makeCurrent()
        self.controllers.destroy();self.controllers=None
        self.doneCurrent()

    def fps(self):
        if len(self.frame_times)<2:return 0.
        return (len(self.frame_times)-1)/(self.frame_times[-1]-self.frame_times[0])

    def reset_view(self):
        self.azimuth=.6;self.elevation=.43;self.distance=3.1;self.target=[0,-.12,-.6]
        screen=self.snapshot()['screen']
        if screen['calibrated']:self.target=sub(screen['center'],scale(unit(screen['up']),screen['height']/2+.762))
        self.save_view()

    def save_view(self):
        if self.view_path:
            self.view_path.parent.mkdir(parents=True,exist_ok=True)
            temp=self.view_path.with_suffix('.tmp')
            temp.write_text(json.dumps({**{name:getattr(self,name) for name in ('azimuth','elevation','distance','target','use_wii_models','swap_wii_models')},'orbit_basis':'display'},indent=2))
            temp.replace(self.view_path)

    def mousePressEvent(self,event):self.last_mouse=event.position()
    def mouseReleaseEvent(self,event):self.last_mouse=None;self.save_view()
    def mouseMoveEvent(self,event):
        if self.last_mouse is None:return
        delta=event.position()-self.last_mouse;self.last_mouse=event.position()
        if event.buttons()&Qt.RightButton:
            self.target=add(self.target,add(scale(self.camera_right,-delta.x()*self.distance/700),scale(self.camera_up,delta.y()*self.distance/700)))
        else:
            self.azimuth-=delta.x()*.006;self.elevation=max(-1.3,min(1.3,self.elevation+delta.y()*.006))
    def wheelEvent(self,event):
        self.distance=max(.4,min(12,self.distance*math.exp(-event.angleDelta().y()/1200)));self.save_view()

    def project(self,point):
        offset=sub(point,self.eye);z=dot(offset,self.camera_forward)
        if z<.045:return None
        factor=self.height()/(2*math.tan(math.radians(43)/2))
        return QPointF(self.width()/2+dot(offset,self.camera_right)*factor/z,
                       self.height()/2-dot(offset,self.camera_up)*factor/z)

    def line(self,painter,a,b,color,width=1,dashed=False):
        a=self.project(a);b=self.project(b)
        if a is None or b is None:return
        pen=QPen(QColor(color),width)
        if dashed:pen.setStyle(Qt.DashLine)
        painter.setPen(pen);painter.drawLine(a,b)

    def label(self,painter,position,title,subtitle,color):
        p=self.project(position)
        if p is None:return
        painter.setFont(QFont('Segoe UI',9));metrics=painter.fontMetrics();w=max(metrics.horizontalAdvance(title),metrics.horizontalAdvance(subtitle))+22
        x=p.x()-w/2;y=p.y()-43
        painter.setPen(QPen(QColor('#344255'),1));painter.setBrush(QColor('#e91a2532'));painter.drawRoundedRect(QRectF(x,y,w,39),5,5)
        painter.setPen(QColor(color));painter.drawText(QPointF(x+10,y+15),title)
        painter.setFont(QFont('Segoe UI',8));painter.setPen(QColor('#8f9db0'));painter.drawText(QPointF(x+10,y+30),subtitle)

    def paintGL(self):
        started=time.perf_counter();self.frame_times.append(started)
        painter=QPainter(self);painter.setRenderHint(QPainter.Antialiasing);painter.fillRect(self.rect(),QColor('#101720'))
        frame=self.snapshot();screen=frame['screen'];pointer=frame['pointer'];head=frame['head'];identity=[0,0,0,1]
        direction,world_up=orbit_direction(screen,self.azimuth,self.elevation)
        self.eye=add(self.target,scale(direction,self.distance));self.camera_forward=unit(sub(self.target,self.eye))
        self.camera_right=unit(cross(self.camera_forward,world_up));self.camera_up=unit(cross(self.camera_right,self.camera_forward))
        reference=frame.get('reference',head);base=screen['base_pose'];bp=base['p'];bq=base['q']
        base_point=lambda point:add(bp,rotate(bq,point))
        floor_origin,floor_right,floor_depth=floor_basis(screen)
        floor_point=lambda x,z:add(floor_origin,add(scale(floor_right,x),scale(floor_depth,z)))
        for i in range(-16,17):
            value=i*.25;color='#35475a' if i==0 else '#243244'
            self.line(painter,floor_point(value,-4),floor_point(value,4),color)
            self.line(painter,floor_point(-4,value),floor_point(4,value),color)
        center=screen['center'];r=scale(screen['right'],screen['width']/2);u=scale(screen['up'],screen['height']/2)
        corners=[add(sub(center,r),u),add(add(center,r),u),sub(add(center,r),u),sub(sub(center,r),u)]
        queue=[]
        hand_models=[]
        roles=wii_model_names(self.swap_wii_models) if self.use_wii_models else (None,None)
        for index,hand in enumerate(frame['hands']):
            active=frame['live'] and hand.get('active',False);tracked=active and hand['flags']&15==15
            if active and hand['flags']&3==3 and head['flags']&3==3:
                pose=relative_pose(reference,display_pose(frame,index,self.use_wii_models,roles[index]));p=pose['p'];q=pose['q']
            else:p=[.38 if index==0 else -.38,-.22,-.68];q=WII_IN_GRIP if self.use_wii_models else identity
            color=('#6be5be' if index==0 else '#82acff') if tracked else '#718397'
            hand_models.append((p,q,color,tracked))
        models=hand_models+[(bp,bq,'',True)]
        painter.beginNativePainting();self.controllers.draw(self,queue,models,self.use_wii_models,self.swap_wii_models,'base');painter.endNativePainting()
        ratio=4/3 if screen['display_mode']=='4:3' else 16/9
        image_width=min(screen['width'],screen['height']*ratio);image_height=image_width/ratio
        r=scale(screen['right'],image_width/2);u=scale(screen['up'],image_height/2)
        image_corners=[add(sub(center,r),u),add(add(center,r),u),sub(add(center,r),u),sub(sub(center,r),u)]
        # Scene overlays: sensor bar, then frusta. The display face is painted last over both.
        top=add(center,scale(screen['up'],screen['height']*(.5 if screen['bar']=='above' else -.5)))
        top=add(top,scale(screen['up'],.045 if screen['bar']=='above' else -.045))
        self.line(painter,sub(top,scale(screen['right'],.13)),add(top,scale(screen['right'],.13)),'#e2b571',6)
        for side in [-1,1]:
            p=self.project(add(top,scale(screen['right'],side*.1)))
            if p:painter.setPen(Qt.NoPen);painter.setBrush(QColor('#ffe3a6'));painter.drawEllipse(p,3,3)
        if self.show_frusta:
            for sx in [-1,1]:
                for sy in [-1,1]:
                    origin=[sx*.08,sy*.035,-.08]
                    direction=unit([sx*.5,sy*.28,-1]);right=unit(cross(direction,[0,1,0]));up=unit(cross(right,direction))
                    end=add(origin,scale(direction,self.range));w=self.range*.8;h=self.range*.65
                    frustum=[add(end,add(scale(right,x*w),scale(up,y*h))) for x,y in [(-1,-1),(1,-1),(1,1),(-1,1)]]
                    for i in range(4):self.line(painter,base_point(origin),base_point(frustum[i]),'#756647');self.line(painter,base_point(frustum[i]),base_point(frustum[(i+1)%4]),'#756647')
                    marker=[add(origin,p) for p in [[-.035,.025,-.08],[0,.07,-.08],[.035,.025,-.08]]]
                    for i in range(3):self.line(painter,base_point(marker[i]),base_point(marker[(i+1)%3]),'#ddba76',1.4)
        hit=None
        try:
            origin,direction=relative_ray(frame)
            hit=pointer.get('point');end=hit or add(origin,scale(direction,2.5))
            self.line(painter,origin,end,'#6be5be' if pointer.get('valid') else '#c48b69',2)
        except ValueError:pass
        projected=[self.project(point) for point in corners]
        if all(point is not None for point in projected):
            painter.setPen(Qt.NoPen);painter.setBrush(QColor('#172d40'));painter.drawPolygon(QPolygonF(projected))
        painter.beginNativePainting();self.controllers.draw(self,queue,models,self.use_wii_models,self.swap_wii_models,'controllers');painter.endNativePainting()
        for i in range(4):self.line(painter,image_corners[i],image_corners[(i+1)%4],'#8faac2')
        self.line(painter,sub(center,r),add(center,r),'#8faac2',1.5)
        self.line(painter,sub(center,u),add(center,u),'#8faac2',1.5)
        if hit:
            roll=pointer.get('roll',0.)*math.pi;c=math.cos(roll);s=math.sin(roll)
            axes=[add(scale(screen['right'],c),scale(screen['up'],s)),sub(scale(screen['up'],c),scale(screen['right'],s))]
            for axis in axes:self.line(painter,sub(hit,scale(axis,.025)),add(hit,scale(axis,.025)),'#83f2c9',2)
        roles=wii_model_names(self.swap_wii_models) if self.use_wii_models else ('Wii Remote','Nunchuk')
        for index,(p,q,color,tracked) in enumerate(hand_models):
            role={'motionplus':'MotionPlus Remote','nunchuk':'Nunchuk'}.get(roles[index],roles[index])
            self.label(painter,add(p,[0,.19,0]),'Right Touch' if index==0 else 'Left Touch',role,color)
        self.label(painter,add(bp,[0,.19,0]),'Quest 2','Base locked' if screen['base_locked'] else 'Base unlocked · movable','#d9e1ec')
        self.label(painter,add(center,scale(screen['up'],screen['height']/2+.18)),'Virtual screen',f"{screen['width']:.2f} × {screen['height']:.2f} m · "+('calibrated' if screen['calibrated'] else 'not calibrated'),'#deb978')
        painter.setFont(QFont('Segoe UI',9));painter.setPen(QColor('#91a1b5'))
        painter.drawText(18,26,'Live OpenXR poses' if frame['live'] else 'No focused Quest session · reference layout')
        painter.drawText(18,self.height()-17,'Drag: orbit    Right-drag: pan    Wheel: zoom    Grid: 0.25 m'+(' · Monitor bottom: 5 ft above floor' if screen['calibrated'] else ''))
        # A small orthographic view makes screen hits visible from every orbit angle.
        iw=190;ih=107;ix=self.width()-iw-18;iy=18
        painter.fillRect(ix,iy,iw,ih,QColor('#dd172332'));painter.setPen(QPen(QColor('#59728b'),1));painter.setBrush(Qt.NoBrush);painter.drawRect(ix,iy,iw,ih)
        gw=iw*image_width/screen['width'];gh=ih*image_height/screen['height'];gx=ix+(iw-gw)/2;gy=iy+(ih-gh)/2
        painter.setPen(QPen(QColor('#8faac2'),1));painter.drawRect(QRectF(gx,gy,gw,gh))
        painter.drawLine(QPointF(gx,iy+ih/2),QPointF(gx+gw,iy+ih/2))
        painter.drawLine(QPointF(ix+iw/2,gy),QPointF(ix+iw/2,gy+gh))
        if pointer.get('valid'):
            x=ix+pointer['u']*iw;y=iy+pointer['v']*ih
            painter.save();painter.translate(x,y);painter.rotate(-pointer.get('roll',0.)*180)
            painter.setPen(QPen(QColor('#6be5be'),1.8))
            painter.drawLine(QPointF(-7,0),QPointF(7,0));painter.drawLine(QPointF(0,-7),QPointF(0,7))
            painter.restore()
        painter.setFont(QFont('Segoe UI',8));painter.setPen(QColor('#9cabbc'));painter.drawText(ix,iy+ih+17,'Virtual IR · '+screen['display_mode']+' · '+('on screen' if pointer.get('valid') else 'no hit'))
        painter.end();self.paint_cost.append((time.perf_counter()-started)*1000)


class TrackingCameraView(QWidget):
    """Lightweight 220-degree controller-visibility view from the base front."""
    BOUNDS={
        'right':((-.053,.039),(-.070,.065),(.020,.133)),
        'left':((-.039,.053),(-.070,.065),(.020,.133)),
        'motionplus':((-.022,.022),(-.019,.019),(-.164,0.)),
        'nunchuk':((-.017,.017),(-.021,.028),(-.076,.034)),
    }
    FACES=((0,1,3,2),(4,6,7,5),(0,4,5,1),(2,3,7,6),(0,2,6,4),(1,5,7,3))
    def __init__(self,snapshot,parent=None):
        super().__init__(parent)
        self.snapshot=snapshot;self.use_wii_models=False;self.swap_wii_models=False
        self.setFixedSize(212,212);self.setAccessibleName('3D controller tracking camera preview')
        self.setToolTip('Forward-facing 220° equidistant fisheye from the center of the Quest base front face.')
        self.timer=QTimer(self);self.timer.setTimerType(Qt.PreciseTimer);self.timer.timeout.connect(self.update);self.timer.start(14)

    def release_models(self):pass

    def project_fisheye(self,point,eye,base_q):
        camera=inverse_rotate(base_q,sub(point,eye));lateral=math.hypot(camera[0],camera[1])
        angle=math.atan2(lateral,-camera[2])
        if angle>math.radians(110):return None
        radius=angle/math.radians(110)*(min(self.width(),self.height())/2-2)
        return QPointF(self.width()/2+(camera[0]/lateral*radius if lateral else 0),
                       self.height()/2-(camera[1]/lateral*radius if lateral else 0))

    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.Antialiasing);painter.fillRect(self.rect(),QColor('#080d13'))
        frame=self.snapshot();screen=frame['screen'];base=screen['base_pose'];bp=base['p'];bq=base['q'];reference=frame.get('reference',frame['head'])
        eye=add(bp,rotate(bq,[0,0,-.08]));roles=wii_model_names(self.swap_wii_models) if self.use_wii_models else ('right','left')
        for index,hand in enumerate(frame['hands']):
            tracked=frame['live'] and hand.get('active',False) and hand.get('flags',0)&15==15 and frame['head'].get('flags',0)&3==3
            if not tracked:continue
            role=roles[index];pose=relative_pose(reference,display_pose(frame,index,self.use_wii_models,role if self.use_wii_models else None))
            bounds=self.BOUNDS[role];corners=[]
            for x in bounds[0]:
                for y in bounds[1]:
                    for z in bounds[2]:corners.append(add(pose['p'],rotate(pose['q'],[x,y,z])))
            faces=[]
            for face in self.FACES:
                points=[self.project_fisheye(corners[i],eye,bq) for i in face]
                if all(point is not None for point in points):faces.append((sum(length(sub(corners[i],eye)) for i in face),points))
            color=QColor('#6be5be' if index==0 else '#82acff');fill=QColor(color);fill.setAlpha(90)
            painter.setPen(QPen(color,1));painter.setBrush(fill)
            for _,points in sorted(faces,reverse=True,key=lambda item:item[0]):painter.drawPolygon(QPolygonF(points))
            center=self.project_fisheye(pose['p'],eye,bq)
            if center:painter.setPen(color);painter.drawText(center+QPointF(4,-4),'R' if index==0 else 'L')
        painter.setBrush(Qt.NoBrush);painter.setPen(QPen(QColor('#8faac2'),1));painter.drawEllipse(QRectF(1,1,self.width()-2,self.height()-2))
        painter.setPen(QPen(QColor('#425c72'),1));painter.drawLine(1,self.height()//2,self.width()-1,self.height()//2);painter.drawLine(self.width()//2,1,self.width()//2,self.height()-1)
        painter.setFont(QFont('Segoe UI',8));painter.setPen(QColor('#aab9c8'));painter.drawText(8,15,'Tracking camera · 220°')
        painter.setPen(QColor('#73869b'));painter.drawText(8,self.height()-8,'Equidistant fisheye · 1:1')
        painter.end()
