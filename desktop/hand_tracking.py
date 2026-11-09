"""OpenXR hand joints -> the existing Dolphin button inputs (wire order: right, left)."""
import math

from motion import rotate


class Gestures:
    def __init__(self):
        self.pinches = [False]*4
        self.anchor = None
        self.right = [1., 0., 0.]
        self.dpad = [0, 0]

    def update(self, hand, slot, head):
        hand.update(buttons=0, trigger=0., squeeze=0., stick=[0., 0.], dpad=[0, 0], gesture='Open hand')
        joints = hand.get('joints', [])
        if not hand['active'] or hand['flags'] & 15 != 15 or not joints:
            self.__init__()
            hand.update(active=False, gesture='Hand not tracked')
            return
        distance = lambda a, b: math.dist(joints[a], joints[b])
        # Scale thresholds by palm width to accommodate different hand sizes.
        scale = max(.04, distance(7, 22))
        curled = all(distance(tip, 1) < distance(pip, 1)*1.1 for tip, pip in ((10,8),(15,13),(20,18),(25,23)))
        thumb = [a-b for a,b in zip(joints[5],joints[3])]
        thumb_length = math.sqrt(sum(v*v for v in thumb))
        thumb_extended = distance(5, 7) > scale*.8
        thumbs_up = curled and thumb_extended and thumb_length > scale*.5 and thumb[1] > thumb_length*.7
        fist = curled and not thumb_extended
        if fist:
            self.pinches = [False]*4
            palm = joints[0]
            if self.anchor is None:
                self.anchor = palm[:]
                right = rotate(head['q'], [1.,0.,0.])
                length = math.hypot(right[0], right[2])
                self.right = [right[0]/length,0.,right[2]/length] if length > .1 else [1.,0.,0.]
            delta = [a-b for a,b in zip(palm,self.anchor)]
            for axis, value in enumerate((sum(a*b for a,b in zip(delta,self.right)),delta[1])):
                threshold = .025 if self.dpad[axis] and value*self.dpad[axis] > 0 else .04
                self.dpad[axis] = (1 if value > 0 else -1) if abs(value) > threshold else 0
            hand.update(dpad=self.dpad[:], gesture='Fist · D-pad')
            return
        self.anchor = None
        self.dpad = [0, 0]
        if thumbs_up:
            self.pinches = [False]*4
            if slot == 0:hand.update(buttons=4, gesture='Thumbs up · +')
            return
        for i, tip in enumerate((10,15,20,25)):
            self.pinches[i] = distance(5,tip) < scale*(.45 if self.pinches[i] else .30)
        index, middle, ring, pinky = self.pinches
        if slot == 0:
            hand.update(buttons=(2 if index else 0)|(1 if ring else 0), trigger=float(middle), squeeze=float(pinky))
            names = [name for name, down in zip(('A','B','1','2'),self.pinches) if down]
        else:
            hand.update(squeeze=float(index), trigger=float(middle))
            names = [name for name, down in zip(('C','Z'),self.pinches) if down]
        if names:hand['gesture'] = ' + '.join(names)
