# Keyframe moving-target INSTALLER -- route 3' of MOVING_TARGETS.md.
# One-time stage-load hook that (a) writes randomized static positions into
# every target bone, and (b) for mover slots builds HSD_FObjDesc/HSD_AObjDesc
# descriptors on the stack and attaches a native animation via
# HSD_AObjLoadDesc + HSD_AObjReqAnim (BOTH are required: LoadDesc leaves
# every FObj in interpreter state 0 -- a hard no-op -- and ReqAnim is what
# arms the state machine; found the hard way in Dolphin).
#
# Hook: 0x801C0A50, Ground_801C0800 epilogue -- the PROVEN flip_items hook
# (entry2.S): displaced insn = lwz r0, 0x64(r1). Epilogue context: all
# volatile regs + LR are dead, so game calls are safe.
#
# Assemble plain for the raw-stream-only installer, or with
# -Wa,--defsym,SYNTH=1 to add the template-synthesis path (mover_synth.inc):
# spec flag bit7 selects a synthesized pattern (elevator/sweep/diagonal/
# diamond) built at install time into an HSD_MemAlloc'd buffer from 1-2
# param bytes, instead of carrying literal stream bytes in the codelist.
#
# Game facts (verified in decomp; addresses = GALE01 v1.02 symbols.txt):
#   HSD_AObjLoadDesc 0x8036439C  HSD_AObjRemove 0x803644CC
#   HSD_AObjReqAnim  0x8036410C  HSD_MemAlloc   0x8037F1E4
#   AOBJ_LOOP 0x20000000; jobj->aobj +0x7C; stage id 0x8049E750;
#   stage_info.x280[199] 0x8049EC64. LoadDesc copies desc fields, retains
#   only fobjdesc->ad (streams persist in the codelist / alloc'd RAM;
#   descriptors are stack temporaries). HSD_JObjAnimAll ticks every scene
#   per frame on every stage (Ground_801C1CD0).
#
# Data appended by gen_moving_gct.py (BLOB CONTRACT):
#   Ldir: 26 x u16 offset from Ldir (0xFFFF = stage untouched), index =
#         stage_id - 0x28. Per-stage blob (4-aligned):
#     u32 presence mask (bit i = slot 199+i has a target)
#     u32 mover mask    (subset of presence)
#     s16 bx, s16 by    (stage coordinate base)
#     per present slot, ascending: s8 rx, s8 ry  (coord = base + 2*r)
#     per mover slot, ascending, one spec:
#       RAW:   u8 flags (bit7=0; bit0 TRAX, bit1 TRAY), u8 end4,
#              per track (TRAX first): u8 frac_value, u8 len, u8 stream[len]
#       SYNTH: u8 0x80|pattern, u8 end4, params (see mover_synth.inc)
#   The assembled code ends [lwz r0,0x64(r1)] [b _blob_end] with Ldir right
#   after the branch; the generator bumps that branch over the data and
#   asserts word[2] == 0x48000005 (bl Lpc) as a drift guard.
#
# Frame map (0x80): +0x08 int->f64 staging, +0x10 2^52+2^31 magic,
#   +0x18 AObjDesc, +0x28 FObjDesc A, +0x3C FObjDesc B, +0x50 f32 zero
#   staging, +0x54 xi (int), +0x58 yi (int), +0x60 stmw r24-r31.
# Nonvolatile: r31 pc base, r30 coord cursor, r29 presence, r28 movers,
# r27 slot index, r26 spec cursor, r25 current jobj, r24 stage blob.

    .section .text.entry, "ax"
    .globl _entry
_entry:
    stwu    1, -0x80(1)
    stmw    24, 0x60(1)
    bl      Lpc
Lpc:
    mflr    31

    lis     3, 0x804A           # internal stage id @ 0x8049E750
    lwz     3, -0x18B0(3)
    addi    3, 3, -0x28
    cmplwi  3, 25
    bgt     Lout                # not a Target Test stage
    addi    4, 31, Ldir-Lpc
    slwi    5, 3, 1
    lhzx    5, 4, 5
    cmplwi  5, 0xFFFF
    beq     Lout                # no data for this stage
    add     24, 4, 5            # r24 = stage blob

    lis     6, 0x4330           # int->float staging: 8(1) hi word fixed,
    stw     6, 8(1)             # 0x10(1) = 2^52 + 2^31 (signed magic)
    stw     6, 0x10(1)
    lis     6, 0x8000
    stw     6, 0x14(1)

    lwz     29, 0(24)           # presence mask
    lwz     28, 4(24)           # mover mask
    addi    30, 24, 12          # coord cursor
    addi    26, 24, 12          # spec cursor = coords + 2*popcount
    mr      5, 29
Lpop:
    cmpwi   5, 0
    beq     Lpopd
    andi.   0, 5, 1
    beq     Lpop1
    addi    26, 26, 2
Lpop1:
    srwi    5, 5, 1
    b       Lpop
Lpopd:

    li      27, 0
