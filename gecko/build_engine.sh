#!/bin/bash
# Build the table-driven moving-target engine (gecko/mover_engine.s).
# Optional alternate deliverable; embeds a demo table in the asm at Ltab.
set -e
cd "$(dirname "$0")"
export PATH=/opt/devkitpro/devkitPPC/bin:$PATH
ROOT="$(cd .. && pwd)"

powerpc-eabi-gcc -c mover_engine.s -o mover_engine.o -mcpu=750
cat > /tmp/mover_engine.ld <<'LD'
ENTRY(_entry)
SECTIONS { . = 0x0; .text : {
    mover_engine.o(.text.entry)
    mover_engine.o(.text)
    mover_engine.o(.text.tail)
} /DISCARD/ : { *(.comment) *(.eh_frame) *(.note*) *(.gnu.attributes) } }
LD
powerpc-eabi-ld -T /tmp/mover_engine.ld -o mover_engine.elf mover_engine.o
powerpc-eabi-objcopy -O binary mover_engine.elf mover_engine.bin

echo -n "reloc: "
powerpc-eabi-objdump -r mover_engine.elf | grep -vE "RELOC|^$|file format|OFFSET" || echo "none"
echo -n "undef: "
powerpc-eabi-objdump -t mover_engine.elf | grep "\*UND\*" || echo "none"

OUT="$ROOT/moving_targets_engine.gecko.txt"
python3 "$ROOT/emit_gecko.py" mover_engine.bin 0x802D8594 \
  "Moving Targets Engine [table-driven demo]" > "$OUT"

S=$(stat -f%z mover_engine.bin)
echo "blob=$S bytes, wrote $OUT ($(wc -l < "$OUT") lines)"
