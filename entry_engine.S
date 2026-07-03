/*
 * Gecko C2 trampoline for mover_engine (itMato phys hook @ 0x802D8594).
 * r3 = item gobj (caller's argument, preserved through the hook).
 * Replays the displaced prologue insn: mflr r0.
 */
    .section .text.entry, "ax"
    .globl _entry
_entry:
    stwu    1, -0x20(1)
    mflr    0
    stw     0, 0x24(1)
    bl      mover_engine
    lwz     0, 0x24(1)
    mtlr    0
    addi    1, 1, 0x20
    mflr    0
    b       _blob_end

    .section .text.tail, "ax"
    .globl _blob_end
_blob_end:
    nop
