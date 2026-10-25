# Template-synthesis extension for mover_install.s (lever 3). Included under
# .ifdef SYNTH; continues .text.entry.
#
# Instead of carrying literal FObj stream bytes in the codelist, a synth spec
# is 3-4 bytes and the stream is BUILT here at install time into an
# HSD_MemAlloc'd buffer (48 B: track A at +0, track B at +24). Buffer RAM is
# free; only the params cost codespace. Streams always use FRAC_S16 values
# (sthbrx = little-endian, the FObj convention) and fixed 2-byte wait
# varints ((w&0x7F)|0x80, w>>7 -- a valid encoding for any w<=16383), so
# synthesis code needs no size special-cases. Alloc is per stage load and
# leaks on reload -- same accepted pattern as flip_and_load's per-load copy.
#
# Spec: u8 0x80|pattern, u8 end4, params. All values derive from the slot's
# already-written static position (xi/yi ints saved at 0x54/0x58(1)), which
# equals the pattern's frame-0 value, so there is no first-frame pop:
#   0 elevator: s8 amp -> TRAY 3-pair (yi, yi+2*amp, yi), waits T/2
#   1 sweep:    s8 amp -> TRAX ditto with xi
#   2 diagonal: s8 ax, s8 ay -> both 3-pair tracks, same waits
#   3 diamond:  u8 amp -> a=2*amp; TRAX (x0,x0-a,x0-2a,x0-a,x0) +
#               TRAY (y0,y0+a,y0,y0-a,y0), waits T/4 -- orbit around
#               (x0-a, y0) starting at the right vertex
#   4 ellipse:  u8 ax, u8 ay -> constant-speed 32-gon (LIN) around (x0-ax, y0),
#               radii (ax, ay); waits T/32. ax==ay is a circle. Two 134 B
#               tracks, so this pattern allocs a 320 B buffer (A @0, B @160).
# Entry state: r9=flags r10=end4 r26=spec start (unadvanced), AObjDesc at
# 0x18(1) already filled. Exits by branching to Lnoy (r8 = first desc) or
# Lnext (alloc failure: spec consumed, target stays static).

Lsynth:
    lbz     9, 0(26)            # peek pattern to size the alloc
    clrlwi  9, 9, 28
    li      3, 48               # 48 B fits el/sw/dg/dm (tracks <=24 B each)
    cmpwi   9, 4
    blt     Lsy_alloc
    li      3, 320              # ellipse/orbit (pid>=4): two 134 B rings (A @0,
Lsy_alloc:                      # B @160)
    lis     4, 0x8037           # HSD_MemAlloc(size) -> r3
    ori     4, 4, 0xF1E4
    mtctr   4
    bctrl
    cmpwi   3, 0
    beq     Lsyskip
    mr      12, 3               # r12 = buf (no game call until LoadDesc)
    lbz     9, 0(26)            # reload pattern/end4 (call clobbered them)
    clrlwi  9, 9, 28
    lbz     10, 1(26)
    cmpwi   9, 2
    beq     Lsy_diag
    cmpwi   9, 4
    beq     Lsy_ellipse
    cmpwi   9, 5
    beq     Lsy_orbit
    cmpwi   9, 3
    beq     Lsy_diam
    cmpwi   9, 1
    beq     Lsy_sweep

    lwz     3, 0x58(1)          # elevator: TRAY from yi
    bl      sy_d3
    mr      6, 12
    bl      emit3
    addi    4, 1, 0x28
    li      5, 6
    b       Lsy_fin1

Lsy_sweep:
    lwz     3, 0x54(1)          # sweep: TRAX from xi
    bl      sy_d3
    mr      6, 12
    bl      emit3
    addi    4, 1, 0x28
    li      5, 5
    b       Lsy_fin1

Lsy_diag:
    lwz     3, 0x54(1)          # diagonal: TRAX leg from param @2
    bl      sy_d3
    mr      6, 12
    bl      emit3
    addi    4, 1, 0x28
    li      5, 5
    mr      6, 12
    li      7, 13
    bl      fsdesc              # desc A
    lwz     3, 0x58(1)          # TRAY leg from param @3
    lbz     5, 3(26)
    extsb   5, 5
    slwi    5, 5, 1
    add     5, 3, 5
    lbz     4, 1(26)            # half = 2*end4 (r10 clobbered by emit3)
    slwi    4, 4, 1
    addi    6, 12, 24
    bl      emit3
    addi    4, 1, 0x3C
    li      5, 6
    addi    6, 12, 24
    li      7, 13
    bl      fsdesc              # desc B
    addi    8, 1, 0x28
    stw     4, 0(8)             # A.next = B
    addi    26, 26, 4
    b       Lnoy

