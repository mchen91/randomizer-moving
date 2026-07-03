#!/bin/bash
# Assemble gecko/mover_install.s into gecko/mover_install.bin.
# The keyframe GCT is produced by gen_moving_gct.py, which splices randomized
# stage data onto this blob per blob_contract.h / mover_install.s BLOB CONTRACT.
set -e
cd "$(dirname "$0")"
export PATH=/opt/devkitpro/devkitPPC/bin:$PATH

powerpc-eabi-gcc -c mover_install.s -o mover_install.o -mcpu=750
cat > /tmp/mover_install.ld <<'LD'
ENTRY(_entry)
SECTIONS { . = 0x0; .text : {
    mover_install.o(.text.entry)
    mover_install.o(.text)
    mover_install.o(.text.tail)
} /DISCARD/ : { *(.comment) *(.eh_frame) *(.note*) *(.gnu.attributes) } }
LD
powerpc-eabi-ld -T /tmp/mover_install.ld -o mover_install.elf mover_install.o
powerpc-eabi-objcopy -O binary mover_install.elf mover_install.bin

echo -n "reloc: "
powerpc-eabi-objdump -r mover_install.elf | grep -vE "RELOC|^$|file format|OFFSET" || echo "none"
echo -n "undef: "
powerpc-eabi-objdump -t mover_install.elf | grep "\*UND\*" || echo "none"
echo "installer bytes (incl tail nop): $(stat -f%z mover_install.bin)"
