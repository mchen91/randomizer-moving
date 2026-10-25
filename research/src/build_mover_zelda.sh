#!/bin/bash
# Build the TZelda moving-target POC (gecko/mover_zelda.s) into
# ../moving_target_zelda_poc.gecko.txt. Hook 0x8022404C (grTZelda_8022404C).
set -e
cd "$(dirname "$0")"
export PATH=/opt/devkitpro/devkitPPC/bin:$PATH

powerpc-eabi-gcc -c mover_zelda.s -o mover_zelda.o -mcpu=750
cat > /tmp/mover_zelda.ld <<'LD'
ENTRY(_entry)
SECTIONS { . = 0x0; .text : {
    mover_zelda.o(.text.entry)
    mover_zelda.o(.text)
    mover_zelda.o(.text.tail)
} /DISCARD/ : { *(.comment) *(.eh_frame) *(.note*) *(.gnu.attributes) } }
LD
powerpc-eabi-ld -T /tmp/mover_zelda.ld -o mover_zelda.elf mover_zelda.o
powerpc-eabi-objcopy -O binary mover_zelda.elf mover_zelda.bin

echo -n "reloc: "; powerpc-eabi-objdump -r mover_zelda.elf | grep -vE "RELOC|^$|file format|OFFSET" || echo "none"
echo -n "undef: "; powerpc-eabi-objdump -t mover_zelda.elf | grep "\*UND\*" || echo "none"

python3 emit_gecko.py mover_zelda.bin 0x8022404C \
  | sed '1s/.*/$Moving Target POC - TZelda orbit [slot 199]/' \
  > ../moving_target_zelda_poc.gecko.txt

S=$(stat -f%z mover_zelda.bin)
echo "blob=$S bytes, GCT lines: $(wc -l < ../moving_target_zelda_poc.gecko.txt)"
