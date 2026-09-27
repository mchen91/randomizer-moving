# Design notes

Facts from the July 2026 sessions that produced this repo, plus the curve experiments that continued in `melee-mods` afterward. Read this before changing the installer, the blob, or the generator. Several wrong turns look reasonable and were already paid for.

The code was written in Claude Code with cwd `~/personal/melee-mods`. This directory was cut out of that tree on July 3, 2026, as a learning repo: the C files explain the steps, the asm is what Dolphin runs. Transcripts:

| When | What | File |
|---|---|---|
| Jul 2, afternoon | How vanilla targets move | `~/.claude/projects/-Users-michael-chen-personal-melee-mods/df1c744c-d2d6-4cff-8d59-50c729e8a116.jsonl` |
| Jul 2 night through Jul 3 morning | Zelda POC, parametric engine, keyframe installer, the frozen-mover bug, compression levers | `.../4fd75916-df7b-4a20-b2ca-9c6ffc84eb48.jsonl`, resumed as `.../d82fb14b-62ef-4ec9-8d65-e0fc9d8ebf9d.jsonl` |
| Jul 3, 1:30am | This repo extracted. C is reference, asm ships | Cursor chat [randomizer-moving repo](17785523-31d6-469b-8f0f-2be05b67623f) in the melee-mods project |
| Jul 3, afternoon | Ellipses, then lissajous. Dolphin showed the playback bugs | `.../dc56f40d-34c0-492a-8bd5-40b2d6dca508.jsonl`, `.../32d8f04b-1ad8-4b9a-bccb-63826106d83d.jsonl` |
| Jul 5 | How the ellipse emitter works, and how Pichu's vanilla circle is actually stored | `.../caf018ac-a404-4847-845c-33d24b1c694c.jsonl` |

The longer research writeup, `MOVING_TARGETS.md`, lives in `melee-mods` under `moving-targets/` after a July 4 reorg. It is not in this repo.

## What `./build.sh` actually produces

One Gecko code, `moving_targets_keyframe.gecko.txt`. The layout is chosen in Python from `--seed` (default 1) and baked into the file. Entering a stage does not roll new positions. The same seed writes the same file.

`gecko/mover_install.s` is assembled, then `gen_moving_gct.py` appends a data blob and patches one branch so it skips the data. The installer has no RNG. It walks the blob.

`./build.sh` does not pass `SYNTH=1`. `gecko/mover_synth.inc` is assembled only with that flag, and the generator in this tree never emits synth specs. The shipping path is raw FObj streams in the codelist.

`./build.sh --engine` is a different code, `moving_targets_engine.gecko.txt`. It is the parametric demo, with a hardcoded table for a few Zelda and Mario targets. It does not randomize the 25 stages. Do not treat it as an alternate build of the keyframe code.

The C files (`mover_install.c`, `mover_engine.c`, the headers, `entry_*.S`) are not compiled. They exist so a reader can see the steps. `stage-inverter` uses the same split.

## Runtime model

A target item stores no motion of its own. `itMato_UnkMotion0_Phys` copies its bone's world position into `it->pos` at item+0x4C every frame. Hurtboxes come from that. Spawn writes the bone pointer into `stage_info.x280[199..219]` on every Target Test stage, static ones included. Slot numbers in the stage JSON are those indices. Sheik / TSEAK (`0x3C`, internal id, 3 targets, debug-only) is skipped. There are 25 playable stages, not 27.

Position exists in three caches after the animation tick:

1. Bone world matrix, translation column. `stage_info.x280[199]` is `0x8049EC64`. Stage id is the word at `0x8049E750`.
2. `it->pos` at +0x4C. This is what collision uses. The item is gone after the target breaks. The bone keeps animating.
3. Hurtbox capsules derived from `it->pos`.

Nothing gameplay-related reads the FObj bytes except the animation tick. There is no lookahead cache. Future position means evaluating the tracks yourself.

The write that places a target, already proven by bttrandomizer, is JObj translate (+0x38, +0x3C) and the cached world-matrix translation (+0x50, +0x60). Target bones have identity parents, so writing world coordinates into the local translate lands correctly on all 25 stages.

