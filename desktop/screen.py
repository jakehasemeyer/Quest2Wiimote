"""A calibrated screen and independently lockable base in a fixed workspace."""
import json
import math
from pathlib import Path

from motion import rotate, inverse_rotate, multiply


def add(a, b): return [x+y for x,y in zip(a,b)]
def sub(a, b): return [x-y for x,y in zip(a,b)]
def scale(a, n): return [x*n for x in a]
def dot(a, b): return sum(x*y for x,y in zip(a,b))
def cross(a, b): return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]
def length(a): return math.sqrt(dot(a,a))
def unit(a):
    n=length(a)
    if n < 1e-7: raise ValueError('Calibration points must be distinct and not on one line.')
    return scale(a,1/n)


def relative_pose(reference, pose):
    q=reference['q']
    return dict(p=inverse_rotate(q,sub(pose['p'],reference['p'])),q=multiply([-q[0],-q[1],-q[2],q[3]],pose['q']))


def plane_orientation(right,up):
    normal=cross(right,up)
    m=[[right[i],up[i],normal[i]] for i in range(3)];trace=sum(m[i][i] for i in range(3))
    if trace>0:
        s=math.sqrt(trace+1)*2
        return [(m[2][1]-m[1][2])/s,(m[0][2]-m[2][0])/s,(m[1][0]-m[0][1])/s,s/4]
    i=max(range(3),key=lambda i:m[i][i]);j=(i+1)%3;k=(j+1)%3
    s=math.sqrt(1+m[i][i]-m[j][j]-m[k][k])*2;q=[0.,0.,0.,0.]
    q[i]=s/4;q[j]=(m[j][i]+m[i][j])/s;q[k]=(m[k][i]+m[i][k])/s;q[3]=(m[k][j]-m[j][k])/s
    return q


# SteamVR Quest 2 right controller: inverse(openxr_grip) * base, in meters.
HANDLE_IN_GRIP=dict(p=[.0104,.015119801291012548,.04468949099182487],
                    q=[0.,.9845031799744367,-.1753667260919871,0.])


def pointing_pose(frame,mode=None):
    hand=frame['hands'][0]
    mode=mode or frame.get('screen',{}).get('ray_mode','face')
    if mode=='face' or frame.get('hand_tracking'):return hand
    if mode!='handle':raise ValueError('Point from face buttons or the handle bottom.')
    grip=hand.get('grip')
    if not grip or grip.get('flags',0)&15!=15:
        raise ValueError('Handle pointing needs a tracked grip pose from the updated Quest app.')
    return dict(p=add(grip['p'],rotate(grip['q'],HANDLE_IN_GRIP['p'])),
                q=multiply(grip['q'],multiply(HANDLE_IN_GRIP['q'],[0,1,0,0])))


def relative_ray(frame, reference=None, mode=None):
    hand, head = frame['hands'][0], frame['head']
    if not frame.get('live',frame.get('focused',False)) or not hand.get('active') or hand.get('flags',0)&15 != 15 or head.get('flags',0)&3 != 3:
        raise ValueError('The right controller needs live position and orientation tracking.')
    reference=reference or frame.get('reference') or head
    pose=pointing_pose(frame,mode)
    origin=inverse_rotate(reference['q'],sub(pose['p'],reference['p']))
    direction=unit(inverse_rotate(reference['q'],rotate(pose['q'],[0,0,-1])))
    return origin,direction


