#!/bin/bash
# Assemble the keyframe-mover installer (gecko/mover_install.s) into
# mover_install.bin. The final GCT is produced by gen_moving_gct.py, which
# appends the generated directory + stage blobs per its BLOB CONTRACT.
set -e
cd "$(dirname "$0")"
export PATH=/opt/devkitpro/devkitPPC/bin:$PATH

cat > /tmp/mover_install.ld <<'LD'
ENTRY(_entry)
SECTIONS { . = 0x0; .text : {
    IN(.text.entry)
    IN(.text)
    IN(.text.tail)
} /DISCARD/ : { *(.comment) *(.eh_frame) *(.note*) *(.gnu.attributes) } }
LD

build_one() {  # $1 = output stem, $2.. = extra as flags
    local stem=$1; shift
    powerpc-eabi-gcc -c mover_install.s -o "$stem.o" -mcpu=750 "$@"
    sed "s/IN(/$stem.o(/" /tmp/mover_install.ld > "/tmp/$stem.ld"
    powerpc-eabi-ld -T "/tmp/$stem.ld" -o "$stem.elf" "$stem.o"
    powerpc-eabi-objcopy -O binary "$stem.elf" "$stem.bin"
    echo -n "$stem reloc: "; powerpc-eabi-objdump -r "$stem.elf" | grep -vE "RELOC|^$|file format|OFFSET" || echo "none"
    echo -n "$stem undef: "; powerpc-eabi-objdump -t "$stem.elf" | grep "\*UND\*" || echo "none"
    echo "$stem code bytes (incl tail nop): $(stat -f%z "$stem.bin")"
}

build_one mover_install
build_one mover_install_synth -Wa,--defsym,SYNTH=1