Lsy_diam:
    lwz     3, 0x54(1)          # x0
    lbz     5, 2(26)
    slwi    5, 5, 1             # a = 2*amp
    mr      4, 10               # q = end4 = T/4
    mr      6, 12
    bl      emit5x
    addi    4, 1, 0x28
    li      5, 5
    mr      6, 12
    li      7, 21
    bl      fsdesc
    lwz     3, 0x58(1)          # y0
    lbz     5, 2(26)
    slwi    5, 5, 1
    lbz     4, 1(26)            # q again (r10 clobbered)
    addi    6, 12, 24
    bl      emit5y
    addi    4, 1, 0x3C
    li      5, 6
    addi    6, 12, 24
    li      7, 21
    bl      fsdesc
    addi    8, 1, 0x28
    stw     4, 0(8)
    addi    26, 26, 3
    b       Lnoy

Lsy_ellipse:                    # 4 ellipse: constant-speed 32-gon, ax @2 ay @3
    lwz     3, 0x54(1)          # x0 = xi (frame-0 = static, right vertex)
    lbz     5, 2(26)            # ax
    lbz     4, 1(26)            # end4
    srawi   4, 4, 3             # leg = end4/8 = T/32
    mr      6, 12
    addi    7, 31, Ltab_ell-Lpc # cos column
    li      8, 16384            # bias so keypoint 0 = x0
    bl      emit_ring
    addi    4, 1, 0x28
    li      5, 5
    mr      6, 12
    li      7, 134
    bl      fsdesc              # desc A (TRAX)
    li      0, 0x23             # patch frac -> FRAC_S16 | divisor 3 (1/8 unit)
    stb     0, 0xD(4)
    stb     0, 0xE(4)
    lwz     3, 0x58(1)          # y0 = yi
    lbz     5, 3(26)            # ay
    lbz     4, 1(26)            # end4
    srawi   4, 4, 3
    addi    6, 12, 160
    addi    7, 31, Ltab_ell-Lpc
    addi    7, 7, 2             # sin column
    li      8, 0                # bias 0
    bl      emit_ring
    addi    4, 1, 0x3C
    li      5, 6
    addi    6, 12, 160
    li      7, 134
    bl      fsdesc              # desc B (TRAY)
    li      0, 0x23             # patch frac -> FRAC_S16 | divisor 3
    stb     0, 0xD(4)
    stb     0, 0xE(4)
    addi    8, 1, 0x28
    stw     4, 0(8)             # A.next = B
    addi    26, 26, 4           # flags+end4+ax+ay
    b       Lnoy

Lsy_orbit:                      # 5 generalized ring: lissajous / tilted ellipse
    lwz     3, 0x54(1)          # / phase offset. params: ax@2 ay@3 sx@4 kx@5
    lbz     5, 2(26)            #                         sy@6 ky@7. X = cos col
    lbz     4, 1(26)            # end4                    walked idx=(sx+kx*i)&31
    srawi   4, 4, 3             # leg = T/32
    mr      6, 12               # dst = track A @ buf+0
    lbz     7, 4(26)            # sx (start index)
    lbz     8, 5(26)            # kx (stride)
    li      9, 0                # cos column (X)
    bl      emit_ring2
    addi    4, 1, 0x28
    li      5, 5
    mr      6, 12
    li      7, 134
    bl      fsdesc              # desc A (TRAX)
    li      0, 0x23             # patch frac -> FRAC_S16 | divisor 3 (like ellipse)
    stb     0, 0xD(4)
    stb     0, 0xE(4)
    lwz     3, 0x58(1)          # y0
    lbz     5, 3(26)            # ay
    lbz     4, 1(26)            # end4
    srawi   4, 4, 3
    addi    6, 12, 160          # dst = track B @ buf+160
    lbz     7, 6(26)            # sy
    lbz     8, 7(26)            # ky
    li      9, 2                # sin column (Y)
    bl      emit_ring2
    addi    4, 1, 0x3C
    li      5, 6
    addi    6, 12, 160
    li      7, 134
    bl      fsdesc              # desc B (TRAY)
    li      0, 0x23
    stb     0, 0xD(4)
    stb     0, 0xE(4)
    addi    8, 1, 0x28
    stw     4, 0(8)             # A.next = B
    addi    26, 26, 8           # flags+end4 + 6 params (ax ay sx kx sy ky)
    b       Lnoy

