#!/usr/bin/env python3
"""Generate the all-stages keyframe moving-target GCT.

For every ten-target Target Test stage: randomize all 10 target positions
within bttrandomizer's bounds/exclusions, pick 3-5 of them as movers, author
looping FObj keyframe streams for those (a variety of patterns: elevators,
sweeps, diagonals, circles, park-and-dash), validate every swept frame
against the stage bounds, and pack everything into the data blob consumed by
gecko/mover_install.s (see its BLOB CONTRACT comment).

The streams are validated by round-tripping through the FObj VM ported from
the decomp (extract_stage.py) plus a simulation of the AObj loop-rewind, so
what is checked is what the game will actually play. A final re-decode pass
parses the packed blob exactly like the installer does and re-validates,
catching packing bugs.

Usage: python3 gen_moving_gct.py [--seed N] [--out FILE]
"""
import argparse
import json
import math
import os
import random
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SHARED = os.path.join(REPO, "shared")   # extract_stage.py and other shared tooling
DATA = os.path.join(REPO, "data")       # extracted stage .json dumps
sys.path.insert(0, HERE)
sys.path.insert(0, SHARED)
from extract_stage import FObj, OP_LIN, OP_SPL0, FRAC_S8, FRAC_S16

# site stage index -> (name, internal ground id, GrT*.json basename)
STAGES = [
    ("DrMario",     0x2C, "GrTDr"), ("Mario",       0x28, "GrTMr"),
    ("Luigi",       0x33, "GrTLg"), ("Bowser",      0x31, "GrTKp"),
    ("Peach",       0x37, "GrTPe"), ("Yoshi",       0x3D, "GrTYs"),
    ("DK",          0x2B, "GrTDk"), ("CFalcon",     0x29, "GrTCa"),
    ("Ganondorf",   0x41, "GrTGn"), ("Falco",       0x2D, "GrTFc"),
    ("Fox",         0x2E, "GrTFx"), ("Ness",        0x36, "GrTNs"),
    ("IceClimbers", 0x2F, "GrTIc"), ("Kirby",       0x30, "GrTKb"),
    ("Samus",       0x3B, "GrTSs"), ("Zelda",       0x3E, "GrTZd"),
    ("Link",        0x32, "GrTLk"), ("YLink",       0x2A, "GrTCl"),
    ("Pichu",       0x38, "GrTPc"), ("Pikachu",     0x39, "GrTPk"),
    ("Jigglypuff",  0x3A, "GrTPr"), ("Mewtwo",      0x35, "GrTMt"),
    ("GameWatch",   0x3F, "GrTGw"), ("Marth",       0x34, "GrTMs"),
    ("Roy",         0x40, "GrTFe"),
]  # Sheik / TSEAK (0x3C) skipped: 3-target debug-only stage; GrTe is a similar leftover

HOOK = 0x801C0A50
BLOB_BUDGET = 3224          # payload bytes; GCT total = 8+8+pad8(blob)+8 <= 3248
CAP_SPEED = 3.5             # u/f sustained (vanilla envelope)
CAP_DASH = 26.0             # u/f inside park-and-dash dash windows (Falco-like)

# ---------------------------------------------------------------- bounds ----

