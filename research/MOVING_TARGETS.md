# Moving Targets — data model, specifications, and randomizer viability

Research notes (2026-07) for a moving-target feature on bttrandomizer.com
(source: `~/personal/bttrandomizer`). Everything here was verified against the
decomp (`~/personal/melee`) and the extracted `GrT*.dat` archives; per-target hard
numbers are in `moving_targets.csv`, and `dump_target_anim.py` reproduces any
raw dump shown below.

## Runtime model: targets are bone-followers

A target (It_Kind_Mato) stores **no motion data of its own**. Its entire
physics callback is: if the item has a JObj pointer, copy that bone's world
position into the item (`itMato_UnkMotion0_Phys`, `it/items/itmato.c:46`).

Spawn wiring: `map_head` entries carry (joint_dfs_index, slot) pairs;
`Ground_801C34AC` writes the bone pointers into `stage_info.x280[slot]`, and
`Ground_801C4210` spawns a Mato on every populated slot in **199..219**
(flippers use 252..259). All movement therefore lives in the stage model's
skeletal animation.

### Where positions live while the game runs

Recomputed each frame from the animation data, then cached at three layers —
gameplay code only ever reads the caches:

1. **Bone world matrix** — `jobj->mtx` translation column (`mtx[0][3]`,
   `mtx[1][3]`, `mtx[2][3]`), rebuilt by the HSD anim tick. Exists even for
   broken targets (the bone animates forever). Ready-made accessor:
   `ground.c:1612` — `lb_8000B1CC(stage_info.x280[i], NULL, &out)`.
2. **Item struct** — `it->pos`, Vec3 at **+0x4C** in the Item data struct;
   this is what collision uses. Destroyed with the target.
3. **Hurtbox capsules** derived from it->pos.

