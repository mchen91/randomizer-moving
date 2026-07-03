/*
 * mover_install.c — Keyframe moving-target installer (reference C, GALE01 NTSC 1.02)
 *
 * Readable equivalent of gecko/mover_install.s. The shipping code is hand-written
 * asm plus a data blob appended by gen_moving_gct.py. Study this file to understand
 * what the installer does at stage load; then implement the same steps in asm.
 *
 * HOOK
 *   Ground_801C0800 epilogue @ 0x801C0A50 (same site as flip_items). Runs once
 *   per stage load after OnInit. Displaced insn: lwz r0, 0x64(r1).
 *
 * STEPS (per Target Test stage with packed data)
 *   1. Look up this stage's blob via the 26-entry u16 directory (index =
 *      internal_stage_id - 0x28). MOVER_DIR_ABSENT = skip.
 *   2. For each present slot (presence mask bits 0..20):
 *      a. Resolve bone from stage_info.x280[199 + slot_index].
 *      b. Remove any vanilla AObj on that bone (PATH anims would fight ours).
 *      c. Write static position from (base_x, base_y) + 2 * (rx, ry).
 *      d. If mover mask bit set: build stack AObjDesc + FObjDesc chain pointing
 *         at FObj streams in the blob, call HSD_AObjLoadDesc then HSD_AObjReqAnim.
 *
 * CRITICAL GAME DETAIL
 *   HSD_AObjLoadDesc alone leaves every FObj interpreter in state 0 (no-op).
 *   HSD_AObjReqAnim(aobj, 0.0f) is REQUIRED to arm playback — learned in Dolphin.
 *
 * DATA LAYOUT
 *   Documented in blob_contract.h and gen_moving_gct.py. At runtime the directory
 *   and stage blobs sit in the Gecko codelist immediately after the installer code
 *   (label Ldir in mover_install.s).
 *
 * SYNTH PATH
 *   mover_synth.inc implements template synthesis (SYNTH=1 asm build). Omitted here;
 *   see gecko/mover_synth.inc for the alloc-and-emit variant of step 2d.
 */

#include "blob_contract.h"
#include "gale01.h"

typedef struct MoverStageView {
    u32 presence;
    u32 movers;
    s16 base_x;
    s16 base_y;
    const s8 *coords;       /* 2 bytes per set bit in presence, ascending index */
    const u8 *specs;        /* mover specs after coord table */
} MoverStageView;

static const u8 *stage_blob_at(const u16 *directory, u32 stage_id)
{
    u32 idx;
    u16 off;

    if (stage_id < TT_STAGE_ID_MIN)
        return 0;
    idx = stage_id - TT_STAGE_ID_MIN;
    if (idx >= MOVER_DIR_ENTRIES)
        return 0;

    off = directory[idx];
    if (off == MOVER_DIR_ABSENT)
        return 0;

    return (const u8 *)directory + off;
}

static void parse_stage_blob(const u8 *blob, MoverStageView *out)
{
    int i;
    int n_present = 0;

    out->presence = ((const u32 *)blob)[0];
    out->movers = ((const u32 *)blob)[1];
    out->base_x = ((const s16 *)blob)[4];
    out->base_y = ((const s16 *)blob)[5];
    out->coords = blob + 12;

    for (i = 0; i < MOVER_SLOT_COUNT; i++) {
        if (out->presence >> i & 1u)
            n_present++;
    }
    out->specs = out->coords + 2 * n_present;
}

static HSD_JObj *bone_for_slot(int slot_index)
{
    if (slot_index < 0 || slot_index >= MOVER_SLOT_COUNT)
        return 0;
    return GALE01_TARGET_BONE_TABLE[slot_index];
}

static void write_bone_xy(HSD_JObj *bone, f32 x, f32 y)
{
    bone->translate_x = x;
    bone->translate_y = y;
    bone->cached_world_tx = x;
    bone->cached_world_ty = y;
}

static void detach_vanilla_animation(HSD_JObj *bone)
{
    void *old;

    if (bone == 0)
        return;

    old = bone->aobj;
    if (old == 0)
        return;

    bone->aobj = 0;
    GALE01_FN_HSD_AOBJ_REMOVE(old);
}