Lsy_fin1:                       # single-track finish: r4=desc r5=type
    mr      6, 12
    li      7, 13
    bl      fsdesc
    mr      8, 4                # fsdesc preserves r4
    addi    26, 26, 3
    b       Lnoy

Lsyskip:                        # alloc failed: consume spec, stay static
    lbz     9, 0(26)
    clrlwi  9, 9, 28
    cmpwi   9, 5                 # orbit: flags+end4 + 6 params = 8 bytes
    beq     Lsysk_orbit
    addi    26, 26, 3
    cmpwi   9, 2                 # diagonal + ellipse carry a 2nd param byte
    beq     Lsysk2
    cmpwi   9, 4
    bne     Lsysk1
Lsysk2:
    addi    26, 26, 1
Lsysk1:
    b       Lnext
Lsysk_orbit:
    addi    26, 26, 8
    b       Lnext

# r3=base coord in -> r5 = base + 2*(s8 @2(r26)), r4 = half = 2*end4.
sy_d3:
    lbz     5, 2(26)
    extsb   5, 5
    slwi    5, 5, 1
    add     5, 3, 5
    lbz     4, 1(26)
    slwi    4, 4, 1
    blr

# One (value, wait) pair at r6, advance 4. Preserves r3,r4,r5; clobbers r0.
emitp:
    sthbrx  3, 0, 6             # s16 value, little-endian
    ori     0, 4, 0x80
    stb     0, 2(6)             # wait low7 | continue
    srwi    0, 4, 7
    stb     0, 3(6)             # wait high7 (terminator)
    addi    6, 6, 4
    blr

# SPL0 3-pair loop track (v0, half)(v1, half)(v0, 0): r3=v0 r5=v1 r4=half
# r6=dst. Clobbers r0, r7, r10.
emit3:
    mflr    7
    mr      10, 3
    li      0, 0x23             # SPL0 x3
    stb     0, 0(6)
    addi    6, 6, 1
    bl      emitp
    mr      3, 5
    bl      emitp
    mr      3, 10
    li      4, 0
    bl      emitp
    mtlr    7
    blr

# Diamond X track x0, x0-a, x0-2a, x0-a, x0: r3=x0 r5=a r4=q r6=dst.
emit5x:
    mflr    7
    mr      10, 3
    li      0, 0x43             # SPL0 x5
    stb     0, 0(6)
    addi    6, 6, 1
    bl      emitp
    sub     3, 10, 5
    bl      emitp
    sub     3, 3, 5
    bl      emitp
    sub     3, 10, 5
    bl      emitp
    mr      3, 10
    li      4, 0
    bl      emitp
    mtlr    7
    blr

# Diamond Y track y0, y0+a, y0, y0-a, y0.
emit5y:
    mflr    7
    mr      10, 3
    li      0, 0x43
    stb     0, 0(6)
    addi    6, 6, 1
    bl      emitp
    add     3, 10, 5
    bl      emitp
    mr      3, 10
    bl      emitp
    sub     3, 10, 5
    bl      emitp
    mr      3, 10
    li      4, 0
    bl      emitp
    mtlr    7
    blr

# Constant-speed elliptical ring: 33 LIN keypoints (32 segments + loop close)
# sampled on the unit-circle table Ltab_ell, scaled by the radius. LIN moves at
# constant velocity within each segment, and 32 equal-angle steps give equal
# chords -> no stop-and-go (unlike SPL0). One track:
#   r3=base r5=amp r4=leg(T/32) r6=dst r7=col ptr (Ltab_ell+0=cos, +2=sin)
#   r8=bias (16384 for X so keypoint 0 = base at the right vertex, 0 for Y)
# raw[i] = (base<<3) + ((amp*(tab[i]-bias))>>11); stored s16 and tagged
# FRAC_S16|3 by the caller, so the game reads raw/8 -> 1/8-unit resolution.
# The x8 keeps keypoints off the integer grid (kills quantization wobble);
# safe from s16 overflow since |coord| <= ~560 and 560*8 << 32767.
# Last keypoint wait 0. Clobbers r0,r3,r7,r9,r10,r11.
emit_ring:
    mflr    11
    mr      10, 3               # base
    slwi    10, 10, 3           # base << 3 (divisor 3: raw is in 1/8 units)
    li      0, 0x82             # LIN x33 (packinfo 0x82,0x04)
    stb     0, 0(6)
    li      0, 0x04
    stb     0, 1(6)
    addi    6, 6, 2
    li      9, 0                # i = 0
