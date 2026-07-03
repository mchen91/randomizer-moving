/*
 * Gecko C2 trampoline for mover_install (stage-load hook @ 0x801C0A50).
 * Replays the instruction displaced by the hook: lwz r0, 0x64(r1).
 *
 * In the shipping blob, mover_install() reads the directory at label Ldir
 * (PC-relative in asm). A C Gecko port would pass that address explicitly or
 * link it as a symbol at the end of the code section.
 */
    .section .text.entry, "ax"
    .globl _entry
_entry:
    bl      mover_install
    lwz     0, 0x64(1)
    b       _blob_end

    .section .text.tail, "ax"
    .globl _blob_end
_blob_end:
    nop