static const u8 *parse_raw_track(HSD_FObjDesc *desc, u8 track_type, const u8 *cursor)
{
    u8 frac = cursor[0];
    u8 len = cursor[1];

    desc->next = 0;
    desc->length = len;
    desc->startframe = 0.0f;
    desc->type = track_type;
    desc->frac_value = frac;
    desc->frac_slope = frac;
    desc->dummy0 = 0;
    desc->ad = cursor + 2;
    return cursor + 2 + len;
}

/*
 * Build AObjDesc + up to two FObjDesc on stack, wire stream pointers into the
 * blob, attach to bone. Returns bytes consumed from spec cursor.
 */
static const u8 *attach_raw_mover(HSD_JObj *bone, const u8 *spec)
{
    u8 flags = spec[0];
    u8 end4 = spec[1];
    HSD_AObjDesc aobj_desc;
    HSD_FObjDesc fobj_a, fobj_b;
    HSD_FObjDesc *head = 0;
    HSD_FObjDesc *tail = 0;
    const u8 *cursor = spec + 2;
    void *aobj;

    aobj_desc.flags = GALE01_AOBJ_LOOP_FLAG;
    aobj_desc.end_frame = (f32)(end4 * 4);
    aobj_desc.obj_id = 0;
    aobj_desc.fobjdesc = 0;

    if (flags & MOVER_FLAG_TRAX) {
        head = &fobj_a;
        cursor = parse_raw_track(head, FO_TRACK_TRAX, cursor);
        tail = head;
    }
    if (flags & MOVER_FLAG_TRAY) {
        if (head == 0) {
            head = &fobj_a;
            cursor = parse_raw_track(head, FO_TRACK_TRAY, cursor);
            tail = head;
        } else {
            cursor = parse_raw_track(&fobj_b, FO_TRACK_TRAY, cursor);
            tail->next = &fobj_b;
            tail = &fobj_b;
        }
    }

    aobj_desc.fobjdesc = head;
    if (head == 0)
        return cursor;

    if (bone != 0) {
        aobj = GALE01_FN_HSD_AOBJ_LOAD_DESC(&aobj_desc);
        bone->aobj = aobj;
        GALE01_FN_HSD_AOBJ_REQ_ANIM(aobj, 0.0f);
    }
    return cursor;
}

static void install_slot(int slot_index, const MoverStageView *stage, const u8 **coord_io,
                         const u8 **spec_io)
{
    HSD_JObj *bone;
    s8 rx, ry;
    f32 x, y;
    int is_mover;

    bone = bone_for_slot(slot_index);
    detach_vanilla_animation(bone);

    rx = (s8)(*coord_io)[0];
    ry = (s8)(*coord_io)[1];
    *coord_io += 2;

    x = (f32)(stage->base_x + 2 * (s32)rx);
    y = (f32)(stage->base_y + 2 * (s32)ry);

    if (bone != 0)
        write_bone_xy(bone, x, y);

    is_mover = (int)(stage->movers >> slot_index & 1u);
    if (!is_mover)
        return;

    if ((spec_io != 0) && (*spec_io != 0)) {
        if ((*spec_io)[0] & MOVER_FLAG_SYNTH) {
            /* Synthesized patterns: see gecko/mover_synth.inc */
            return;
        }
        *spec_io = attach_raw_mover(bone, *spec_io);
    }
}

static void install_stage(const u8 *blob)
{
    MoverStageView stage;
    int slot_index;
    const u8 *coord;
    const u8 *spec;

    parse_stage_blob(blob, &stage);
    coord = stage.coords;
    spec = stage.specs;

    for (slot_index = 0; slot_index < MOVER_SLOT_COUNT; slot_index++) {
        if (!(stage.presence >> slot_index & 1u))
            continue;
        install_slot(slot_index, &stage, &coord, &spec);
    }
}

/*
 * Run the installer. `directory` points at Ldir in the Gecko blob (26 x u16
 * offsets, then concatenated stage blobs). gen_moving_gct.py builds this layout.
 */
void mover_install(const u16 *directory)
{
    u32 stage_id;
    const u8 *blob;

    if (directory == 0)
        return;

    stage_id = GALE01_STAGE_ID;
    if (stage_id < TT_STAGE_ID_MIN || stage_id - TT_STAGE_ID_MIN > 25u)
        return;

    blob = stage_blob_at(directory, stage_id);
    if (blob == 0)
        return;

    install_stage(blob);
}
