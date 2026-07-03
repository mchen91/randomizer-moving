#!/usr/bin/env python3
"""Generate the all-stages keyframe moving-target GCT.

For every ten-target Target Test stage: randomize all 10 target positions
within bttrandomizer's bounds/exclusions, pick 3-5 of them as movers, author
looping FObj keyframe streams for those (a variety of patterns: elevators,
sweeps, diagonals, circles, park-and-dash), validate every swept frame
against the stage bounds, and pack everything into the data blob consumed by
gecko/mover_install.s (see its BLOB CONTRACT comment).

The streams are validated by round-tripping through fobj_vm.py (FObj VM ported
from the decomp) plus a simulation of the AObj loop-rewind, so what is checked
is what the game will actually play. A final re-decode pass parses the packed
blob exactly like the installer does and re-validates, catching packing bugs.

Usage: python3 gen_moving_gct.py [--seed N] [--out FILE]

Requires gecko/mover_install.bin (run ./build.sh or gecko/build_install.sh).
Stage target slots live in data/stages/GrT*.json; bounds in btt_bounds.json.
"""
import argparse
import json
import math
import os
import random
import struct

HERE = os.path.dirname(os.path.abspath(__file__))
STAGE_DATA = os.path.join(HERE, "data", "stages")
from fobj_vm import FObj, OP_LIN, OP_SPL0, FRAC_S8, FRAC_S16

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

def _leg_pairs(op, a, b, half):
    return (op, [(a, half), (b, half), (a, 0)])

def pat_elevator(rng, x, y, amp):
    T = rng.choice([240, 320, 400, 480])
    return {6: _leg_pairs(OP_SPL0, y - amp, y + amp, T // 2)}, T, "elevator"

def pat_sweep(rng, x, y, amp):
    T = rng.choice([240, 320, 400, 480])
    return {5: _leg_pairs(OP_SPL0, x - amp, x + amp, T // 2)}, T, "sweep"

def pat_diagonal(rng, x, y, amp):
    T = rng.choice([320, 400, 480])
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

# ------------------------------------------------------------- generation ----

def snap2(v):
    return int(round(v / 2.0)) * 2

def gen_stage(rng, si, bounds, targets, n_movers, rich):
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
    for slot in mover_order:
        if n_done >= n_movers:
            break
        spec, x, y, label = gen_mover(rng, si, bounds, sample_pos, rich, state)
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

def gen_mover(rng, si, bounds, sample_pos, rich, state):
    if rich:
        pats = [pat_elevator] * 3 + [pat_sweep] * 3 + [pat_diagonal, pat_diamond]
        if state["parkdash"] < 1:
            pats += [pat_parkdash]
    else:
        pats = PATTERNS_CHEAP
    x, y = sample_pos()
    for attempt in range(120):
        if attempt and attempt % 20 == 0:
            x, y = sample_pos()                 # tight spot: try elsewhere
        pat = rng.choice(pats)
        amp = rng.randint(16, 56) if attempt < 60 else rng.randint(10, 24)
        tracks, T, label = pat(rng, x, y, amp)
        assert T % 4 == 0 and T <= 1020
        # integer values, loop closure, then simulate exactly like the game
        ok = True
        sampled = {}
        for ttype, (op, pairs) in tracks.items():
            pairs = [(int(round(v)), w) for v, w in pairs]
            tracks[ttype] = (op, pairs)
            frac, stream = encode_track(op, pairs)
            sampled[ttype] = sample_loop(stream, frac, T, T + 8)
        xs = sampled.get(5, [float(x)] * (T + 8))
        ys = sampled.get(6, [float(y)] * (T + 8))
        is_dash = label == "parkdash"
        px, py = xs[0], ys[0]
        for f in range(T + 8):
            if not bounds.valid(si, xs[f], ys[f]):
                ok = False
                break
            step = math.hypot(xs[f] - px, ys[f] - py)
            if step > (CAP_DASH if is_dash else CAP_SPEED):
                ok = False
                break
            px, py = xs[f], ys[f]
        if not ok:
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
            flags = (1 if 5 in spec["tracks"] else 0) | (2 if 6 in spec["tracks"] else 0)
            specs += struct.pack("BB", flags, spec["end"] // 4)
            for ttype in (5, 6):                # installer order: TRAX, TRAY
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default=os.path.join(HERE, "moving_targets_keyframe.gecko.txt"))
    args = ap.parse_args()

    bounds = Bounds(os.path.join(HERE, "btt_bounds.json"))
    code_bin = open(os.path.join(HERE, "gecko", "mover_install.bin"), "rb").read()

    for richness in (2, 1, 0):                 # degrade until the blob fits
        rng = random.Random(args.seed)
        per_stage = []
        report = []
        for si, (name, gid, jname) in enumerate(STAGES):
            targets = json.load(open(os.path.join(STAGE_DATA, jname + ".json")))["targets"]
            assert all(199 <= t["slot"] <= 219 for t in targets), name
            n = rng.randint(3, 5) if richness == 2 else (rng.randint(3, 4) if richness == 1 else 3)
            st = gen_stage(rng, si, bounds, targets, n, rich=richness > 0)
            per_stage.append(((name, gid, jname), st))
            report.append((name, len(targets), st["stats"]))
        data = pack_all(per_stage)
        blob = splice(code_bin, data)
        if len(blob) <= BLOB_BUDGET:
            break
        print(f"richness {richness}: blob {len(blob)} B over budget, degrading")
    else:
        raise SystemExit("cannot fit blob in codespace")

    ns, nf = verify_blob(data, per_stage, bounds)
    nlines = emit_gct(blob, f"Moving Targets Keyframe [all stages, seed {args.seed}]", args.out)

    total_movers = 0
    print(f"{'stage':<12} targets movers  patterns")
    for name, ntgt, stats in report:
        total_movers += len(stats)
        pd = ", ".join(f"{lab}({T}f)" for _, lab, T in stats)
        print(f"{name:<12} {ntgt:^7} {len(stats):^6}  {pd}")
    gct_total = 8 + 8 + ((len(blob) + 7) // 8) * 8 + 8
    print(f"\nmovers: {total_movers}  installer+data blob: {len(blob)} B  "
          f"GCT total: {gct_total} B (cap ~3248)  codelist lines: {nlines}")
    print(f"verified: {ns} static positions, {nf} mover frames in bounds")
    print(f"wrote {args.out}")

if __name__ == "__main__":
    main()
