# POC: per-frame parametric moving target on TZELDA (GrTZd) -- "route 2" of
# MOVING_TARGETS.md. Target slot 199 (spawn (-0.7, 92.6), above the central
# tower top at y=79.96, ceiling at y=115) orbits a circle centered (0, 95),
# radius 13, period 256 frames (~0.32 u/f, inside the vanilla 2-3 u/f
# envelope; the orbit bottom grazes head height on the tower top).
#
# Hook: grTZelda_8022404C @ 0x8022404C -- TZelda's scene-2 per-frame GObj proc
# (StageCallbacks.callback2, registered via HSD_GObj_SetupProc(gobj, cb, 4) in
# Ground_SetupStageCallbacks). Runs exactly once per frame and ONLY on this
# stage, so no stage-id check is needed. Displaced instruction = mflr r0
# (MWCC non-leaf prologue; symbols.txt size 0x34 matches the canonical
# 13-insn two-call body). VERIFY IN DOLPHIN on first run: if the game crashes
# at TZelda load, read 0x8022404C in the debugger -- it must be 7C0802A6.
#
# Motion math (no game trig, no division): parabola sine
#     s(t) = 16*u*(|u|-0.5), u = t-0.5, t in [0,1)   ~= sin(2*pi*t)  (err ~5.6%)
# cos = same curve a quarter period ahead ((ctr+64)&255). The 16x is folded
# into the amplitude constant (Kamp = 16*radius). Phase source is a counter
# word EMBEDDED IN THIS BLOB: the codelist is plain writable MEM1 and the
# codehandler only rewrites the hook word and the final branch-back word each
# frame, so blob-local data persists. Period 256 so the wrap is a mask.
#
# Bone write sequence (proven by the bttrandomizer spawn hook): JObj
# translate.x/y (+0x38/+0x3C) AND cached world mtx tx/ty (+0x50/+0x60), no
# SetMtxDirty -- both copies agree whichever one the engine reads. The Mato
# item copies the bone's world position into it->pos every frame
# (itMato_UnkMotion0_Phys), and hurtbox + render follow it->pos.
#
# Register use: r0 (displaced insn), r4-r8 scratch, r11 pc-base, r12 LR save,
# f0-f12. r3 (the gobj arg) is untouched; LR restored before the displaced
# mflr. Runs pre-prologue, so all volatile regs are dead.

    .section .text.entry, "ax"
    .globl _entry
_entry:
    stwu    1, -0x20(1)         # scratch frame for int->float staging
    mflr    12                  # save caller LR (bl below clobbers it)
    bl      Lpc                 # pc-relative anchor for blob-local data
Lpc:
    mflr    11                  # r11 = Lpc
    mtlr    12                  # LR restored before anything else runs

    lis     5, 0x804A           # stage_info.x280[199] @ 0x8049EC64
    lwz     5, -0x139C(5)       # (stage_info 0x8049E6C8 + 0x280 + 199*4)
    cmpwi   5, 0
    beq     Ldone               # slot empty -> nothing to move

    lwz     4, Lctr-Lpc(11)     # blob-local frame counter
    addi    4, 4, 1
    clrlwi  4, 4, 24            # mod 256
    stw     4, Lctr-Lpc(11)

    lis     6, 0x4330           # u32 -> f64 via the 2^52 exponent trick
    li      7, 0                # (750 has no fcfid)
    stw     6, 8(1)
    stw     6, 0x10(1)
    stw     7, 0x14(1)
    lfd     1, 0x10(1)          # f1 = 2^52
    stw     4, 0xC(1)
    lfd     0, 8(1)
    fsub    0, 0, 1             # f0 = (f64)ctr
    addi    8, 4, 64            # cos phase = sin phase + quarter period
    clrlwi  8, 8, 24
    stw     8, 0xC(1)
    lfd     3, 8(1)
    fsub    3, 3, 1             # f3 = (f64)((ctr+64)&255)

    lfs     2, Kinv-Lpc(11)     # 1/256
    fmuls   0, 0, 2             # ts in [0,1)
    fmuls   3, 3, 2             # tc in [0,1)
    lfs     4, Khalf-Lpc(11)    # 0.5
    fsubs   5, 0, 4             # u = ts - 0.5
    fabs    6, 5
    fsubs   6, 6, 4             # |u| - 0.5
    fmuls   5, 5, 6             # sin(2pi*ts) / 16
    fsubs   7, 3, 4             # v = tc - 0.5
    fabs    8, 7
    fsubs   8, 8, 4
    fmuls   7, 7, 8             # cos(2pi*ts) / 16

    lfs     9, Kamp-Lpc(11)     # 208.0 = 16 * radius(13)
    lfs     10, Kcy-Lpc(11)     # 95.0 = center y (center x = 0)
    fmuls   11, 7, 9            # x = 16r * cos/16
    fmadds  12, 5, 9, 10        # y = cy + 16r * sin/16
    stfs    11, 0x38(5)         # JObj translate.x
    stfs    12, 0x3C(5)         # JObj translate.y
    stfs    11, 0x50(5)         # cached world mtx [0][3]
    stfs    12, 0x60(5)         # cached world mtx [1][3]

Ldone:
    addi    1, 1, 0x20
    mflr    0                   # displaced original instruction
    b       _blob_end

Lctr:
    .long   0                   # frame counter (blob-local mutable state)
Kinv:
    .long   0x3B800000          # 1/256
Khalf:
    .long   0x3F000000          # 0.5
Kamp:
    .long   0x43500000          # 208.0 = 16 * radius
Kcy:
    .long   0x42BE0000          # 95.0 = orbit center y

    .section .text.tail, "ax"
    .globl _blob_end
_blob_end:
    nop                         # codehandler's branch-back overwrites the
                                # blob's final word; execution lands here