`Ground_801C1CD0` calls `HSD_JObjAnimAll` every frame on every stage, including the eight with no vanilla movers. Static stages are static because no AObj is attached, not because the ticker is absent.

## How vanilla motion is stored

Survey of all `GrT*.dat` files: 55 animated targets on 17 of 25 stages. Eight stages are fully static: Kirby, Bowser, Link, Mario, Ness, Jigglypuff, Samus, Zelda.

| Kind | Count | What the bytes are |
|---|---|---|
| Keyframes on the target's own bone (TRAX/TRAY) | 50 | Compressed `(value, wait)` streams. This is almost everything. |
| PATH on the target bone | 3 | Falcon x2, Game & Watch x1. The track is a scalar u in [0,1] along an arc-length Catmull-Rom spline. |
| Parent bone has the PATH | 2 | Mewtwo. The target's own keys are a small wiggle. Stage JSON understates their travel because extraction samples the child bone and does not compose the parent spline. |

Sustained vanilla speed is mostly under 3 units/frame. Falco slot 203 is the outlier, a park-and-dash at about 22 to 52 u/f. Loop periods run from a few seconds to about 40. Ganondorf's conveyors glide and then wrap hundreds of units in one frame. Game & Watch's movers have sustained speed 0. They teleport between LCD poses.

PATH targets are relative to a reference joint on the AObj (`aobj->hsd_obj`), multiplied by that joint's live world matrix. On these stages the reference joint is a standalone joint outside every scene tree, used as a fixed spline anchor. Falco slot 205 looks like it orbits a platform. The platform is static and the association is level design. That is why stage inversion (`scale.y = -1` on scene roots) left the orbit behind: the ref joint never flipped. Nulling `jobj->aobj` kills keyframe tracks and the spline override together.

Pichu's spinning target is not a 32-gon and it is not 81,000 of anything. TRAX is 319 bytes and 81 keys, TRAY is 405 bytes and 81 keys. Interpolation is SPL0/SPL. One revolution is 4 keys over 40 frames: X is a cosine between -10 and +10 with slope ±1 at the zero crossings, Y is the same wave 90 degrees later, radius 10. Nintendo concatenated about 20 copies to fill an `endframe` of 799 instead of looping one revolution. One revolution of both axes is about 32 bytes. The whole baked track is about 730 bytes. An FObj has no "repeat this cycle" opcode, so either the AObj loops one revolution or the author pastes the revolution out by hand.

## FObj stream rules

These were gotten wrong once and then checked against the VM. Do not re-derive them from a table that includes a frame column. The frame column is a running sum. The file stores deltas.

```
group  := opbyte contbyte* (value wait) x count
opbyte := low nibble opcode, bits 4-6 = (count-1) low bits, bit 7 = count continues
wait   := 7-bit varint, high bit continues. SLP emits no wait.
```

Opcodes: CON 1, LIN 2, SPL0 3, SPL 4, SLP 5. Track types used here: TRAX 5, TRAY 6. PATH is 4.

A key's wait is the time until the next key. During that window the VM interpolates toward the next key's value. DK's elevator made this invisible because it is symmetric. Falco's teleporter (slot 203) is the case that shows it: long holds on a station, then a 3 to 8 frame LIN dash.

Values are little-endian on a big-endian machine. `sthbrx` in the synth emitter is that fact in asm.

Past the last pair the VM replays the last segment and then extrapolates. For a seamless loop, `endframe` must equal the sum of the waits, and the last value must equal the first. A two-point bob (`y0`, `y0+amp`) sawtooths, because the loop snaps from the top back to the start. The third key, returning to `y0` with wait 0, is what makes an elevator a ping-pong. A ring needs the same extra closing key. That is why a 32-gon is 33 keys.

SPL0 is Hermite with slope 0. Velocity is zero at every key. An 8-point SPL0 "circle" stops at every vertex. That was visible in Dolphin and is why later ellipses switched to LIN. Two-axis SPL0 with the same easing is a diamond with smooth stops, not a circle. A real spline circle needs SPL with nonzero tangents, which is what Pichu does. SPL0 on a ring makes motion worse.

