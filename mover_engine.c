/*
 * mover_engine.c — Table-driven moving-target engine (reference C, GALE01 NTSC 1.02)
 *
 * Readable equivalent of gecko/mover_engine.s. The shipping code is hand-written
 * asm (~440 B); this file exists so others can study the algorithm before
 * writing their own Gecko hook.
 *
 * ROUTE
 *   Per-frame hook on itMato_UnkMotion0_Phys @ 0x802D8594. The game calls this
 *   once per frame for every live breakable target. We run BEFORE the displaced
 *   prologue insn (mflr r0), write the bone position, then the function body
 *   copies it->pos from the bone — so the new position applies the same frame.
 *
 * WHY ONE HOOK COVERS ALL 25 STAGES
 *   No stage-load hook, no per-stage code. A shared evaluator walks a packed
 *   8-byte table keyed by (stage_id, slot). Unlisted targets stay vanilla.
 *
 * PHASE SOURCE
 *   VI retrace counter @ 0x804D7420 (+1 per rendered frame). Position is a
 *   pure function of time — no mutable state in the blob. Pause freezes motion
 *   because item physics stop running.
 *
 * BONE WRITE
 *   Targets read JObj translate AND cached world-matrix translation each frame.
 *   Write both (+0x38/+0x3C and +0x50/+0x60), same as the keyframe installer.
 */

#include "gale01.h"

#define TABLE_SENTINEL_KEY  0xFFFFFFFFu
#define PHASE_SCALE         65536.0f
#define SIN_PARABOLA_SCALE  16.0f   /* asm divides sin by 16 before amplitude */

/*
 * One 8-byte table row (big-endian in the Gecko blob; native structs here for
 * reading). See mover_engine.s header comment for bit packing.
 */
typedef struct MoverEngineEntry {
    u32 word1;
    u32 word2;
} MoverEngineEntry;

static u32 entry_key(u32 word1)
{
    return (word1 >> 21) & 0x7FFu;
}

static int entry_ls(u32 word1)
{
    return (int)(((word1 >> 18) & 7u) + 4u);
}

static u32 entry_phase8(u32 word1)
{
    return (word1 >> 8) & 0xFFu;
}

static void decode_word2(u32 word2, s8 *cx, s8 *cy, u8 *ax, u8 *ay)
{
    *cx = (s8)(word2 >> 24);
    *cy = (s8)((word2 >> 16) & 0xFFu);
    *ax = (u8)((word2 >> 8) & 0xFFu);
    *ay = (u8)(word2 & 0xFFu);
}

/*
 * Parabola sine approximation from mover_engine.s / sin16.
 * Input phase16 in 0..65535 maps one lap; output is sin(2*pi*t) / 16.
 * Max error ~5.6% of full amplitude before the final *16 in position math.
 */
static f32 sin16_over_16(u32 phase16)
{
    f32 t = (f32)phase16 / PHASE_SCALE;
    f32 u = t - 0.5f;
    f32 abs_u = u < 0.0f ? -u : u;
    return u * (abs_u - 0.5f);
}

static void write_target_bone_xy(HSD_JObj *bone, f32 x, f32 y)
{
    bone->translate_x = x;
    bone->translate_y = y;
    bone->cached_world_tx = x;
    bone->cached_world_ty = y;
}

/*
 * Scan stage_info.x280[199..219] for the bone pointer; return slot index 0..20
 * or -1 if not found.
 */
static int slot_index_for_bone(HSD_JObj *bone)
{
    int i;

    for (i = 0; i < MOVER_SLOT_COUNT; i++) {
        if (GALE01_TARGET_BONE_TABLE[i] == bone)
            return i;
    }
    return -1;
}

static u32 build_lookup_key(u32 stage_id, int slot_index)
{
    u32 stage_idx = stage_id - TT_STAGE_ID_MIN;
    return (stage_idx << 5) | (u32)slot_index;
}

static const MoverEngineEntry *find_table_entry(const MoverEngineEntry *table, u32 key)
{
    for (;; table++) {
        if (table->word1 == TABLE_SENTINEL_KEY)
            return 0;
        if (entry_key(table->word1) == key)
            return table;
    }
}

static void apply_entry_motion(HSD_JObj *bone, const MoverEngineEntry *entry, u32 frame)
{
    int ls = entry_ls(entry->word1);
    u32 phase16 = ((frame << ls) + (entry_phase8(entry->word1) << 8)) & 0xFFFFu;
    u32 phase_cos = (phase16 + 0x4000u) & 0xFFFFu;
    s8 cx, cy;
    u8 ax, ay;
    f32 cosv, sinv, x, y;

    decode_word2(entry->word2, &cx, &cy, &ax, &ay);

    cosv = sin16_over_16(phase_cos) * SIN_PARABOLA_SCALE;
    sinv = sin16_over_16(phase16) * SIN_PARABOLA_SCALE;

    x = 2.0f * (f32)cx + (f32)ax * cosv;
    y = 2.0f * (f32)cy + (f32)ay * sinv;
    write_target_bone_xy(bone, x, y);
}

/*
 * Main per-target callback. `table` points at the embedded spec table (Ltab in
 * the asm blob). In asm the table lives in the codelist after the code; a
 * generator would append rows before the sentinel.
 */
void mover_engine_phys(HSD_GObj *gobj, const MoverEngineEntry *table)
{
    Item *item;
    HSD_JObj *bone;
    u32 stage_id;
    int slot_index;
    u32 key;
    const MoverEngineEntry *entry;

    if (gobj == 0 || table == 0)
        return;

    item = (Item *)gobj->user_data;
    if (item == 0)
        return;

    bone = item->target_bone;
    if (bone == 0)
        return;

    slot_index = slot_index_for_bone(bone);
    if (slot_index < 0)
        return;

    stage_id = GALE01_STAGE_ID;
    if (stage_id < TT_STAGE_ID_MIN || stage_id > TT_STAGE_ID_MAX)
        return;
    if (stage_id - TT_STAGE_ID_MIN >= TT_STAGE_COUNT)
        return;

    key = build_lookup_key(stage_id, slot_index);
    entry = find_table_entry(table, key);
    if (entry == 0)
        return;

    apply_entry_motion(bone, entry, GALE01_VI_RETRACE_COUNT);
}

/*
 * Demo table matching gecko/mover_engine.s Ltab (Zelda + Mario samples).
 * Shipping builds embed equivalent words in the asm blob.
 */
static const MoverEngineEntry MOVER_ENGINE_DEMO_TABLE[] = {
    { 0x58100000u, 0x00300D0Du },  /* TZd slot 199: circle r=13 @ (0,96), T=256f */
    { 0x58B40000u, 0x001E0800u },  /* TZd slot 204: horizontal bob */
    { 0x582C0000u, 0xC2F10008u },  /* TZd slot 200: vertical bob */
    { 0x000C0000u, 0x31030808u },  /* TMr slot 199: circle r=8 @ (98,6) */
    { TABLE_SENTINEL_KEY, 0u },
};

/* Gecko entry trampoline calls this; r3 = item gobj (preserved by hook). */
void mover_engine(HSD_GObj *gobj)
{
    mover_engine_phys(gobj, MOVER_ENGINE_DEMO_TABLE);
}
