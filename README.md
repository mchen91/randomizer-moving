# randomizer-moving

Gecko codes that **randomize Target Test target positions** and animate a subset
as **moving targets** on every playable BTT stage (25 characters). Targets
**Melee NTSC 1.02 (GALE01)**.

## How to read this repo

**Shipping code is hand-written PPC assembly** in `gecko/`. That is what
`build.sh` assembles and what Dolphin loads.

The **C sources are a readable reference** — same role as `flip_stage.c` /
`flip_items.c` in [stage-inverter](https://github.com/mchen91/stage-inverter).
They document the logic and game interaction steps so you can study the
algorithm, then implement an equivalent Gecko hook in asm yourself. The C is
not compiled into the published `.gecko.txt` files.

| Learn from | Ship with |
|------------|-----------|
| `mover_install.c`, `mover_engine.c` | `gecko/mover_install.s`, `gecko/mover_engine.s` |
| `gen_moving_gct.py`, `fobj_vm.py` | Data spliced into the installer blob |
| `entry_install.S`, `entry_engine.S` | Entry prologue in each `.s` file |

## Build

### Prerequisites

**devkitPPC** — PowerPC cross-assembler used by `gecko/build_install.sh` to assemble
`mover_install.s` (and optionally `mover_engine.s`). See
[devkitPro getting started](https://devkitpro.org/wiki/Getting_Started).

macOS:

1. `xcode-select --install` (if you don't have Xcode command-line tools)
2. Install [devkitpro-pacman-installer.pkg](https://github.com/devkitPro/pacman/releases/latest)
3. Reboot (devkitPro recommends this so `/opt/devkitpro` is on your PATH)
4. Install the toolchain:

```bash
sudo dkp-pacman -S devkitPPC
```

5. Confirm `powerpc-eabi-gcc` is available:

```bash
export PATH=/opt/devkitpro/devkitPPC/bin:$PATH
powerpc-eabi-gcc --version
```

`build.sh` prepends that PATH automatically; you only need the export for manual
`gecko/build_*.sh` runs outside `./build.sh`.

**Python 3** — required for `gen_moving_gct.py` and `emit_gecko.py` (stdlib only).

### Assemble

```bash
./build.sh
```

Output: `moving_targets_keyframe.gecko.txt` — import into Dolphin's Gecko code manager.

```bash
./build.sh --seed 42 --out my_run.gecko.txt
./build.sh --engine    # optional table-driven engine GCT
```

## What it does

**Keyframe path** (primary): on each Target Test stage load (`0x801C0A50`):

1. Writes randomized static positions for all 10 breakable targets (within
   bttrandomizer-style bounds and collision exclusions).
2. Attaches native HSD AObj/FObj animations to 3–5 “mover” targets (elevator,
   sweep, diagonal, diamond, park-and-dash patterns).

`gen_moving_gct.py` packs the randomized layout into the Gecko codelist;
`gecko/mover_install.s` installs it at runtime.

**Table-driven engine** (alternate route): per-frame hook on itMato physics
(`0x802D8594`) evaluates parametric motion from an 8-byte-per-target table.
See `mover_engine.c` / `gecko/mover_engine.s`.

Sheik / TSEAK (`0x3C`, `GrTSk`) is skipped — debug-only 3-target stage.

## Source layout

| File | Role |
|------|------|
| `mover_install.c` | **Reference C** — stage-load installer logic |
| `mover_engine.c` | **Reference C** — table-driven per-frame engine |
| `entry_install.S`, `entry_engine.S` | **Reference** Gecko trampolines (hook glue) |
| `gen_moving_gct.py` | Randomizer: patterns, bounds check, blob packer, GCT emitter |
| `fobj_vm.py` | FObj keyframe VM (validates streams match in-game playback) |
| `blob_contract.h`, `gale01.h` | Blob layout, types, GALE01 addresses |
| `btt_bounds.json` | Per-stage placement bounds, exclusions, exceptions |
| `data/stages/GrT*.json` | Target slot list per stage (`slot`, `x`, `y` only) |
| `gecko/mover_install.s` | **Shipping asm** — stage-load installer |
| `gecko/mover_engine.s` | **Shipping asm** — table-driven engine |
| `gecko/mover_synth.inc` | Template synthesis path (`SYNTH=1` asm build) |
| `gecko/build_install.sh`, `gecko/build_engine.sh` | Assemble PPC blobs |
| `emit_gecko.py` | Pack raw binaries into Dolphin C2 text format |

## Codespace

Installer + packed data must fit Dolphin's ~3248 B Gecko cap. `gen_moving_gct.py`
automatically degrades pattern richness until the blob fits (`BLOB_BUDGET` in
`blob_contract.h`).
