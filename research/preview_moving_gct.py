#!/usr/bin/env python3
"""Render an SVG preview of a generated moving-targets GCT.

Decodes the data blob out of the emitted .gecko.txt (the honest path: what
the installer will parse), samples every mover's looped path through the
FObj VM, and draws a grid of stages: collision in gray, bttrandomizer
bounds rect dashed blue, exclusion zones red, static targets as dots,
mover paths as colored traces.

Usage: python3 preview_moving_gct.py [gct] [out.svg]
"""
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SHARED = os.path.join(REPO, "shared")   # extract_stage.py and other shared tooling
DATA = os.path.join(REPO, "data")       # extracted stage .json dumps
sys.path.insert(0, HERE)
sys.path.insert(0, SHARED)
from extract_stage import FRAC_S16
from gen_moving_gct import STAGES, Bounds, sample_loop, synth_streams, synth_frac

PATH_COLORS = ["#e6194b", "#3cb44b", "#ff8c00", "#911eb4", "#00bfff"]

def load_blob(gct_path):
    lines = open(gct_path).read().split("\n")
    nlines = int(lines[1].split()[1], 16)
    words = []
    for l in lines[2:2 + nlines]:
        a, b = l.split()
        words += [int(a, 16), int(b, 16)]
    blob = b"".join(struct.pack(">I", w) for w in words)
    for binname in ("mover_install.bin", "mover_install_synth.bin"):
        ncode = len(open(os.path.join(HERE, "src", binname), "rb").read()) - 4
        data = blob[ncode:]
        if len(data) > 52:
            offs = struct.unpack(">26H", data[:52])
            real = [o for o in offs if o != 0xFFFF]
            if real and all(52 <= o < len(data) for o in real):
                return data
    raise SystemExit("cannot locate data blob (unknown installer variant?)")

def decode_stage(data, gid):
    off = struct.unpack(">H", data[2 * (gid - 0x28):2 * (gid - 0x28) + 2])[0]
    if off == 0xFFFF:
        return None
    p = off
    presence, movers, bx, by = struct.unpack(">IIhh", data[p:p + 12])
    p += 12
    coords = {}
    for i in range(21):
        if presence >> i & 1:
            rx, ry = struct.unpack("bb", data[p:p + 2])
            p += 2
            coords[i] = (bx + 2 * rx, by + 2 * ry)
    paths = {}
    for i in range(21):
        if not (movers >> i & 1):
            continue
        flags, end4 = struct.unpack("BB", data[p:p + 2])
        p += 2
        T = end4 * 4
        tr = {}
        if flags & 0x80:                        # synth spec
            pid = flags & 0xF
            if pid == 5:
                n, fmt = 6, "6B"
            elif pid == 4:
                n, fmt = 2, "BB"
            elif pid == 3:
                n, fmt = 1, "B"
            elif pid == 2:
                n, fmt = 2, "bb"
            else:
                n, fmt = 1, "b"
            params = list(struct.unpack(fmt, data[p:p + n]))
            p += n
            for t, s in synth_streams(pid, end4, *coords[i], params).items():
                tr[t] = sample_loop(list(s), synth_frac(pid), T, T)
        else:
            for ttype, bit in ((5, 1), (6, 2)):
                if flags & bit:
                    frac, ln = struct.unpack("BB", data[p:p + 2])
                    p += 2
                    tr[ttype] = sample_loop(data[p:p + ln], frac, T, T)
                    p += ln
        xs = tr.get(5, [float(coords[i][0])] * T)
        ys = tr.get(6, [float(coords[i][1])] * T)
        paths[i] = (xs, ys)
    return coords, paths