`HSD_AObjLoadDesc` (`0x8036439C`) copies descriptor fields and keeps the stream pointer. Descriptors can live on the stack. Streams must persist, either in the codelist or in `HSD_MemAlloc` (`0x8037F1E4`) memory. LoadDesc leaves every FObj interpreter in state 0. State 0 is a no-op forever. `HSD_AObjReqAnim` (`0x8036410C`) is what arms playback. `HSD_AObjRemove` is `0x803644CC`. AObj pointer on a JObj is +0x7C.

## Routes, and why this one won

1. Reuse vanilla animations. Shuffle bones or phase. Rejected. Only 17 stages have any motion, and the shapes stay fixed.

2. Per-frame parametric mover. Our code computes `x = cx + ax*cos`, `y = cy + ay*sin` and writes the bone every frame. Built as `gecko/mover_engine.s`. Hook is `itMato_UnkMotion0_Phys` at `0x802D8594` (displaced insn `mflr r0`, `0x7C0802A6`). One hook covers every stage, broken targets drop out, pause freezes motion because item physics stops. Phase comes from the VI retrace counter at `0x804D7420`, so the blob has no mutable state. Lag frames advance the counter faster than the game. 8 bytes per target. Engine core about 430 bytes, so 50 movers are about 830 bytes. Shapes in the v1 evaluator: ellipses, circles (`ax==ay`), and axis-aligned bobs (one amplitude 0). Diagonals are not expressible because x and y are hardwired 90 degrees apart. Two shape bits and a spare byte were reserved. Waypoint paths (park-and-dash with arbitrary stations) do not fit a periodic formula. On the 17 animated stages the stage AObj re-ticks the bone after this hook, so a spec on an animated slot fights the vanilla anim unless something nulls that AObj first. The table in this file is a demo, not a generator output.

   The Zelda POC that preceded it (`mover_zelda.s`, hook `0x8022404C`, 224 bytes for one circle) was a mechanics test: hook timing, the bone write, codehandler rules. About 95% of those bytes were fixed machinery. A parabola sine (`16*u*(|u|-0.5)`, max error 5.6%) avoided libm. Period 256 so wrap is a mask. The 750 has no `fcfid`. int-to-float is the `2^52+2^31` trick.

3. Ship prebuilt AObj/FObj graphs in the codelist. Rejected. Absolute pointers and about 140 bytes of structs per target, and the relocation work the `.dat` loader normally does.

3-prime, what shipped. Ship only the raw key streams. At stage load, build descriptors and hand them to the game. Hook `0x801C0A50`, the `Ground_801C0800` epilogue, displaced insn `lwz r0, 0x64(r1)`. Same site as the upside-down Target Test code, so the two codes cannot both be on. The installer removes any vanilla AObj, writes static positions for every present slot, and for movers calls LoadDesc then ReqAnim. After that, `HSD_JObjAnimAll` plays the streams. No per-frame code of ours. Replacing the AObj is also how animated stages stop fighting.

Route 2 remains the right shape for anything that must react every frame (a clock, a video). Route 3-prime is the right shape for a seeded layout of looping paths.

## The frozen-mover bug

First Dolphin test, Fox and Pikachu: every target was at a randomized position, and nothing moved. No crash.

`HSD_AObjLoadDesc` had run. `HSD_AObjReqAnim` had not. The offline checker starts by calling `req()`, so it cannot see this failure. Signature to remember: statics fine, movers frozen, no crash.

The fix is in `mover_install.s`: after storing `jobj->aobj`, call `HSD_AObjReqAnim(aobj, 0.0)`. That boot also proved the hook, the displaced instruction, the directory decode, the static writes, and that LoadDesc from this hook survives. The thread does not record a second Dolphin pass on this repo's raw-stream build after the fix. Later the same load path was booted in `melee-mods` (Kirby sweeps and one ellipse moved correctly), on a newer synth installer.

## Blob contract

Documented in the comment at the top of `gecko/mover_install.s` and in `blob_contract.h`.

