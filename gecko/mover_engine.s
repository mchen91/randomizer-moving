# Table-driven moving-target engine for ALL Target Test stages -- the
# compressed successor to mover_zelda.s (route 2 of MOVING_TARGETS.md).
# One shared evaluator + a packed 8-byte-per-target spec table. Engine code
# is constant (~440 B); each moving target costs exactly 8 B of table.
#
# Hook: itMato_UnkMotion0_Phys @ 0x802D8594 (it/items/itmato.c) -- the target
# item's physics callback. Runs once per frame for every LIVE target on every
# TT stage, with the item gobj in r3, and stops when the target breaks or the
# game pauses. This is what makes one code cover all 25 stages: no per-stage
# hooks, no stage-load hook, no frame loop of our own. We write the bone
# BEFORE the displaced prologue insn, so the function body's lb_8000B1CC ->
# it->pos copy picks up the new position the same frame.
# Displaced instruction = mflr r0 (MWCC non-leaf prologue: the function calls
# lb_8000B1CC; symbols.txt size 0x58). VERIFY IN DOLPHIN on first boot: the
# word at 0x802D8594 must be 7C0802A6.
#
# Phase source: the VI retrace counter @ 0x804D7420 (what VIGetRetraceCount
# returns -- that function is just `lwz r3, ...; blr`). Monotonic, +1 per
# frame at 60 fps, so position is a pure function of real time: zero mutable
# state in the blob. Quirks: phase16 = (F << ls) means a lap wraps every
# 65536>>ls frames; lag frames advance the retrace counter faster than game
# frames (targets skip slightly ahead -- cosmetic); pause freezes targets
# because item phys stops running, not because we check for pause.
#
# Table entry (8 B), sentinel word1 == 0xFFFFFFFF:
#   word1: [31:21] key = (stage_id-0x28)*32 + (slot-199)    (11 bits)
#          [20:18] ls-4, where period = 65536>>ls frames    (ls 4..11 ->
#                  period 4096,2048,1024,512,256,128,64,32)
#          [17:16] shape (0 = ellipse; 1-3 reserved)
#          [15:8]  phase offset, 1/256ths of a lap
#          [7:0]   spare
#   word2: [31:24] cx s8, 2-unit steps (+-254)   [23:16] cy s8, 2-unit steps
#          [15:8]  ax u8, 1-unit amplitude       [7:0]   ay u8
#   x = 2*cx + ax*cos(2pi*t),  y = 2*cy + ay*sin(2pi*t)
#   ax=ay: circle; ay=0: horizontal line; ax=0: vertical line (cos/sin via
#   the parabola approximation, max err 5.6% of amplitude).
#
# Per-target cost beyond the table: none. Engine finds this item's bone
# (Item.xDD4 itemVar.mato.x0), derives its slot by scanning
# stage_info.x280[199..219] for the matching pointer, builds the key from the
# stage id (@ 0x8049E750, guarded to the TT range 0x28..0x41), and walks the
# table; first match wins. Unmatched targets stay vanilla-static. On the 17
# stages with vanilla movers, entries for ANIMATED slots will fight the
# stage animation (it re-ticks the bone after us) -- point entries at static
# slots there, or add the AObj-nulling load hook from MOVING_TARGETS.md.
#
# Bone write: JObj translate x/y (+0x38/+0x3C) AND cached world mtx tx/ty
# (+0x50/+0x60), the randomizer-proven sequence. (Its correctness in prod
# also proves target bones have identity parent transforms: it writes world
# coords into the LOCAL translate and positions come out right on all 25
# stages.)
#
# Registers (pre-prologue, all volatile): r3 PRESERVED (the gobj arg);
# r0/r4-r12/ctr/cr0 scratch; LR held in r12 until the end. f1=2^52+2^31
# convert magic, f2=1/65536, f3=2.0, f4=0.5, f13=16.0; f0,f5-f12 scratch.

    .section .text.entry, "ax"
    .globl _entry
_entry:
    stwu    1, -0x20(1)
    mflr    12                  # caller LR (internal bl's clobber LR)
    bl      Lpc
Lpc:
    mflr    11                  # r11 = Lpc (pc-relative base)

    lwz     4, 0x2C(3)          # Item* = gobj->user_data
    lwz     4, 0xDD4(4)         # bone jobj = itemVar.mato.x0
    cmpwi   4, 0
    beq     Ldone

    lis     5, 0x804A           # scan stage_info.x280[199..219] for this
    addi    5, 5, -0x139C       # bone; index -> slot key part
    li      6, 21
    mtctr   6
    li      7, 0                # r7 = slot index 0..20
Lscan:
    lwz     6, 0(5)
    cmpw    6, 4
    beq     Lgotslot
    addi    5, 5, 4
    addi    7, 7, 1
    bdnz    Lscan
    b       Ldone               # not a tracked slot (paranoia)
Lgotslot:
    lis     6, 0x804A
    lwz     6, -0x18B0(6)       # stage id @ 0x8049E750
    addi    6, 6, -0x28
    cmplwi  6, 25               # guard: TT stages only (Mato elsewhere --
    bgt     Ldone               # e.g. leftover GrTe -- must not fake a key)
    slwi    6, 6, 5
    or      7, 7, 6             # r7 = key = stage*32 + slot

    lis     8, 0x804D
    lwz     8, 0x7420(8)        # r8 = VI retrace count (frame source)

    lis     6, 0x4330           # staging slots for signed int->f64:
    stw     6, 8(1)             # 8(1)  = 0x43300000 ........ (per-convert)
    stw     6, 0x10(1)
    lis     0, 0x8000
    stw     0, 0x14(1)          # 0x10(1) = 2^52 + 2^31 magic
    lfd     1, 0x10(1)          # f1 = magic
    lfs     2, Kinv-Lpc(11)     # 1/65536
    lfs     3, K2-Lpc(11)       # 2.0 (center scale)
    lfs     4, Khalf-Lpc(11)    # 0.5
    lfs     13, K16-Lpc(11)     # 16.0 (un-scales the parabola sine)

    addi    9, 11, Ltab-Lpc     # r9 = table cursor