def main():
    gct = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "moving_targets_keyframe.gecko.txt")
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "moving_targets_preview.svg")
    data = load_blob(gct)
    bounds = Bounds(os.path.join(HERE, "btt_bounds.json"))

    CELL, PAD = 260, 26
    cols = 5
    rows = (len(STAGES) + cols - 1) // cols
    W, H = cols * (CELL + PAD) + PAD, rows * (CELL + PAD + 16) + PAD
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="monospace" font-size="11">',
           f'<rect width="{W}" height="{H}" fill="#fafafa"/>']

    for si, (name, gid, jname) in enumerate(STAGES):
        dec = decode_stage(data, gid)
        stage = json.load(open(os.path.join(DATA, jname + ".json")))
        r = bounds.rect[si]
        # fit stage rect (+margin) into the cell, y flipped
        x1, y1, x2, y2 = r["x1"] - 15, r["y1"] - 15, r["x2"] + 15, r["y2"] + 15
        s = min(CELL / (x2 - x1), CELL / (y2 - y1))
        ox = PAD + (si % cols) * (CELL + PAD)
        oy = PAD + (si // cols) * (CELL + PAD + 16) + 14

        def tx(x):
            return ox + (x - x1) * s
        def ty(y):
            return oy + (y2 - y) * s

        cw, ch = (x2 - x1) * s, (y2 - y1) * s
        svg.append(f'<text x="{ox}" y="{oy - 4}" fill="#333">{name}</text>')
        svg.append(f'<clipPath id="c{si}"><rect x="{ox}" y="{oy}" width="{cw:.0f}" height="{ch:.0f}"/></clipPath>')
        svg.append(f'<rect x="{ox}" y="{oy}" width="{cw:.0f}" height="{ch:.0f}" fill="white" stroke="#ddd"/>')
        svg.append(f'<g clip-path="url(#c{si})">')
        # bounds rect + exclusions
        svg.append(f'<rect x="{tx(r["x1"]):.1f}" y="{ty(r["y2"]):.1f}" width="{(r["x2"]-r["x1"])*s:.1f}" '
                   f'height="{(r["y2"]-r["y1"])*s:.1f}" fill="none" stroke="#4a90d9" stroke-dasharray="4 3"/>')
        for vs in bounds.excl[si]:
            pts = vs if len(vs) > 2 else [[vs[0][0], vs[0][1]], [vs[1][0], vs[0][1]],
                                          [vs[1][0], vs[1][1]], [vs[0][0], vs[1][1]]]
            p = " ".join(f"{tx(px):.1f},{ty(py):.1f}" for px, py in pts)
            svg.append(f'<polygon points="{p}" fill="#e6194b" fill-opacity="0.12" stroke="none"/>')
        # collision
        verts = stage["collision"]["vertices"]
        for ln in stage["collision"]["lines"]:
            a, b = verts[ln["v0"]], verts[ln["v1"]]
            svg.append(f'<line x1="{tx(a[0]):.1f}" y1="{ty(a[1]):.1f}" x2="{tx(b[0]):.1f}" '
                       f'y2="{ty(b[1]):.1f}" stroke="#999" stroke-width="1.2"/>')
        if dec:
            coords, paths = dec
            ci = 0
            for i, (x, y) in sorted(coords.items()):
                if i in paths:
                    xs, ys = paths[i]
                    col = PATH_COLORS[ci % len(PATH_COLORS)]
                    ci += 1
                    pts = " ".join(f"{tx(xs[f]):.1f},{ty(ys[f]):.1f}" for f in range(0, len(xs), 2))
                    svg.append(f'<polyline points="{pts}" fill="none" stroke="{col}" '
                               f'stroke-width="1.6" stroke-opacity="0.85"/>')
                    svg.append(f'<circle cx="{tx(xs[0]):.1f}" cy="{ty(ys[0]):.1f}" r="3.2" '
                               f'fill="{col}"/>')
                else:
                    svg.append(f'<circle cx="{tx(x):.1f}" cy="{ty(y):.1f}" r="2.6" '
                               f'fill="#c00" fill-opacity="0.85"/>')
        svg.append('</g>')
    svg.append("</svg>")
    open(out, "w").write("\n".join(svg))
    print("wrote", out)

if __name__ == "__main__":
    main()