- `Ldir`: 26 big-endian `u16` offsets from `Ldir`, index = `stage_id - 0x28`. `0xFFFF` means skip.
- Per stage, 4-byte aligned: `u32` presence mask, `u32` mover mask, `s16` base x, `s16` base y.
- Then per present slot, ascending: `s8 rx, s8 ry`. World position is `base + 2*r`, so a slot must sit within ±254 of the stage center.
- Then per mover, ascending. Raw spec: flags (bit0 TRAX, bit1 TRAY, bit7 clear), `endframe/4`, then per track `frac`, `len`, `stream[len]`. Synth spec, only if the asm was built with `SYNTH=1`: `0x80|pattern`, `end4`, params. Patterns in this tree's `mover_synth.inc` are 0 elevator, 1 sweep, 2 diagonal, 3 diamond. Patterns 4 and 5 were added later in `melee-mods` and are not in this file.

The generator's splice asserts `code_bin[8:12] == 0x48000005` (`bl Lpc` still at that offset) and lengthens the unique forward branch that lands on `Ldir`. Shrinking the prologue without updating that assert will splice into the wrong place. `BLOB_BUDGET` is 3224 payload bytes. Dolphin's Gecko cap is about 3248 including the C2 header and trailer.

Alloc failure in the synth path is silent: the spec is consumed and the target stays at its static position.

## Generator

`gen_moving_gct.py` owns positions, which slots move, pattern choice, amplitude, and period. Bounds come from `btt_bounds.json` (rect, exclusion polygons, exception islands), dumped from bttrandomizer's `randomizer.js`. Stage files in `data/stages/` supply the slot list. The `x` and `y` fields in those JSON files are vanilla positions and are not read.

Placement snaps to even coordinates, rejects points outside the rect or inside an exclusion unless an exception island covers them, and gives up after many tries.

Patterns in this tree:

| Pattern | Look | Encoding |
|---|---|---|
| Elevator | Vertical bob, SPL0, 3 keys | One axis, cheapest |
| Sweep | Horizontal bob, same shape | One axis |
| Diagonal | Both axes, Y amplitude is half of X and randomly signed | Two tracks |
| Diamond | 4-vertex SPL0 orbit | Both tracks, heavier |
| Park-and-dash | 3 stations on one axis, park ~88 or 116 frames, dash 8 or 12 | LIN, most bytes. Cap one per stage at the default richness |

Speed caps: 3.5 u/f sustained, 26 u/f inside a dash window. Every swept frame, plus 8 frames past the loop point, is checked through `fobj_vm.py` and a simulation of the AObj loop rewind. Then the packed blob is parsed the way the installer parses it and checked again.

Richness in this file starts at 2 and degrades to 1, then 0, until the blob fits. Levels 2 and 1 use the cheap-biased menu (elevator and sweep weighted 3x, one park-and-dash allowed). Level 2 picks 3 to 5 movers, level 1 picks 3 to 4, level 0 picks 3 and only elevator/sweep. Periods are 240 to 480 frames, so half-legs are often 2-byte varints.

A later `melee-mods` generator added richness 3 (flat weights, two park-and-dashes), `--synth`, and `--short-waits`. Those flags are not in this tree's `gen_moving_gct.py`.

## Compression, measured

On the first all-stages build the data was about 4x the installer. About 600 bytes of code, about 500 bytes of static coordinates, about 1600 bytes of mover streams. When full richness overflowed by about 20 bytes, the generator biased toward elevators and sweeps, capped park-and-dash, and skipped 8-key circles.

Three levers, then actually built in `melee-mods` the morning after this repo was cut:

1. Prefer periods whose waits fit in one varint byte. This did not shrink the artifact. It bought richness. The wait-aware build was larger than baseline because richness 3 then fit.
2. Asm shrink of the installer. Small. Folded into the shared source, so it was not isolated cleanly.
3. Template synthesis. A 12 to 40 byte stream becomes a 3 to 5 byte spec. The installer `HSD_MemAlloc`s a buffer and writes the stream at load. About 680 extra bytes of installer. Net win: smaller GCT at higher richness, with headroom left. Once elevator, sweep, diagonal, and diamond are synthesized, park-and-dash is the only raw stream left, and its waits were already one byte, so lever 1 then does nothing. The "all three levers" file was byte-identical to "lever 3 only".