class Bounds:
    def __init__(self, path):
        d = json.load(open(path))

        def at(lst, i):
            return lst[i] if i < len(lst) and lst[i] else None

        self.rect = [at(d["newBounds"], i) or d["bounds"][i] for i in range(26)]
        self.excl = [(at(d["exclusions"], i) or []) + (at(d["newExclusions"], i) or [])
                     for i in range(26)]
        self.exc = [at(d["exceptions"], i) or [] for i in range(26)]

    @staticmethod
    def _within(x, y, vs):
        if len(vs) == 2:                       # rectangle: BL, TR
            return vs[0][0] <= x <= vs[1][0] and vs[0][1] <= y <= vs[1][1]
        inside = False                          # substack point-in-polygon
        j = len(vs) - 1
        for i in range(len(vs)):
            xi, yi = vs[i]; xj, yj = vs[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
            j = i
        return inside

    def valid(self, si, x, y):
        r = self.rect[si]
        if not (r["x1"] <= x <= r["x2"] and r["y1"] <= y <= r["y2"]):
            return False
        for vs in self.excl[si]:
            if self._within(x, y, vs):
                for ex in self.exc[si]:
                    if self._within(x, y, ex):
                        return True
                return False
        return True

# ---------------------------------------------------------- FObj encoding ----

def enc_op(op, count):
    c = count - 1
    out = bytearray()
    rest = c >> 3
    out.append((c & 7) << 4 | op | (0x80 if rest else 0))
    while rest:
        b = rest & 0x7F
        rest >>= 7
        out.append(b | (0x80 if rest else 0))
    return bytes(out)

def enc_wait(w):
    out = bytearray()
    while True:
        b = w & 0x7F
        w >>= 7
        out.append(b | (0x80 if w else 0))
        if not w:
            return bytes(out)

def encode_track(op, pairs):
    """pairs = [(int value, int wait)]; wait semantics: spend `wait` frames
    travelling from this value to the NEXT pair's value (empirically verified
    against the FObj VM; matches MOVING_TARGETS.md). Returns (frac, bytes)."""
    vals = [v for v, _ in pairs]
    if all(-128 <= v <= 127 for v in vals):
        frac, fmt = FRAC_S8 | 0, "<b"
    else:
        frac, fmt = FRAC_S16 | 0, "<h"          # FObj values are LITTLE-endian
    out = bytearray(enc_op(op, len(pairs)))
    for v, w in pairs:
        out += struct.pack(fmt, v)
        out += enc_wait(w)
    return frac, bytes(out)

def sample_loop(stream, frac, endframe, nframes):
    """Frame-by-frame values as the game plays them: FObj VM + the AObj
    LOOP rewind (curr_frame fmod endframe -> FObjReqAnimAll(curr))."""
    fo = FObj(list(stream), frac, frac, 5, 0.0)
    fo.req(0.0)
    cur, last, out = 0.0, None, []
    for _ in range(nframes):
        rate = 1.0
        cur += 1.0
        if endframe <= cur:
            cur = math.fmod(cur, float(endframe))
            fo.req(cur)
            rate = 0.0
        fo.interpret(rate)
        if fo.out is not None:
            last = fo.out
        out.append(last if last is not None else 0.0)
    return out

# -------------------------------------------------------------- patterns ----

# Each pattern returns (tracks, endframe, label) where tracks is a dict
# {5: [(v,w)...]} / {6: ...} with LIN or SPL0 op per track: (op, pairs).
# Loop rule: last pair's value == first pair's value, wait 0; endframe =
# sum of all waits except the last.

# Lever 1 (--short-waits): periods whose leg waits fit a 1-byte varint
# (<=127f), saving ~2 B per raw one-axis mover. Diamond quarters and
# parkdash parks are already 1-byte either way.
P_LEG = {"long": [240, 320, 400, 480], "short": [160, 200, 240, 248]}
P_DIAG = {"long": [320, 400, 480], "short": [200, 240, 248]}
PERIOD_MODE = "long"                # set from --short-waits in main()

def _leg_pairs(op, a, b, half):
    return (op, [(a, half), (b, half), (a, 0)])

def pat_elevator(rng, x, y, amp):
    T = rng.choice(P_LEG[PERIOD_MODE])
    return {6: _leg_pairs(OP_SPL0, y - amp, y + amp, T // 2)}, T, "elevator"

def pat_sweep(rng, x, y, amp):
    T = rng.choice(P_LEG[PERIOD_MODE])
    return {5: _leg_pairs(OP_SPL0, x - amp, x + amp, T // 2)}, T, "sweep"

def pat_diagonal(rng, x, y, amp):
    T = rng.choice(P_DIAG[PERIOD_MODE])
    ay = rng.choice([-1, 1]) * max(8, amp // 2)
    return {5: _leg_pairs(OP_SPL0, x - amp, x + amp, T // 2),
            6: _leg_pairs(OP_SPL0, y - ay, y + ay, T // 2)}, T, "diagonal"

def pat_diamond(rng, x, y, amp):
    T = rng.choice([256, 320, 384, 448])
    q = T // 4
    xs = [(x + amp, q), (x, q), (x - amp, q), (x, q), (x + amp, 0)]
    ys = [(y, q), (y + amp, q), (y, q), (y - amp, q), (y, 0)]
    return {5: (OP_SPL0, xs), 6: (OP_SPL0, ys)}, T, "diamond"

def pat_parkdash(rng, x, y, amp):
    axis = rng.choice([5, 6])
    c = x if axis == 5 else y
    n = 3
    stations = [c - amp, c + rng.randint(-amp // 2, amp // 2), c + amp]
    rng.shuffle(stations)
    park, dash = rng.choice([88, 116]), rng.choice([8, 12])  # park+dash % 4 == 0
    pairs = []
    for s in stations:
        pairs += [(s, park), (s, dash)]
    pairs[-1] = (stations[-1], dash)            # last dash returns to start
    pairs.append((stations[0], 0))
    T = n * (park + dash)
    return {axis: (OP_LIN, pairs)}, T, "parkdash"

PATTERNS = [pat_elevator, pat_sweep, pat_diagonal, pat_diamond, pat_parkdash]
PATTERNS_CHEAP = [pat_elevator, pat_sweep]      # 1-axis s8-friendly fallbacks

# ------------------------------------------------- lever 3: synth mirror ----

# Byte-exact mirror of gecko/mover_synth.inc: the installer builds these
# streams at load time from (static pos, params); the generator validates and
# verifies against the SAME bytes. Values s16 LE, waits fixed 2-byte varints.
SYNTH_KIND = {"el": 0, "sw": 1, "dg": 2, "dm": 3, "ell": 4, "lj": 5, "tl": 5}
SYNTH_LABEL = ["elevator", "sweep", "diagonal", "diamond", "ellipse", "orbit"]

# Unit circle * 16384, N=32 (must byte-match Ltab_ell in mover_synth.inc).
ELL_N = 32
ELL_DIV = 3                     # keypoints stored in 1/8 units (FRAC_S16|3) to
ELL_UNIT = [(int(round(math.cos(2 * math.pi * i / ELL_N) * 16384)),   # keep them
             int(round(math.sin(2 * math.pi * i / ELL_N) * 16384)))   # off the
            for i in range(ELL_N)]                                     # integer grid

def synth_frac(pid):
    """FObj frac_value byte for a synth pattern: ellipse/orbit use a divisor for
    sub-unit precision; the others store whole units."""
    return FRAC_S16 | (ELL_DIV if pid in (4, 5) else 0)

def synth_streams(pid, end4, x0, y0, params):
    def w16(w):
        return bytes([(w & 0x7F) | 0x80, w >> 7])

    def pair(v, w):
        return struct.pack("<h", v) + w16(w)

    def leg3(v0, d, half):
        return bytes([0x23]) + pair(v0, half) + pair(v0 + d, half) + pair(v0, 0)

    half, q = 2 * end4, end4
    if pid == 0:
        return {6: leg3(y0, 2 * params[0], half)}
    if pid == 1:
        return {5: leg3(x0, 2 * params[0], half)}
    if pid == 2:
        return {5: leg3(x0, 2 * params[0], half), 6: leg3(y0, 2 * params[1], half)}
    if pid == 4:                                # ellipse: constant-speed 32-gon
        ax, ay = params[0], params[1]
        leg = end4 >> 3                          # T/32

        def ring(base, amp, col, bias):
            b = bytes([0x82, 0x04])              # LIN x33 packinfo
            for i in range(ELL_N + 1):
                c = ELL_UNIT[i % ELL_N][col]     # entry 32 wraps to 0 (loop close)
                raw = (base << ELL_DIV) + ((amp * (c - bias)) >> (14 - ELL_DIV))
                b += pair(raw, 0 if i == ELL_N else leg)
            return b

        return {5: ring(x0, ax, 0, 16384), 6: ring(y0, ay, 1, 0)}
    if pid == 5:                                # generalized ring: lissajous /
        ax, ay, sx, kx, sy, ky = params         # tilted ellipse / phase offset
        leg = end4 >> 3                          # T/32

        def ring2(base, amp, s, k, col):         # byte-exact mirror of emit_ring2
            def tv(idx):                         # col 0 = cos (X), 1 = sin (Y)
                return ELL_UNIT[idx % ELL_N][col]
            adj = (base << ELL_DIV) - ((amp * tv(s)) >> (14 - ELL_DIV))
            b = bytes([0x82, 0x04])              # LIN x33 packinfo
            idx = s % ELL_N
            for i in range(ELL_N + 1):
                raw = adj + ((amp * tv(idx)) >> (14 - ELL_DIV))
                b += pair(raw, 0 if i == ELL_N else leg)
                idx = (idx + k) % ELL_N          # entry 32 wraps to s (loop close)
            return b

        return {5: ring2(x0, ax, sx, kx, 0), 6: ring2(y0, ay, sy, ky, 1)}
    a = 2 * params[0]
    xs = bytes([0x43]) + pair(x0, q) + pair(x0 - a, q) + pair(x0 - 2 * a, q) \
        + pair(x0 - a, q) + pair(x0, 0)
    ys = bytes([0x43]) + pair(y0, q) + pair(y0 + a, q) + pair(y0, q) \
        + pair(y0 - a, q) + pair(y0, 0)
    return {5: xs, 6: ys}

def synth_spec_bytes(pid, end4, params):
    if pid == 3:
        return bytes([0x80 | pid, end4]) + struct.pack("B", params[0])
    if pid == 4:
        return bytes([0x80 | pid, end4]) + struct.pack("BB", params[0], params[1])
    if pid == 5:                                # ax ay sx kx sy ky (all u8)
        return bytes([0x80 | pid, end4]) + struct.pack("6B", *params)
    return bytes([0x80 | pid, end4]) + struct.pack("%db" % len(params), *params)

# ------------------------------------------------------------- generation ----

def snap2(v):
    return int(round(v / 2.0)) * 2

def gen_formation(rng, si, bounds, sample_pos, base):
    """Phase-stagger: N movers evenly spaced around ONE shared ellipse, orbiting
    in lockstep (a pid-5 ring with kx=ky=1, staggered start index per member).

    Each member's static position is placed AT its point on the shared ellipse
    (center anchored so adj-base ~= center), so frame-0 == static (no pop) and
    the whole set traces the same locus. Returns [(x, y, spec, label)] or None."""
    bx, by = base
    N = rng.choice([2, 3])
    for _ in range(40):
        cx, cy = sample_pos()                    # shared ellipse center
        ax = rng.randint(12, 26)
        ay = rng.randint(max(8, ax // 2), ax)
        T = rng.choice([384, 448, 512, 576, 640])
        s0 = rng.randrange(ELL_N)
        members, ok = [], True
        for j in range(N):
            s = (s0 + j * (ELL_N // N)) % ELL_N
            # place member at the ellipse point for phase s: solve base so the
            # ring's folded bias re-centers the locus on (cx, cy).
            x = snap2(cx + ((ax * ELL_UNIT[s][0]) >> (14 - ELL_DIV)) / 8.0)
            y = snap2(cy + ((ay * ELL_UNIT[s][1]) >> (14 - ELL_DIV)) / 8.0)
            if abs(x - bx) > 254 or abs(y - by) > 254:
                ok = False
                break
            params = [ax, ay, s, 1, s, 1]
            streams = synth_streams(5, T // 4, x, y, params)
            tr = {t: sample_loop(list(st), synth_frac(5), T, T + 8)
                  for t, st in streams.items()}
            if not _path_ok(si, bounds, tr[5], tr[6], T, False):
                ok = False
                break
            members.append((x, y, {"synth": 5, "params": params, "end": T},
                            "formation"))
        if ok:
            return members
    return None

def gen_stage(rng, si, bounds, targets, n_movers, cfg):
    """Returns per-stage dict: base, entries [(slotidx, x, y, moverspec)]."""
    r = bounds.rect[si]
    bx, by = snap2((r["x1"] + r["x2"]) / 2), snap2((r["y1"] + r["y2"]) / 2)

    def sample_pos():
        for _ in range(2000):
            x = snap2(rng.uniform(r["x1"], r["x2"]))
            y = snap2(rng.uniform(r["y1"], r["y2"]))
            if abs(x - bx) <= 254 and abs(y - by) <= 254 and bounds.valid(si, x, y):
                return x, y
        raise RuntimeError(f"stage {si}: cannot place a target")

    slots = sorted(t["slot"] for t in targets)
    mover_order = rng.sample(slots, len(slots))  # movers first, rest as backup
    entries = {}
    stats = []
    state = {"parkdash": 0}
    n_done = 0
    assigned = set()

    # phase-stagger: ~half the stages get one lockstep ellipse formation, using
    # up the first few mover slots. Falls through to normal movers on failure.
    if cfg.get("formations") and n_movers >= 2 and rng.random() < 0.5:
        form = gen_formation(rng, si, bounds, sample_pos, (bx, by))
        if form and len(form) <= min(n_movers, len(mover_order)):
            for (x, y, spec, label), slot in zip(form, mover_order):
                entries[slot] = (slot - 199, x, y, spec)
                assigned.add(slot)
                stats.append((slot, label, spec["end"]))
                n_done += 1

    for slot in mover_order:
        if n_done >= n_movers:
            break
        if slot in assigned:
            continue
        spec, x, y, label = gen_mover(rng, si, bounds, sample_pos, cfg, state)
        if spec:
            stats.append((slot, label, spec["end"]))
            n_done += 1
            entries[slot] = (slot - 199, x, y, spec)
    for slot in slots:
        if slot not in entries:
            x, y = sample_pos()
            entries[slot] = (slot - 199, x, y, None)
    return {"base": (bx, by),
            "entries": [entries[s] for s in slots],
            "stats": stats}

RAW_FN = {"el": pat_elevator, "sw": pat_sweep, "dg": pat_diagonal,
          "dm": pat_diamond, "pd": pat_parkdash}

def _menu(cfg, state):
    rich = cfg["rich"]
    if rich >= 3:                               # flat weights, 2 parkdash
        m = ["el", "sw", "dg", "dm"]
        if cfg["synth"]:                        # ellipse/lissajous/tilted are
            m += ["ell", "lj", "tl"]            # synth-only
        if state["parkdash"] < 2:
            m.append("pd")
    elif rich == 2:                             # cheap-biased, 1 parkdash
        m = ["el"] * 3 + ["sw"] * 3 + ["dg", "dm"]
        if cfg["synth"]:
            m.append("ell")
        if state["parkdash"] < 1:
            m.append("pd")
    else:
        m = ["el", "sw"]
    return m

def _path_ok(si, bounds, xs, ys, T, is_dash):
    px, py = xs[0], ys[0]
    for f in range(T + 8):
        if not bounds.valid(si, xs[f], ys[f]):
            return False
        if math.hypot(xs[f] - px, ys[f] - py) > (CAP_DASH if is_dash else CAP_SPEED):
            return False
        px, py = xs[f], ys[f]
    return True

def gen_mover(rng, si, bounds, sample_pos, cfg, state):
    menu = _menu(cfg, state)
    x, y = sample_pos()
    for attempt in range(120):
        if attempt and attempt % 20 == 0:
            x, y = sample_pos()                 # tight spot: try elsewhere
        kind = rng.choice(menu)
        p = rng.randint(8, 28) if attempt < 60 else rng.randint(5, 12)

        if cfg["synth"] and kind != "pd":       # lever 3: params, not streams
            pid = SYNTH_KIND[kind]
            if pid == 5:                        # orbit: longer periods so the
                T = rng.choice([384, 448, 512, 576, 640, 768])  # strided chords
            elif pid in (3, 4):                 # stay under the speed cap
                T = rng.choice([256, 320, 384, 448])
            else:
                T = rng.choice(P_LEG["long"])
            label = SYNTH_LABEL[pid]
            if pid == 2:
                params = [rng.choice([-1, 1]) * p,
                          rng.choice([-1, 1]) * max(4, p // 2)]
            elif pid == 3:
                params = [p]
            elif pid == 4:                      # ellipse: radii (ax, ay). Keep
                ax = max(11, p)                 # both >= 8 so integer keypoints
                ay = rng.randint(max(8, ax // 2), ax)  # don't stair-step at
                params = [ax, ay]               # small radius (LIN quantization)
            elif pid == 5:                      # generalized ring
                ax = max(11, p)
                ay = rng.randint(max(8, ax // 2), ax)
                sx = rng.randrange(ELL_N)
                if kind == "lj":                # lissajous: distinct strides ->
                    kx, ky = rng.choice([(1, 2), (2, 1), (2, 3),  # figure-8s /
                                         (3, 2), (1, 3), (3, 1)])  # pretzels
                    sy = rng.randrange(ELL_N)
                    label = "lissajous"
                else:                           # tilted ellipse: kx=ky=1, but Y
                    kx = ky = 1                 # phase off the 90-degree offset
                    sy = (sx + rng.choice([2, 4, 6, 10, 12,
                                           -2, -4, -6])) % ELL_N  # (=8) so it tilts
                    label = "tilted"
                params = [ax, ay, sx, kx, sy, ky]
            else:
                params = [rng.choice([-1, 1]) * p]
            streams = synth_streams(pid, T // 4, x, y, params)
            tr = {t: sample_loop(list(s), synth_frac(pid), T, T + 8)
                  for t, s in streams.items()}
            xs = tr.get(5, [float(x)] * (T + 8))
            ys = tr.get(6, [float(y)] * (T + 8))
            if not _path_ok(si, bounds, xs, ys, T, False):
                continue
            # frame-0 value == the static position by construction
            return ({"synth": pid, "params": params, "end": T},
                    x, y, label + "*")

        amp = 2 * p                             # raw-stream path
        pat = RAW_FN[kind]
        tracks, T, label = pat(rng, x, y, amp)
        assert T % 4 == 0 and T <= 1020
        sampled = {}
        for ttype, (op, pairs) in tracks.items():
            pairs = [(int(round(v)), w) for v, w in pairs]
            tracks[ttype] = (op, pairs)
            frac, stream = encode_track(op, pairs)
            sampled[ttype] = sample_loop(stream, frac, T, T + 8)
        xs = sampled.get(5, [float(x)] * (T + 8))
        ys = sampled.get(6, [float(y)] * (T + 8))
        if not _path_ok(si, bounds, xs, ys, T, label == "parkdash"):
            continue
        # base position = frame-0 track values (no first-frame pop)
        bx0 = int(round(xs[0])) if 5 in tracks else x
        by0 = int(round(ys[0])) if 6 in tracks else y
        spec = {"tracks": tracks, "end": T}
        if label == "parkdash":
            state["parkdash"] += 1
        return spec, snap2(bx0), snap2(by0), label
    return None, x, y, "static-fallback"      # give up: caller tries next slot

# ---------------------------------------------------------------- packing ----

def pack_stage(st):
    presence = 0
    movers = 0
    coords = bytearray()
    specs = bytearray()
    for idx, x, y, spec in st["entries"]:
        presence |= 1 << idx
        bx, by = st["base"]
        rx, ry = (x - bx) // 2, (y - by) // 2
        assert -128 <= rx <= 127 and -128 <= ry <= 127, (idx, x, y, st["base"])
        coords += struct.pack("bb", rx, ry)
        if spec:
            movers |= 1 << idx
            if spec.get("synth") is not None:
                specs += synth_spec_bytes(spec["synth"], spec["end"] // 4,
                                          spec["params"])
            else:
                flags = (1 if 5 in spec["tracks"] else 0) | (2 if 6 in spec["tracks"] else 0)
                specs += struct.pack("BB", flags, spec["end"] // 4)
                for ttype in (5, 6):            # installer order: TRAX, TRAY
                    if ttype not in spec["tracks"]:
                        continue
                    op, pairs = spec["tracks"][ttype]
                    frac, stream = encode_track(op, pairs)
                    assert len(stream) <= 255
                    specs += struct.pack("BB", frac, len(stream)) + stream
    blob = struct.pack(">IIhh", presence, movers, *st["base"]) + coords + specs
    while len(blob) % 4:                        # keep every stage 4-aligned so
        blob += b"\x00"                         # the installer's lwz is aligned
    return blob

def pack_all(per_stage):
    dir_entries = [0xFFFF] * 26
    blobs = b""
    off = 52                                    # directory = 26 * u16
    for (name, gid, _), st in per_stage:
        b = pack_stage(st)
        dir_entries[gid - 0x28] = off
        blobs += b
        off += len(b)
    directory = b"".join(struct.pack(">H", e) for e in dir_entries)
    return directory + blobs

# ------------------------------------------------------ blob -> installer ----

def splice(code_bin, data):
    assert code_bin[8:12] == b"\x48\x00\x00\x05", "Lpc drift: rebuild contract"
    code, nop = code_bin[:-4], code_bin[-4:]
    ldir = len(code)                            # Ldir sits right before the nop
    # find the unique branch that targets Ldir and stretch it over the data
    patched = None
    ca = bytearray(code)
    for o in range(0, len(code), 4):
        w = struct.unpack(">I", code[o:o + 4])[0]
        if (w & 0xFC000003) == 0x48000000:
            disp = w & 0x03FFFFFC
            if disp & 0x02000000:
                continue                        # backward branch
            if o + disp == ldir:
                assert patched is None, "two branches target Ldir"
                nw = 0x48000000 | ((disp + len(data)) & 0x03FFFFFC)
                ca[o:o + 4] = struct.pack(">I", nw)
                patched = o
    assert patched is not None, "branch to Ldir not found"
    return bytes(ca) + data + nop

def emit_gct(blob, title, path):
    words = list(struct.unpack(">%dI" % (len(blob) // 4), blob))
    if len(words) % 2:
        words.append(0x60000000)
    lines = [f"${title}", "C2%06X %08X" % (HOOK & 0x01FFFFFF, len(words) // 2)]
    for i in range(0, len(words), 2):
        lines.append("%08X %08X" % (words[i], words[i + 1]))
    open(path, "w").write("\n".join(lines) + "\n")
    return len(words) // 2

# ------------------------------------------------------------ verification ----

def verify_blob(data, per_stage, bounds):
    """Re-parse the packed blob exactly like mover_install.s does and
    re-validate every static position and every mover frame."""
    checked_static = checked_frames = 0
    for (name, gid, _), st in per_stage:
        si = [i for i, s in enumerate(STAGES) if s[1] == gid][0]
        off = struct.unpack(">H", data[2 * (gid - 0x28):2 * (gid - 0x28) + 2])[0]
        assert off != 0xFFFF
        p = off
        presence, movers, bx, by = struct.unpack(">IIhh", data[p:p + 12])
        p += 12
        coords = {}
        for i in range(21):
            if presence >> i & 1:
                rx, ry = struct.unpack("bb", data[p:p + 2])
                p += 2
                coords[i] = (bx + 2 * rx, by + 2 * ry)
        for i in range(21):
            if not (movers >> i & 1):
                if i in coords:
                    assert bounds.valid(si, *coords[i]), (name, i, coords[i])
                    checked_static += 1
                continue
            flags, end4 = struct.unpack("BB", data[p:p + 2])
            p += 2
            T = end4 * 4
            tr = {}
            if flags & 0x80:                    # synth spec: re-synthesize the
                pid = flags & 0xF               # stream exactly like the
                if pid == 2:                    # installer and validate that
                    params = list(struct.unpack("bb", data[p:p + 2]))
                    p += 2
                elif pid == 3:
                    params = list(struct.unpack("B", data[p:p + 1]))
                    p += 1
                elif pid == 4:
                    params = list(struct.unpack("BB", data[p:p + 2]))
                    p += 2
                elif pid == 5:
                    params = list(struct.unpack("6B", data[p:p + 6]))
                    p += 6
                else:
                    params = list(struct.unpack("b", data[p:p + 1]))
                    p += 1
                streams = synth_streams(pid, end4, *coords[i], params)
                tr = {t: sample_loop(list(s), synth_frac(pid), T, T + 8)
                      for t, s in streams.items()}
            else:
                for ttype, bit in ((5, 1), (6, 2)):
                    if flags & bit:
                        frac, ln = struct.unpack("BB", data[p:p + 2])
                        p += 2
                        tr[ttype] = sample_loop(data[p:p + ln], frac, T, T + 8)
                        p += ln
            xs = tr.get(5, [float(coords[i][0])] * (T + 8))
            ys = tr.get(6, [float(coords[i][1])] * (T + 8))
            for f in range(T + 8):
                assert bounds.valid(si, xs[f], ys[f]), (name, i, f, xs[f], ys[f])
            checked_frames += T + 8
    return checked_static, checked_frames

# ----------------------------------------------------------------- main ----

def main():
    global PERIOD_MODE
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default=os.path.join(HERE, "moving_targets_keyframe.gecko.txt"))
    ap.add_argument("--synth", action="store_true",
                    help="lever 3: emit param specs for the SYNTH installer")
    ap.add_argument("--short-waits", action="store_true",
                    help="lever 1: raw-stream periods with 1-byte waits")
    ap.add_argument("--installer", default=None,
                    help="installer .bin (default picks by --synth)")
    ap.add_argument("--title", default=None)
    args = ap.parse_args()

    PERIOD_MODE = "short" if args.short_waits else "long"
    inst = args.installer or os.path.join(
        HERE, "src",
        "mover_install_synth.bin" if args.synth else "mover_install.bin")
    bounds = Bounds(os.path.join(HERE, "btt_bounds.json"))
    code_bin = open(inst, "rb").read()

    # No degradation: always richest patterns (targets a modded-codespace
    # Dolphin; the stock ~3248 B cap no longer gates -- see note below).
    rng = random.Random(args.seed)
    cfg = {"synth": args.synth, "rich": 3, "formations": args.synth}
    per_stage = []
    report = []
    for si, (name, gid, jname) in enumerate(STAGES):
        targets = json.load(open(os.path.join(DATA, jname + ".json")))["targets"]
        assert all(199 <= t["slot"] <= 219 for t in targets), name
        n = rng.randint(3, 5)
        st = gen_stage(rng, si, bounds, targets, n, cfg)
        per_stage.append(((name, gid, jname), st))
        report.append((name, len(targets), st["stats"]))
    data = pack_all(per_stage)
    blob = splice(code_bin, data)

    ns, nf = verify_blob(data, per_stage, bounds)
    title = args.title or f"Moving Targets Keyframe [all stages, seed {args.seed}]"
    nlines = emit_gct(blob, title, args.out)

    total_movers = 0
    print(f"{'stage':<12} targets movers  patterns")
    for name, ntgt, stats in report:
        total_movers += len(stats)
        pd = ", ".join(f"{lab}({T}f)" for _, lab, T in stats)
        print(f"{name:<12} {ntgt:^7} {len(stats):^6}  {pd}")
    gct_total = 8 + 8 + ((len(blob) + 7) // 8) * 8 + 8
    ncode = len(code_bin) - 4
    print(f"\nmovers: {total_movers}  "
          f"installer: {ncode} B code + {len(data)} B data = {len(blob)} B  "
          f"GCT total: {gct_total} B  codelist lines: {nlines}")
    if len(blob) > BLOB_BUDGET:
        print(f"NOTE: blob {len(blob)} B exceeds the stock ~{BLOB_BUDGET} B cap "
              f"-- fine on modded codespace, would need degradation for stock")
    print(f"verified: {ns} static positions, {nf} mover frames in bounds")
    print(f"wrote {args.out}")

if __name__ == "__main__":
    main()
