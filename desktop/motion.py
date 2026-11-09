"""OpenXR aim-space motion -> Dolphin's DSU accelerometer and gyro conventions."""
import math

G = 9.80665


def multiply(a, b):
    x,y,z,w=a;X,Y,Z,W=b
    return [w*X+x*W+y*Z-z*Y,w*Y-x*Z+y*W+z*X,w*Z+x*Y-y*X+z*W,w*W-x*X-y*Y-z*Z]


def rotate(q, v):
    x, y, z, w = q
    tx, ty, tz = 2 * (y*v[2]-z*v[1]), 2 * (z*v[0]-x*v[2]), 2 * (x*v[1]-y*v[0])
    return [v[0]+w*tx+y*tz-z*ty, v[1]+w*ty+z*tx-x*tz, v[2]+w*tz+x*ty-y*tx]


def inverse_rotate(q, v):
    return rotate([-q[0], -q[1], -q[2], q[3]], v)


class Motion:
    def __init__(self):
        self.previous = None
        self.acceleration = [0., 0., 0.]

    def update(self, hand, timestamp):
        q = hand['q']
        flags = hand.get('flags', 0)
        orientation = bool(flags & 1) and bool(flags & 4) and hand.get('active', False)
        position = bool(flags & 2) and bool(flags & 8) and hand.get('lv', False) and orientation
        velocity = hand.get('v', [0., 0., 0.])
        dt = timestamp - self.previous[0] if self.previous else 0
        if position and self.previous and self.previous[2] and .002 <= dt <= .1:
            alpha = 1 - math.exp(-dt / .035)
            for i in range(3):
                a = max(-8*G, min(8*G, (velocity[i]-self.previous[1][i])/dt))
                self.acceleration[i] += alpha * (a-self.acceleration[i])
        else:
            self.acceleration = [0., 0., 0.]
        self.previous = (timestamp, velocity[:], position)
        if not orientation:
            return {'accel': [0., -1., 0.], 'gyro': [0., 0., 0.], 'tracked': False}
        specific_force = inverse_rotate(q, [self.acceleration[0], self.acceleration[1]+G, self.acceleration[2]])
        omega = inverse_rotate(q, hand.get('w', [0., 0., 0.])) if hand.get('av', False) else [0., 0., 0.]
        return {'accel': [-f/G for f in specific_force],
                'gyro': [math.degrees(omega[0]), -math.degrees(omega[1]), -math.degrees(omega[2])],
                'tracked': position, 'orientation': orientation}