Lloop:
    lwz     10, 0(9)
    cmpwi   10, -1
    beq     Ldone               # sentinel: no spec for this target
    rlwinm  6, 10, 11, 21, 31   # entry key = word1 >> 21
    cmpw    6, 7
    beq     Lmatch
    addi    9, 9, 8
    b       Lloop

Lmatch:
    rlwinm  5, 10, 14, 29, 31   # (word1 >> 18) & 7
    addi    5, 5, 4             # ls
    slw     6, 8, 5             # F << ls
    rlwinm  0, 10, 0, 16, 23    # phase8 << 8
    add     6, 6, 0
    clrlwi  6, 6, 16            # sin phase16
    addi    0, 6, 0x4000
    clrlwi  0, 0, 16            # cos phase16 (quarter lap ahead)
    bl      sin16               # f5 = sin(2pi*ph/65536) / 16
    fmr     6, 5                # f6 = sin/16
    mr      6, 0
    bl      sin16               # f5 = cos/16

    lwz     10, 4(9)            # word2: cx cy ax ay
    srawi   6, 10, 24           # cx (s8)
    bl      tofloat
    fmr     8, 0                # f8 = cx
    slwi    6, 10, 8
    srawi   6, 6, 24            # cy (s8)
    bl      tofloat
    fmr     9, 0                # f9 = cy
    rlwinm  6, 10, 24, 24, 31   # ax (u8)
    bl      tofloat
    fmr     10, 0               # f10 = ax
    clrlwi  6, 10, 24           # ay (u8)
    bl      tofloat             # f0 = ay

    fmuls   12, 10, 5           # ax * cos/16
    fmuls   12, 12, 13          # * 16
    fmadds  12, 8, 3, 12        # x = 2*cx + ax*cos
    fmuls   7, 0, 6             # ay * sin/16
    fmuls   7, 7, 13
    fmadds  7, 9, 3, 7          # y = 2*cy + ay*sin
    stfs    12, 0x38(4)         # JObj translate.x
    stfs    7, 0x3C(4)          # JObj translate.y
    stfs    12, 0x50(4)         # cached world mtx [0][3]
    stfs    7, 0x60(4)          # cached world mtx [1][3]

Ldone:
    mtlr    12                  # caller LR back before the displaced insn
    addi    1, 1, 0x20
    mflr    0                   # displaced original instruction
    b       _blob_end

# f0 = (float)r6, signed, via the 2^52+2^31 trick (no fcfid on the 750).
# Clobbers r0. High word of 8(1) is pre-set to 0x43300000.
tofloat:
    xoris   0, 6, 0x8000
    stw     0, 0xC(1)
    lfd     0, 8(1)
    fsub    0, 0, 1
    blr

# f5 = sin(2pi * r6/65536) / 16, parabola approximation s = u*(|u|-0.5),
# u = t-0.5. Clobbers f5, f7, r0.
sin16:
    xoris   0, 6, 0x8000
    stw     0, 0xC(1)
    lfd     5, 8(1)
    fsub    5, 5, 1             # (f64)phase16
    fmuls   5, 5, 2             # t in [0,1)
    fsubs   5, 5, 4             # u
    fabs    7, 5
    fsubs   7, 7, 4             # |u| - 0.5
    fmuls   5, 5, 7
    blr

Kinv:
    .long   0x37800000          # 1/65536
Khalf:
    .long   0x3F000000          # 0.5
K2:
    .long   0x40000000          # 2.0
K16:
    .long   0x41800000          # 16.0

# ---- spec table: 8 B per moving target, 0xFFFFFFFF sentinel ----
# Demo: 3 movers on TZelda (id 0x3E, stage idx 22) + 1 on TMario (0x28, idx 0).
Ltab:
    # TZd slot 199 (idx 0): circle r=13 around (0,96), period 256f, ~0.32 u/f
    # key=22*32+0=704 -> 704<<21 | (8-4)<<18
    .long   0x58100000
    .long   0x00300D0D          # cx=0 cy=48(*2=96) ax=13 ay=13
    # TZd slot 204 (idx 5): horizontal bob +-8 at (0,60), period 128f
    .long   0x58B40000          # key=709, ls=9
    .long   0x001E0800          # cx=0 cy=30(*2=60) ax=8 ay=0
    # TZd slot 200 (idx 1): vertical bob +-8 at (-124,-30), period 512f
    .long   0x582C0000          # key=705, ls=7
    .long   0xC2F10008          # cx=-62(*2=-124) cy=-15(*2=-30) ax=0 ay=8
    # TMr slot 199 (idx 0): circle r=8 around (98,6), period 512f, ~0.1 u/f
    .long   0x000C0000          # key=0, ls=7
    .long   0x31030808          # cx=49(*2=98) cy=3(*2=6) ax=8 ay=8
    .long   0xFFFFFFFF          # sentinel

    .section .text.tail, "ax"
    .globl _blob_end
_blob_end:
    nop                         # codehandler overwrites the blob's final
                                # word with the branch back to hook+4
