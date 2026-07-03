"""FObj keyframe interpreter — ported from Melee sysdolphin baselib/fobj.c.

Used by gen_moving_gct.py to simulate what the game plays when validating
moving-target animation streams before packing them into the installer blob.
"""
import struct
import math

OP_CON, OP_LIN, OP_SPL0, OP_SPL, OP_SLP, OP_KEY = 1, 2, 3, 4, 5, 6
FRAC_FLOAT, FRAC_S16, FRAC_U16, FRAC_S8, FRAC_U8 = 0 << 5, 1 << 5, 2 << 5, 3 << 5, 4 << 5


def _helmite(fterm, time, p0, p1, d0, d1):
    _1_T2 = time * time
    t2 = fterm * fterm
    t2_T = _1_T2 * fterm
    t3_T2 = t2 * (_1_T2 * time)
    _2t3 = 2.0 * t3_T2 * fterm
    _3t2 = 3.0 * _1_T2 * t2
    return (
        d1 * (t3_T2 - t2_T)
        + (
            d0 * (time + ((t3_T2 - t2_T) - t2_T))
            + (p0 * (1.0 + (_2t3 - _3t2)) + p1 * (-_2t3 + _3t2))
        )
    )


class FObj:
    """Single-track FObj state machine (one animation channel)."""

    def __init__(self, data, frac_value, frac_slope, obj_type, startframe):
        self.d = data
        self.pos = 0
        self.length = len(data)
        self.fv = frac_value
        self.fs = frac_slope
        self.obj_type = obj_type
        self.startframe = startframe
        self.flags = self.op = self.op_intrp = self.nb_pack = 0
        self.fterm = 0
        self.p0 = self.p1 = self.d0 = self.d1 = 0.0
        self.time = 0.0
        self.out = None

    def _pf(self, frac):
        d = self.d
        if frac == FRAC_FLOAT:
            v = struct.unpack_from("<f", bytes(d[self.pos : self.pos + 4]))[0]
            self.pos += 4
            return v
        denom = 1 << (frac & 0x1F)
        k = frac & 0xE0
        if k == FRAC_S8:
            n = struct.unpack_from("b", bytes([d[self.pos]]))[0]
            self.pos += 1
        elif k == FRAC_U8:
            n = d[self.pos]
            self.pos += 1
        elif k == FRAC_S16:
            n = struct.unpack_from("<h", bytes(d[self.pos : self.pos + 2]))[0]
            self.pos += 2
        elif k == FRAC_U16:
            n = struct.unpack_from("<H", bytes(d[self.pos : self.pos + 2]))[0]
            self.pos += 2
        else:
            return 0.0
        return n / denom

    def _packinfo(self):
        d = self.d[self.pos]
        self.pos += 1
        nb = ((d >> 4) & 7) + 1
        shift = 3
        if not (d & 0x80):
            return nb
        while True:
            d = self.d[self.pos]
            self.pos += 1
            nb += (d & 0x7F) << shift
            shift += 7
            if not (d & 0x80):
                break
        return nb

    def _wait(self):
        w = 0
        shift = 0
        while True:
            d = self.d[self.pos]
            self.pos += 1
            w |= (d & 0x7F) << shift
            shift += 7
            if not (d & 0x80):
                break
        return w

    def setstate(self, s):
        self.flags = (s & 0xF) | (self.flags & 0xF0)
        return s

    def getstate(self):
        return self.flags & 0xF

    def req(self, startframe=0.0):
        self.pos = 0
        self.time = float(self.startframe) + startframe
        self.op = self.op_intrp = self.nb_pack = 0
        self.flags &= ~0x40
        self.fterm = 0
        self.p0 = self.p1 = self.d0 = self.d1 = 0.0
        self.setstate(1)

    def _launch(self):
        if self.flags & 0x40:
            self.op_intrp = self.op
            self.flags &= ~0x40
            self.flags |= 0x80
            self.p0 = self.p1

    def _loaddata(self):
        if self.pos >= self.length:
            return 6
        self.op_intrp = self.op
        if self.nb_pack == 0:
            self.op = self.d[self.pos] & 0xF
            self.nb_pack = self._packinfo()
        self.nb_pack -= 1
        st = self.getstate()
        if self.op in (OP_CON, OP_LIN):
            self.p0 = self.p1
            self.p1 = self._pf(self.fv)
            if self.op_intrp != 5:
                self.d0 = self.d1
                self.d1 = 0.0
            return self.setstate(3 if st == 1 else 4)
        if self.op == OP_SPL0:
            self.p0 = self.p1
            self.d0 = self.d1
            self.p1 = self._pf(self.fv)
            self.d1 = 0.0
            return self.setstate(3 if st == 1 else 4)
        if self.op == OP_SPL:
            self.p0 = self.p1
            self.p1 = self._pf(self.fv)
            self.d0 = self.d1
            self.d1 = self._pf(self.fs)
            return self.setstate(3 if st == 1 else 4)
        if self.op == OP_SLP:
            self.d0 = self.d1
            self.d1 = self._pf(self.fs)
            return self.getstate()
        if self.op == OP_KEY:
            self._launch()
            self.p1 = self._pf(self.fv)
            self.flags |= 0x40
            return self.setstate(3 if st == 1 else 4)
        return 0

    def _loadwait(self):
        if self.pos >= self.length:
            return 6
        self.fterm = self._wait()
        self.flags |= 0x20
        return self.setstate(2)

    def _update(self):
        oi = self.op_intrp
        fv = None
        if oi == OP_KEY:
            if self.flags & 0x80:
                fv = self.p0
                self.flags &= 0xFFFFFF7F
            else:
                return
        elif oi == OP_CON:
            fv = self.p1 if self.time >= self.fterm else self.p0
        elif oi == OP_LIN:
            if self.flags & 0x20:
                self.flags &= 0xFFFFFFDF
                if self.fterm != 0:
                    self.d0 = (self.p1 - self.p0) / self.fterm
                else:
                    self.d0 = 0
                    self.p0 = self.p1
            fv = self.d0 * self.time + self.p0
        elif oi in (OP_SPL0, OP_SPL, OP_SLP):
            fv = (
                _helmite(1.0 / self.fterm, self.time, self.p0, self.p1, self.d0, self.d1)
                if self.fterm != 0
                else self.p1
            )
        if fv is not None:
            self.out = fv

    def interpret(self, rate):
        fterm = 0.0
        state = self.getstate()
        if state == 0:
            return
        self.time += rate
        if self.time < 0.0:
            return
        while True:
            if state == 6:
                self.time += fterm
                self._launch()
                self._update()
                return
            elif state in (1, 2):
                state = self._loaddata()
            elif state == 3:
                if self.flags & 0x80:
                    self._update()
                state = self._loadwait()
            elif state == 4:
                if self.fterm <= self.time:
                    fterm = self.fterm
                    self.time -= self.fterm
                    self.setstate(3)
                    state = 3
                else:
                    self._update()
                    self.setstate(5)
                    return
            elif state == 5:
                self.setstate(4)
                state = 4
            elif state == 0:
                return