Lring:
    lha     0, 0(7)             # unit-circle entry (s16, big-endian in code)
    sub     0, 0, 8             # - bias
    mullw   0, 0, 5             # * amp
    srawi   0, 0, 11            # >> (14-3): offset in 1/8 units
    add     3, 10, 0            # base*8 + offset*8
    cmpwi   9, 32
    bne     Lring_e
    li      4, 0                # last keypoint: wait 0
Lring_e:
    bl      emitp
    addi    7, 7, 4             # next unit vector (stride 4)
    addi    9, 9, 1
    cmpwi   9, 33
    blt     Lring
    mtlr    11
    blr

# Generalized constant-speed ring (pid 5): 33 LIN keypoints with table index
# idx(i) = (s + k*i) & 31 -> lissajous (kx!=ky), tilted ellipse (kx=ky=1,
# sy!=sx-8), and phase offset (any start s). Bias = tab[s] is folded into the
# base so keypoint 0 == base for ANY s/k (no first-frame pop). One track:
#   r3=base r4=leg(T/32) r5=amp r6=dst r7=s(start idx) r8=k(stride) r9=col(0/2)
# raw = (base<<3) - ((amp*tab[s])>>11) + ((amp*tab[idx])>>11); stored s16,
# tagged FRAC_S16|3 by the caller (raw/8 -> 1/8-unit resolution, off the grid).
# LR spilled to 0x50(1) (free during synth). Clobbers r0,r3,r4,r7,r9,r10,r11.
emit_ring2:
    mflr    0
    stw     0, 0x50(1)          # spill return LR (0x50 free during synth)
    addi    11, 31, Ltab_ell-Lpc
    add     11, 11, 9           # r11 = column base (Ltab_ell + col); r9 free
    slwi    9, 7, 2             # s*4
    lhax    0, 11, 9            # bias = tab[s]
    mullw   0, 0, 5
    srawi   0, 0, 11            # (amp*bias)>>11
    slwi    3, 3, 3             # base<<3
    sub     10, 3, 0            # r10 = adjbase = base<<3 - (amp*bias)>>11
    li      0, 0x82             # LIN x33 packinfo (0x82,0x04) -- MUST precede
    stb     0, 0(6)             # the keypoints or the game misparses the stream
    li      0, 0x04
    stb     0, 1(6)
    addi    6, 6, 2
    li      9, 0                # i = 0  (r7 = idx = s, kept as running index)
Lring2:
    slwi    3, 7, 2             # idx*4
    lhax    0, 11, 3            # tab[idx]
    mullw   0, 0, 5             # * amp
    srawi   0, 0, 11
    add     3, 10, 0            # value = adjbase + (amp*tab[idx])>>11
    cmpwi   9, 32
    bne     Lring2_e
    li      4, 0                # last keypoint: wait 0 (loop close)
Lring2_e:
    bl      emitp               # emit (value,wait); advances r6, clobbers r0
    add     7, 7, 8             # idx += k
    andi.   7, 7, 31            # idx &= 31 (32-entry table)
    addi    9, 9, 1
    cmpwi   9, 33
    blt     Lring2
    lwz     0, 0x50(1)
    mtlr    0
    blr

# unit circle * 16384 (cx,cy interleaved s16); angle i = 2*pi*i/32, entry 32
# repeats entry 0 to close the loop so emit_ring can read [0..32] linearly.
Ltab_ell:
    .short   16384,      0,  16069,   3196,  15137,   6270,  13623,   9102
    .short   11585,  11585,   9102,  13623,   6270,  15137,   3196,  16069
    .short       0,  16384,  -3196,  16069,  -6270,  15137,  -9102,  13623
    .short  -11585,  11585, -13623,   9102, -15137,   6270, -16069,   3196
    .short  -16384,      0, -16069,  -3196, -15137,  -6270, -13623,  -9102
    .short  -11585, -11585,  -9102, -13623,  -6270, -15137,  -3196, -16069
    .short       0, -16384,   3196, -16069,   6270, -15137,   9102, -13623
    .short   11585, -11585,  13623,  -9102,  15137,  -6270,  16069,  -3196
    .short   16384,      0

# Fill a synth FObjDesc: r4=desc r5=type r6=ad r7=len. Preserves r4.
fsdesc:
    li      0, 0
    stw     0, 0(4)             # .next
    stw     0, 8(4)             # .startframe = 0.0f
    stb     0, 0xF(4)           # .dummy0
    stw     7, 4(4)             # .length
    stb     5, 0xC(4)           # .type
    li      0, 0x20             # FRAC_S16 | divisor 0
    stb     0, 0xD(4)
    stb     0, 0xE(4)
    stw     6, 0x10(4)          # .ad = synthesized stream
    blr