class VirtualScreen:
    def __init__(self, path=None):
        self.path=Path(path) if path else None
        self.reference=None
        self.depth_reference=None
        self.data=dict(enabled=True,calibrated=False,width=1.2,height=.675,distance=1.5,
                       center=[0,0,-1.5],right=[1,0,0],up=[0,1,0],bar='above',method='none',
                       base_locked=True,base_pose=dict(p=[0,0,0],q=[0,0,0,1]),
                       dolphin_bounds=[0.,0.,1.,1.],dolphin_aligned=False,display_mode='16:9',ray_mode='face')
        if self.path and self.path.exists():
            saved=json.loads(self.path.read_text())
            for name in self.data:
                if name in saved: self.data[name]=saved[name]

    def observe(self, frame):
        head=frame['head']
        if not frame.get('live',frame.get('focused',False)) or head.get('flags',0)&15 != 15:return
        if self.reference is None:
            base=self.data['base_pose'];q=base['q']
            rotation=multiply(head['q'],[-q[0],-q[1],-q[2],q[3]])
            self.reference=dict(p=sub(head['p'],rotate(rotation,base['p'])),q=rotation)
        if not self.data['base_locked']:
            self.data['base_pose']=relative_pose(self.reference,head)

    def lock_base(self, locked, frame):
        if not frame.get('live',frame.get('focused',False)) or frame['head'].get('flags',0)&15 != 15:
            raise ValueError('The headset needs live position and orientation tracking to change its base lock.')
        self.observe(frame)
        self.data.update(base_locked=bool(locked),base_pose=relative_pose(self.reference,frame['head']))
        self.save()

    def save(self):
        if self.path:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            temp=self.path.with_suffix('.tmp')
            temp.write_text(json.dumps(self.data,indent=2))
            temp.replace(self.path)

    def settings(self, width, height, distance, enabled=True, bar='above'):
        values=[float(width),float(height),float(distance)]
        if not all(math.isfinite(x) and .08 <= x <= 10 for x in values):
            raise ValueError('Screen dimensions and distance must be between 0.08 and 10 meters.')
        self.data.update(width=values[0],height=values[1],distance=values[2],enabled=bool(enabled),bar='below' if bar=='below' else 'above')
        self.save()

    def calibrate_center(self, frame):
        self.depth_reference=None
        self.observe(frame)
        reference=self.reference or frame.get('reference') or frame['head']
        origin,forward=relative_ray(frame,reference,self.data['ray_mode'])
        up=inverse_rotate(reference['q'],[0,1,0])
        right=unit(cross(forward,up))
        up=unit(cross(right,forward))
        self.data.update(center=add(origin,scale(forward,self.data['distance'])),right=right,up=up,calibrated=True,method='center aim')
        self.save()

    def calibrate_corners(self, top_left, top_right, bottom_left):
        horizontal=sub(top_right,top_left)
        vertical=sub(top_left,bottom_left)
        width=length(horizontal);right=unit(horizontal)
        if width < .08 or length(vertical)<.08: raise ValueError('Corners are too close together.')
        if abs(dot(right,unit(vertical)))>.35: raise ValueError('Capture the top-left, top-right and bottom-left corners of a rectangle.')
        vertical=sub(vertical,scale(right,dot(vertical,right)))
        height=length(vertical);up=unit(vertical)
        center=add(top_left,sub(scale(right,width/2),scale(up,height/2)))
        self.data.update(center=center,right=right,up=up,width=width,height=height,calibrated=True,method='three corners')
        self.depth_reference=None
        self.save()

    def corners(self):
        d=self.data;c=d['center'];r=scale(d['right'],d['width']/2);u=scale(d['up'],d['height']/2)
        return [add(sub(c,r),u),add(add(c,r),u),sub(add(c,r),u),sub(sub(c,r),u)]

    def align_dolphin(self,left,right,top,bottom):
        values=[float(v) for v in (left,top,right,bottom)]
        left,top,right,bottom=values
        if not all(math.isfinite(v) and 0<=v<=1 for v in values) or right-left<.05 or bottom-top<.05:
            raise ValueError('Capture the first point where the Dolphin cursor touches each edge: left, right, top, bottom.')
        self.data.update(dolphin_bounds=values,dolphin_aligned=True)
        self.save()

    def reset_dolphin(self):
        self.data.update(dolphin_bounds=[0.,0.,1.,1.],dolphin_aligned=False)
        self.save()

    def set_display_mode(self,mode):
        if mode not in ('4:3','16:9'):raise ValueError('Wii display mode must be 4:3 or 16:9.')
        self.data['display_mode']=mode
        self.save()

    def set_ray_mode(self,mode):
        if mode not in ('face','handle'):raise ValueError('Point from face buttons or the handle bottom.')
        self.data['ray_mode']=mode;self.depth_reference=None
        self.save()

    def image_size(self):
        ratio=4/3 if self.data['display_mode']=='4:3' else 16/9
        width=min(self.data['width'],self.data['height']*ratio)
        return width,width/ratio

    def pointer(self, frame, allow_outside=False):
        result=dict(valid=False,u=.5,v=.5,point=None,reason='Calibrate the virtual screen')
        if not self.data['enabled']: result['reason']='Virtual IR disabled';return result
        if not self.data['calibrated']: return result
        try: origin,direction=relative_ray(frame,self.reference,self.data['ray_mode'])
        except ValueError as e: result['reason']=str(e);return result
        d=self.data;normal=cross(d['right'],d['up']);denom=dot(direction,normal)
        if abs(denom)<1e-7: result['reason']='Aim is parallel to the screen';return result
        distance=dot(sub(d['center'],origin),normal)/denom
        if distance<=0: result['reason']='Screen is behind the aim ray';return result
        point=add(origin,scale(direction,distance));offset=sub(point,d['center'])
        u=.5+dot(offset,d['right'])/d['width'];v=.5-dot(offset,d['up'])/d['height']
        width,height=self.image_size()
        image_u=.5+dot(offset,d['right'])/width;image_v=.5-dot(offset,d['up'])/height
        on_screen=0<=image_u<=1 and 0<=image_v<=1
        screen_distance=abs(dot(sub(d['center'],origin),unit(normal)))
        if self.depth_reference is None and on_screen and screen_distance>.2 and not frame.get('calibrating'):
            self.depth_reference=screen_distance
        depth=max(-.9,min(.9,2*(1-screen_distance/self.depth_reference))) if self.depth_reference else 0.
        reference=self.reference or frame.get('reference') or frame['head']
        hand=pointing_pose(frame,self.data['ray_mode'])
        right=inverse_rotate(reference['q'],rotate(hand['q'],[1,0,0]))
        up=inverse_rotate(reference['q'],rotate(hand['q'],[0,1,0]))
        roll=math.atan2(dot(right,d['up']),dot(up,d['up']))/math.pi
        left,top,right,bottom=d['dolphin_bounds']
        result.update(valid=on_screen or allow_outside,u=u,v=v,point=point,image_u=image_u,image_v=image_v,dolphin_u=left+image_u*(right-left),dolphin_v=top+image_v*(bottom-top),reason='On screen' if on_screen else 'Outside picture · alignment allowed' if allow_outside else 'Outside picture')
        result.update(depth=0. if frame.get('calibrating') else depth,roll=0. if frame.get('calibrating') else roll,screen_distance=screen_distance)
        return result