Synth streams in this `mover_synth.inc` always use `FRAC_S16` and a fixed 2-byte wait encoding, so the emitter has no size special cases. Buffer RAM is free. The codelist is not. The alloc leaks across stage reloads, same accepted pattern as the flip code's per-load copy.

Recommendation from that session: test the synth build first for size and variety, and fall back to the raw-stream build if movers misbehave, because Fox and Pikachu had already exercised the raw path up through the ReqAnim fix.

## Curves, after this repo was cut

These sessions booted Dolphin. The code they produced is in `melee-mods` (`moving-targets/`), not here. `mover_synth.inc` in this repo stops at diamond.

Cheapest ellipse: do not put keypoints in the codelist. Add a synth pattern whose spec is `0x80|pid, end4, ax, ay` (4 bytes, same as a diagonal) and emit the ring into the alloc buffer. The unit-circle table is paid once, in the installer.

What looked bad, and why:

- 8-point SPL0 ellipse. Jagged. Velocity hits zero at each vertex. Not a point-count problem.
- 32-point LIN ring. Constant speed along each chord. Circles look steady. Eccentric ellipses vary linear speed with the orbit, which reads fine. Chord error at 32 sides is under half a percent of the radius.
- Whole-unit keypoints. Dolphin looked wobbly, not faceted. Vertices sat up to about 1 unit off the true ellipse, irregularly. Fix: store values at 1/8 unit with frac tag `FRAC_S16 | 3` (`0x23`). Same stream size. Quantization dropped to about 0.15 units. The game divides by 8 on read.
- 256 points. Pointless. The cos/sin table would eat about 1 KB of the Gecko cap. Each mover's alloc grows to about 2 KB, and alloc failure already drops that mover back to static. Periods are a few hundred frames, so most of the extra segments round to a 0-frame wait and do nothing. 32 points is already near the floor of a few frames per segment.

Lissajous, tilted ellipses, and phase-staggered formations were one synth pattern: walk the same 32-entry table with a per-axis start index and stride. Frame 0 is forced back to the static position so there is no pop. Formations are 2 or 3 movers on one shared ellipse at staggered phases.

Dolphin, Ganondorf: the lissajous teleported and left the stage. Kirby, a static stage, isolated it. Horizontal sweeps were fine. The plain ellipse was a clean loop. The tilted ellipse played for about 32 frames, teleported, and froze. Cause: `emit_ring2` never wrote the LIN packinfo header (`0x82 0x04`) that `emit_ring` writes. The VM parsed key bytes as an opcode.

The check that missed it compared a Python mirror to an asm simulator written from the intended routine, not from the disassembly. `verify_blob` resynthesizes through that same Python mirror, so it cannot catch an asm/Python split. The check that works is a simulator transcribed from the built disassembly.

SPL with real tangents would be smoother than LIN-32 and would not cost codespace, because the keys still land in the alloc buffer. It is not worth it at these radii. The visible error is already sub-pixel, and the emitter would have to emit slope encodings. SPL0 specifically would bring the stutter back.

## Checks that lie

- The Python FObj VM calls `req()` before sampling. A missing `HSD_AObjReqAnim` looks correct offline.
- A byte-identical claim is worthless unless one side was read from the object file.
- Host simulation of the parabola sine and of packed blobs does not prove the displaced instruction at the hook. The bets that were used: `lwz r0, 0x64(r1)` at `0x801C0A50` (already shipped by the flip code), `mflr r0` at `0x802D8594`.
- "81k" in the moving-target CSV means 81 keys, not 81,000 bytes.

## If you extend it

Stay on the keyframe installer unless the new behavior has to change every frame based on game state. New looping shapes should be synth specs if they are used on many stages, and raw streams if they are rare and awkward (park-and-dash is the awkward one). Keep frame 0 equal to the static position. Keep `endframe` equal to the wait sum, last value equal to first. Validate swept frames against `btt_bounds.json`, including the dash segments, not just the stations. If you add emitter asm, diff it against a mirror built from the disassembly before trusting `verify_blob`.

Do not turn `SYNTH=1` on in `build_install.sh` without also teaching `gen_moving_gct.py` to emit `0x80` specs. The installer and the generator have to agree, and this tree's generator only emits raw streams.
