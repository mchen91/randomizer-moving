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
# Entry state: r9=flags r10=end4 r26=spec start (unadvanced), AObjDesc at
# 0x18(1) already filled. Exits by branching to Lnoy (r8 = first desc) or
# Lnext (alloc failure: spec consumed, target stays static).

Lsynth:
    li      3, 48
    lis     4, 0x8037           # HSD_MemAlloc(48) -> r3
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
    bgt     Lsy_diam
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
    addi    26, 26, 3
    cmpwi   9, 2
    bne     Lsysk1
    addi    26, 26, 1
Lsysk1:
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