Lslot:
    srw     0, 29, 27
    andi.   0, 0, 1
    beq     Lnext               # slot not present
    lis     25, 0x804A
    addi    25, 25, -0x139C
    slwi    0, 27, 2
    lwzx    25, 25, 0           # r25 = jobj (may be NULL, guarded below)
    cmpwi   25, 0
    beq     Lnoaobj
    lwz     4, 0x7C(25)         # detach + free any vanilla AObj so stage
    cmpwi   4, 0                # anims (incl. PATH) can't fight ours;
    beq     Lnoaobj             # present slots are the only populated bones
    li      0, 0
    stw     0, 0x7C(25)
    mr      3, 4
    lis     4, 0x8036           # HSD_AObjRemove(old)
    ori     4, 4, 0x44CC
    mtctr   4
    bctrl
Lnoaobj:

    lbz     5, 0(30)            # static position: coord = base + 2*s8
    extsb   5, 5
    lha     6, 8(24)
    slwi    5, 5, 1
    add     3, 6, 5
    stw     3, 0x54(1)          # xi (int, for synth patterns)
    bl      tofloat
    fmr     2, 0                # f2 = x
    lbz     5, 1(30)
    extsb   5, 5
    lha     6, 10(24)
    slwi    5, 5, 1
    add     3, 6, 5
    stw     3, 0x58(1)          # yi
    bl      tofloat             # f0 = y
    addi    30, 30, 2
    cmpwi   25, 0
    beq     Lnostat
    stfs    2, 0x38(25)         # translate + cached world mtx (randomizer-
    stfs    0, 0x3C(25)         # proven write pair; identity parents)
    stfs    2, 0x50(25)
    stfs    0, 0x60(25)
Lnostat:

    srw     0, 28, 27
    andi.   0, 0, 1
    beq     Lnext               # not a mover
    lbz     9, 0(26)            # spec flags (r26 not yet advanced)
    lbz     10, 1(26)           # end4
    lis     0, 0x2000           # AObjDesc.flags = AOBJ_LOOP
    stw     0, 0x18(1)
    slwi    3, 10, 2
    bl      tofloat
    stfs    0, 0x1C(1)          # .end_frame = end4*4
    li      0, 0
    stw     0, 0x24(1)          # .obj_id = 0
.ifdef SYNTH
    andi.   0, 9, 0x80
    bne     Lsynth              # synthesized pattern (mover_synth.inc)
.endif
    addi    26, 26, 2           # raw spec: consume flags/end4
    li      8, 0                # r8 = first FObjDesc (0 = none yet)
    andi.   0, 9, 1
    beq     Lnox
    addi    4, 1, 0x28          # TRAX -> desc A
    li      5, 5
    bl      parse_track
    mr      8, 4
Lnox:
    andi.   0, 9, 2
    beq     Lnoy
    cmpwi   8, 0
    beq     Lyfirst
    addi    4, 1, 0x3C          # TRAY -> desc B, chained after A
    li      5, 6
    bl      parse_track
    stw     4, 0(8)             # A.next = B
    b       Lnoy
Lyfirst:
    addi    4, 1, 0x28          # TRAY only -> desc A
    li      5, 6
    bl      parse_track
    mr      8, 4
Lnoy:
    stw     8, 0x20(1)          # AObjDesc.fobjdesc
    addi    3, 1, 0x18
    lis     4, 0x8036           # HSD_AObjLoadDesc(&desc)
    ori     4, 4, 0x439C
    mtctr   4
    bctrl
    cmpwi   25, 0
    beq     Lnext
    stw     3, 0x7C(25)         # jobj->aobj = our animation
    li      0, 0                # HSD_AObjReqAnim(aobj, 0.0f): REQUIRED --
    stw     0, 0x50(1)          # arms the FObj state machines (state 0 is
    lfs     1, 0x50(1)          # a no-op) + sets FIRST_PLAY, mirroring
    lis     4, 0x8036           # vanilla JObjReqAnimAll after AddAnimAll.
    ori     4, 4, 0x410C
    mtctr   4
    bctrl

Lnext:
    addi    27, 27, 1
    cmplwi  27, 21
    blt     Lslot

Lout:
    lmw     24, 0x60(1)
    addi    1, 1, 0x80
    lwz     0, 0x64(1)          # displaced original instruction (epilogue
    b       _blob_end           # LR reload); generator bumps this branch

# f0 = (f64)(s32)r3 via 2^52+2^31 magic (750 has no fcfid). Clobbers r0, f1.
tofloat:
    xoris   0, 3, 0x8000
    stw     0, 0xC(1)
    lfd     1, 0x10(1)
    lfd     0, 8(1)
    fsub    0, 0, 1
    blr

# Fill FObjDesc at r4 (type r5) from the spec cursor r26: reads u8 frac,
# u8 len, then leaves ad -> stream and advances r26 past it. Clobbers r0,r6.
parse_track:
    li      6, 0
    stw     6, 0(4)             # .next = 0
    stw     6, 8(4)             # .startframe = 0.0f
    stb     6, 0xF(4)           # .dummy0
    stb     5, 0xC(4)           # .type
    lbz     6, 0(26)
    stb     6, 0xD(4)           # .frac_value
    stb     6, 0xE(4)           # .frac_slope (unused by CON/LIN/SPL0)
    lbz     6, 1(26)
    stw     6, 4(4)             # .length = stream bytes
    addi    26, 26, 2
    stw     26, 0x10(4)         # .ad = stream (lives in this codelist blob)
    add     26, 26, 6
    blr

.ifdef SYNTH
    .include "mover_synth.inc"
.endif

Ldir:                           # generator appends: directory + stage blobs

    .section .text.tail, "ax"
    .globl _blob_end
_blob_end:
    nop