Character position for distance math: Fighter `cur_pos`, Vec3 at **fp+0xB0**.
No lookahead exists anywhere at runtime — "position in N frames" requires
evaluating the FObj data yourself (extract_stage.py's ported VM) or ticking a
cloned AObj.

## The three movement mechanisms

Census across the 25 playable stages (TSEAK/Sheik is a 26th via debug/codes,
archive `GrTSk`; leftover `GrTe` is not in the normal roster — a `GrT*` glob can
hit 27 files but there is no 27th playable stage): **55 animated targets on 17 stages**; 8 stages fully static
(Kb Kp Lk Mr Ns Pr Ss Zd). Full roster: `moving_targets.csv`.

| Type | Count | Data | Where |
|---|---|---|---|
| A — keyframes on own bone | 50 | per-axis FObj tracks (TRAX/TRAY...) | everywhere |
| B — spline path on own bone | 3 | PATH FObj drives u∈[0,1] along an HSD_Spline | Falco 205/208, G&W 201 |
| C — parent bone rides spline | 2 | parent has PATH; own bone adds keyframed offset | Mewtwo 204/206 |

Type A is the only place 1-D curves exist; the only true 2-D geometry in
target animation is the B/C spline control-point list. "2D path" type-A
targets are just two independently keyframed axes.

### PATH mechanics and the standalone reference joint (flip-code tie-in)

A PATH track's value is a progress scalar u. Runtime
(`sysdolphin/baselib/jobj.c:191-199`): after the bone's matrix is built
normally, the engine evaluates the spline at u (arc-length-corrected via the
`segLength` table, so linear u = constant world speed), transforms the point
by the **reference joint's live world matrix** (`aobj->hsd_obj`), and
**overwrites** the bone's world translation (parent translation is discarded).

In every TT stage the reference joint is a **standalone joint outside all
scene trees** (parent==0, child==0, flags 0x4000 = SPLINE union), with a fixed
translation. Falco 205's "orbit around a platform" is choreography around a
static platform — nothing structural ties them. This is why the upside-down
flip missed them (scene-root sy=-1 never reaches an out-of-tree joint) and why
`flip_items.c` needs `reflect_path_refs` (negate sy/ty on parentless refs
only; in-tree refs would double-flip).

## FObj stream format (the actual bytes)

Per track: FObj header {next, length, startframe f32, track_type u8,
value_enc u8, slope_enc u8, data ptr}; track types 1-3 ROT, 4 PATH, 5-7 TRA,
8-10 SCA. The stream:

```
stream := group*
group  := opbyte contbyte* (value wait)×count
opbyte := low nibble = opcode (1=CON 2=LIN 3=SPL0 4=SPL 5=SLP 6=KEY)
          bits 4-6   = (count-1) low bits
          bit 7      = count continues into following byte(s), 7 bits each
value  := per header value_enc: float32, or quantized int (kind u8/s8/u16/s16
          in bits 5-7, divisor 2^(bits 0-4)); SPL also reads a slope
wait   := varint (7 bits/byte, bit 7 = continue); SLP keys emit NO wait
```

**Wait semantics (the part that's easy to get backwards):** a key's wait is
the gap from this key to the NEXT key, and the segment interpolates toward
the **next** key's value during that window. `(v, 96)(v, 4)(w, 92)` means:
park at v 96 frames, dash v→w in 4 frames, park at w 92 frames. Opcodes: CON
holds then jumps at wait end; LIN is linear toward next; SPL0/SPL are Hermite
with zero/explicit tangents; SLP sets the incoming tangent of the next key
(zero duration); KEY is an instant set.

### Worked example, type A minimal — DK slot 200 (whole animation = 21 bytes)

```
TRAX: 11 87 ac 02 87 00              = CON×2: (135, 300)(135, 0)   [constant]
TRAY: 23 1e 96 01 e6 96 01 1e 00     = SPL0×3: (15, 150)(115, 150)(15, 0)
TRAZ: 11 00 ac 02 00 00              = CON×2: (0, 300)(0, 0)       [constant]
```
enc: TRAY values u8/2^1 (0x1e=30/2=15, 0xe6=230/2=115); waits are varints
(`ac 02` = 300). Meaning: ease y 15→115 over 150f, back over 150f. Elevator.

### Worked example, type A park-and-dash — Falco slot 203 ("teleporter")

One CON key + one `c2 01` op byte (LIN, count 13) per axis; values raw s8:

```
TRAX pairs: (110,96)(110,4) (55,92)(55,8) (-110,93)(-110,7)
            (55,96)(55,4) (-35,96)(-35,4) (0,96)(0,3) (110,0)
TRAY pairs: (30,96)(30,4) (110,92)(110,8) (-70,93)(-70,7)
            (25,96)(25,4) (0,96)(0,4) (-80,96)(-80,3) (30,0)
```

Six stations — (110,30) (55,110) (-110,-70) (55,25) (-35,0) (0,-80) — parked
~92-96f each, with 3-8 frame LIN dashes between (21.8-51.9 u/f; reads as
teleporting but there are real in-between frames, hitbox included). Cheapest
"interesting movement" encoding in the dataset: 31 bytes/axis.

### Worked example, type B — Falco slot 205 (circle)

PATH stream (53 B): `01 00 01` (CON 0.0, wait 1) + `f2 02` (LIN ×24) +
12 laps of `(0.0, 49)(1.0, 1)` — u ramps 0→1 in 49f, 1f seamless reset
(cv[0]==cv[8]). Spline: catmull-rom, 9 cv forming a radius~20 circle centered
(55,65), totalLength 129.5, segLength = cumulative arc-length fractions.
Ref joint translate (0,0,0). One lap / 50 frames = 2.6 u/f constant.

## Behavior envelope (from moving_targets.csv)

- Sustained speeds: 0.11-6.2 u/f, almost all ≤3; Falco 203's dashes
  (22-52 u/f) are the outlier.
- Loop periods: 200-2400 frames (3.3-40 s); stage loop = LCM of AObj
  endframes.
- Travel extents: 20-unit bobs up to 480-unit sweeps.
- Quirks: Ganondorf's 8 conveyor targets glide 1.2-4 u/f then sawtooth-wrap
  455-480 units in one frame; all 6 G&W movers have p95 speed = 0 (parked,
  discrete LCD-style jumps only); 2-key tracks are constants (authoring
  artifacts).
- `moving_targets.csv` columns: stage/char/slot/bone, mechanism, tracks with
  keyframe counts, period, rest pos, min/max/extent per axis, p95/max speed,
  wrap flag, shape heuristic, spline details.

## Randomizer viability (for bttrandomizer.com)

Current site code (randomizer.js): C2 hook at 0x801C4244 (inside the
Ground_801C4210 spawn loop) writes randomized coords into the target bone's
**JObj translate** (+0x38, paired-single) AND the cached world matrix tx/ty
(+0x50/+0x60). The default end template also sets JOBJ_MTX_DIRTY
(+0x14 |= 0x00800000); the enableMoving template omits it — "enable moving
targets" keeps vanilla movers on vanilla paths (they are not randomized).

Three routes to randomized movement:

1. **Reuse existing anims** (shuffle slots / phase / rate): cheap, weak —
   fixed shapes, 17 stages only.
2. **Per-frame parametric mover (recommended)**: a per-frame hook loops
   `stage_info.x280[199..219]` and writes each bone's translate + cached
   matrix (same write sequence the spawn hook already uses) from a packed
   per-target table (shape, center, amplitude, period, phase). No .dat edits.
   Destroyed targets' bones move harmlessly. To stop vanilla animation from
   fighting the mover, null the target bones' AObj at load — this also kills
   the PATH override (it's driven by jobj->aobj->hsd_obj), making every bone
   inert and uniform across all 25 stages.
3. **Author real FObj/spline data**: formats fully understood (this doc), but
   pointer fixups + allocation make it a bad fit for Gecko delivery; only
   worth it for .dat-patch/ISO distribution.

Reachability: the site already has per-stage bounds/exclusion polygons —
validate the entire swept path against them and cap speed at vanilla-feeling
~2-3 u/f (park-and-dash a la Falco 203 only needs station points + dash
segments checked). Main constraint is Gecko codespace (see LEARNINGS.md "Code
size" — ~3248 B Dolphin cap): mover blob is small but per-target params ~3
words vs 1 today; all-stages mode needs tight packing or a moving subset per
stage.

## Route-2 implementations (2026-07): POC + table-driven engine

Two working codes, neither yet booted in Dolphin (both statically verified:
disasm reviewed, exact-f32 math simulated against stage geometry):

1. **POC** — `gecko/mover_zelda.s` → `moving_target_zelda_poc.gecko.txt`:
   one hardcoded orbit (TZelda slot 199, circle (0,96) r13, 256f). 224 B.
   Kept as the minimal reference for the hook/write/state mechanics; its
   cost is almost all fixed machinery, NOT per-target cost.
2. **Engine (the real design)** — `gecko/mover_engine.s` →
   `moving_targets_engine.gecko.txt` (build: `gecko/build_mover_engine.sh`):
   one shared evaluator + packed spec table, **8 bytes per moving target**,
   one code covers all 25 stages. 428 B engine core; total = 428 + 8N + 4.
   50 targets ≈ 832 B ≈ 105 codelist lines — a quarter of the ~3248 B cap
   (and still fits alongside the 2560 B upside-down code). Demo table: 3
   movers on TZelda + 1 on TMario.

### Engine design (mover_engine.s)

- **Hook = `itMato_UnkMotion0_Phys` @ 0x802D8594** (the target item's physics
  callback, r3 = item gobj). Runs once per frame per LIVE target on every TT
  stage — one hook replaces 25 per-stage hooks, stops running when a target
  breaks, and freezes on pause for free. We write the bone pre-prologue, so
  the function's own bone→`it->pos` copy picks the new position up the same
  frame. Displaced insn `mflr r0` (non-leaf MWCC, size 0x58 in symbols.txt).
- **Identity**: bone = `Item.xDD4 itemVar.mato.x0` (item +0xDD4; Item* =
  gobj+0x2C); slot index = scan `stage_info.x280[199..219]` for the pointer;
  key = `(stage_id-0x28)*32 + slot` with a TT-range guard (0x28..0x41).
- **Phase = VI retrace counter @ 0x804D7420** (what VIGetRetraceCount
  returns). Position is a pure function of real time — zero mutable state in
  the blob. `phase16 = ((F << ls) + phase8*256) & 0xFFFF`, one lap per
  `65536>>ls` frames. Quirk: lag frames advance retrace faster than game
  frames (cosmetic skip-ahead).
- **Table entry (8 B)**, sentinel 0xFFFFFFFF:
  `word1 = key<<21 | (ls-4)<<18 | shape<<16 | phase8<<8` (shape 0 = ellipse,
  1-3 reserved); `word2 = cx.s8<<24 | cy.s8<<16 | ax.u8<<8 | ay.u8`.
  Centers are s8 in 2-unit steps (±254), amplitudes u8 in 1-unit steps
  (0..255 — Ganon's 480-unit sweep = amplitude 240 still fits).
  `x = 2cx + ax·cos(2πt)`, `y = 2cy + ay·sin(2πt)`: ax=ay circle, ay=0
  horizontal line, ax=0 vertical line, phase8 staggers copies of a path.
- **Un-specced targets stay vanilla-static** (table miss → no write). On the
  17 stages with vanilla movers, entries on ANIMATED slots will fight the
  stage anim (it re-ticks the bone after us) — spec only static slots there,
  or add the AObj-nulling load hook (still unbuilt).
- For bttrandomizer: the engine words are constant; the site generates just
  the table words per seed and splices them into the C2 block before the
  sentinel (recompute the C2 line count in the header).

The mechanics recipe below (learned building the POC, reused by the engine):

1. **Per-frame hook = the stage's own scene-2 GObj proc.** Every TT stage
   registers `StageCallbacks.callback2` as a per-frame proc via
   `HSD_GObj_SetupProc(gobj, cb2, 4)` (`Ground_SetupStageCallbacks`,
   gr/inlines.h). Find it in the decomp's `grT<char>.c` callbacks array
   (index [2] = bones+mesh scene; e.g. TZelda's is `grTZelda_8022404C`
   @ 0x8022404C). Because the proc belongs to one stage, **no stage-id check
   is needed** — the hook only ever executes there. For an all-stages mover
   you'd instead need one shared per-frame hook plus the stage-id check
   (id @ 0x8049E750, TT range 0x28-0x41; the id is the stage's index in the
   `Ground_803DFEDC` StageData table, ground.c — TZd = 0x3E, count from the
   table). The engine solved this with the itMato phys hook above — prefer
   that for anything target-related; the per-stage scene procs remain useful
   for non-target per-frame stage work.
2. **Displaced instruction:** hooking an MWCC non-leaf function's first
   instruction means displacing `mflr r0` (0x7C0802A6) — cheap to replicate
   in the payload and easy to sanity-check (symbols.txt size 0x34 = the
   canonical 13-insn two-call body). No local DOL exists to verify against;
   confirm in the Dolphin debugger on first boot.
3. **Bone write per frame:** `j = *(void**)(0x8049E948 + slot*4)`
   (stage_info.x280); null-check; store x to j+0x38 and j+0x50, y to j+0x3C
   and j+0x60 (translate + cached world mtx — both, so it's correct whichever
   copy the engine reads; no SetMtxDirty needed). Same sequence the
   randomizer spawn hook uses. The Mato item copies bone→`it->pos` each frame
   and hurtbox/render follow.
4. **Phase state lives in the blob.** The codelist is writable MEM1 and the
   codehandler only rewrites the hook word and the blob's FINAL word (the
   branch-back) each frame, so a `.long` between the code and `.text.tail`
   persists. Keep the period a power of two so wrap is a mask, and make
   position a pure function of the counter (idempotent, survives phase loss).
5. **Trig-free circle:** parabola sine `s(t)=16u(|u|-0.5), u=t-0.5` (max err
   5.6%, invisible); cos = same curve at `(ctr + period/4) & mask`. Fold the
   16 into the amplitude constant. int→float on the 750 is the 2^52
   store/`lfd`/`fsub` trick (no `fcfid`).
6. **Blob layout** (see `mover_zelda.s` / `entry3.S` precedent):
   `.text.entry` (code, ends `b _blob_end`) → data words → `.text.tail`
   (`_blob_end: nop`, gets overwritten by the handler's branch-back). Payload
   runs pre-prologue: r0/r4-r12/f0-f13/cr0 are free, preserve r3 (the gobj
   arg) and restore LR before the displaced `mflr r0`. Give yourself a real
   `stwu` frame for the float staging — no red zone on EABI.
7. **Validate the path against the stage JSON** (`GrT*.json` collision +
   target spawns): keep the swept path off floors/ceilings/walls and speed
   ≤~2-3 u/f. The POC's check lives in the build transcript (python sim of
   the exact f32 ops).

Scaling (superseded the POC-era estimate): with the table-driven engine the
marginal cost is exactly 8 B/target — 50 targets across 25 stages ≈ 832 B
total, 100 targets ≈ 1232 B, both far under the ~3248 B cap. The remaining
size levers if ever needed: per-stage table sections dropping the stage bits
(~6 B/entry), and shape-specific packing.

## Route-3′ keyframe installer + generator (2026-07): the shipping design

The full "all 25 stages, 10 randomized targets, 3-5 keyframe movers each"
pipeline. Statically verified (installer disasm reviewed; every stream
round-tripped through the FObj VM; final GCT structurally checked) and
**partially Dolphin-verified**: a first boot (Fox, Pikachu) confirmed the
hook fires, the blob decodes, randomized static positions apply, and
LoadDesc runs without crashing — but movers were frozen, which exposed the
attach-vs-arm bug below (fixed 2026-07-02; the fixed build is pending
re-verify).

**THE BUG THAT BIT US — attaching an AObj does not start it.**
`HSD_FObjLoadDesc` leaves every FObj with `flags = 0` = interpreter state 0,
which is a hard no-op: `HSD_FObjInterpretAnim` returns immediately and the
bone never updates. Vanilla stage code always pairs `HSD_JObjAddAnimAll`
with `HSD_JObjReqAnimAll(root, frame)`, whose per-bone effect is
`HSD_AObjReqAnim(aobj, frame)` @ **0x8036410C**: sets `curr_frame`, sets
`AOBJ_FIRST_PLAY` (first tick plays frame 0 at rate 0), and calls
`HSD_FObjReqAnimAll` — which is what arms the FObj state machines. Any
runtime-constructed animation MUST call ReqAnim after attaching. Symptom
signature if you forget: statics fine, movers frozen, no crash.

- `gecko/mover_install.s` (+`build_mover_install.sh`) — 600 B one-time
  stage-load hook at **0x801C0A50** (the Dolphin-proven flip_items hook,
  displaced insn `lwz r0,0x64(r1)`; epilogue context = volatiles/LR free, so
  game calls are safe). Per stage: detaches every target bone's vanilla AObj
  (`HSD_AObjRemove`, kills stage anims fighting ours incl. PATH), writes the
  randomized static positions (translate + mtx pair), and for movers builds
  `HSD_FObjDesc`/`HSD_AObjDesc` **on the stack** and attaches
  `HSD_AObjLoadDesc(&desc)` to `jobj->aobj` (+0x7C). The game's own
  `HSD_JObjAnimAll` (per-frame via `Ground_801C1CD0`, registered on every
  scene of EVERY stage — verified, static stages included) plays the anim;
  zero per-frame code of ours. Descriptors can be stack temporaries because
  LoadDesc copies fields and retains only the `ad` stream pointer — streams
  live in the codelist blob. `HSD_AObjLoadDesc` @ 0x8036439C,
  `HSD_AObjRemove` @ 0x803644CC, `AOBJ_LOOP` = 0x20000000, framerate
  defaults 1.0 in `HSD_AObjAlloc`.
- `gen_moving_gct.py` — generates `moving_targets_keyframe.gecko.txt`:
  randomizes all 250 positions inside bttrandomizer's bounds/exclusions
  (`btt_bounds.json`, dumped from randomizer.js; rect + point-in-polygon
  exclusions + exception islands, newBounds/newExclusions merged), picks 3-5
  movers/stage with patterns elevator/sweep/diagonal/diamond/park-and-dash,
  encodes FObj streams, and **validates every swept frame** by decoding with
  extract_stage.py's FObj VM plus an AObj loop-rewind simulation, then
  re-decodes the final packed blob installer-style as a packing check.
  Budget-aware (degrades pattern richness if over the ~3248 B cap). Typical:
  ~100 movers, GCT ≈ 3.1 KB. `preview_moving_gct.py` renders the artifact
  to `moving_targets_preview.svg` for eyeballing.
- Data format ("BLOB CONTRACT" in mover_install.s): 26×u16 directory by
  (stage_id−0x28) → per-stage {presence u32, mover u32, s16 base×2, s8
  coord pairs (2-unit grid, ±254 around base — covers even IceClimbers'
  y=500), mover specs: flags/end4 + per-track frac/len/stream}. 8 B/static,
  ~15-45 B/mover.

Encoder facts nailed down while building (beyond the stream grammar above):

- **Wait pairing, empirically confirmed**: pair (v,w) = "spend w frames
  interpolating from v to the NEXT pair's value" (window's opcode = the
  next-loaded group's op due to `op_intrp` lag — single-op streams behave
  as written). Park-and-dash = duplicated station values, exactly vanilla
  Falco.
- **Loop closure**: first value == last value, last wait 0, AObjDesc
  end_frame = sum of the other waits. The AObj rewind (`fmod` +
  `HSD_FObjReqAnimAll`) then replays seamlessly. Critical: **past the final
  pair the VM replays the last segment and then extrapolates** (LIN runs
  off to infinity) — end_frame must not exceed the wait sum.
- **Stream values are little-endian** (s16/u16/f32 alike) despite the
  big-endian console; op/wait bytes are byte-oriented. frac byte = kind
  (FLOAT 0<<5, S16 1<<5, U16 2<<5, S8 3<<5, U8 4<<5) | log2 divisor.
- SPL0 with keys on x and y eased identically gives straight segments with
  smooth stops — a 4-point "circle" is really a diamond orbit. A true
  circle needs ~8 keys or SPL explicit tangents.
- `FObjDesc.length` = stream byte count (gates `ad` reads; hitting it =
  end state).

### Size-lever variants (2026-07-03)

Three builds compared, same seed, same validation pipeline (baseline
regenerated for parity; all richness-adaptive against the ~3248 B cap):

| variant | installer | code+data | GCT | movers | richness reached |
|---|---|---:|---:|---:|---|
| baseline | mover_install.bin | 600+2348 | 2976 | 97 | 2 (cheap-biased) |
| A: levers 1+2 | mover_install.bin `--short-waits` | 600+2548 | 3176 | 102 | **3 (flat weights, 2 parkdash)** |
| B: lever 3 | mover_install_synth.bin `--synth` | 1280+1572 | **2880** | 103 | 3 |
| C: all | same + `--short-waits` | 1280+1572 | 2880 | 103 | 3 |

- **Lever 1** (`--short-waits`): raw-stream periods capped so leg waits fit
  1-byte varints. Doesn't shrink the artifact per se — it buys richness:
  baseline can't fit richness 3, A can.
- **Lever 2**: asm folds (vanilla-AObj detach folded into the slot loop,
  parse_track tightened) — paid for the ReqAnim fix, base installer back to
  600 B.
- **Lever 3** (`mover_synth.inc`, assembled with `--defsym SYNTH=1`): spec
  flag bit7 = synthesized pattern. The installer builds the FObj stream at
  load time into an HSD_MemAlloc(48) buffer (leaked per reload — same
  accepted pattern as flip_and_load) from 3-4 B params: elevator/sweep
  (s8 amp), diagonal (s8 ax, s8 ay), diamond (u8 amp), all anchored at the
  slot's static position = frame-0 value. Streams are always FRAC_S16 with
  fixed 2-byte wait varints ((w&0x7F)|0x80, w>>7 — valid for any w≤16383)
  since buffer RAM is free. Park-and-dash stays a raw stream (station lists
  don't template). Generator validates via `synth_streams()`, a byte-exact
  python mirror of the asm, and `verify_blob` re-synthesizes from the packed
  params — so what is validated is what the installer will build.
- **B == C byte-identical**: with synthesis, the only raw streams left are
  parkdash, whose waits were already 1-byte — lever 1 is inert. C's real
  meaning is "synth + the asm folds + richest patterns".

Recommendation: **B/C is the keeper** — smallest artifact AND full richness
with ~370 B headroom; A is the fallback if the synth path misbehaves in
Dolphin (it adds 680 B of new hand asm: alloc + stream synthesis, the least-
proven code here).

Known limitations / first-boot checklist: shares the 0x801C0A50 hook with
the upside-down mod — enable only one. Sheik's 3-target stage skipped.
Movers pause with the game (anim tick stops — vanilla behavior). On boot:
verify TT stages load, targets sit in new spots, 3-5 move per stage, broken
targets stay broken (AObjRemove path), and retry/reload works (descs are
rebuilt per load; HSD pools absorb the reallocation).

## Tools

- `extract_stage.py` — full stage → JSON (collision, targets, anim tracks).
  **Known gap:** it samples a target's own bone only, so Mewtwo 204/206
  (type C) get their local wiggle (~30×30) without the parent spline
  (~198×109 / ~166×69 real travel).
- `dump_target_anim.py <GrT*.dat> <slot>...` — raw per-key dump of any
  target's tracks + spline structures (source of the worked examples above).
- `moving_targets.csv` — the full 55-row specification table.
