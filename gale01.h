/*
 * gale01.h — Types, layouts, and fixed addresses for moving-target code (GALE01 v1.02).
 *
 * Shared by the reference C sources (mover_install.c, mover_engine.c). The
 * shipping Gecko codes are hand-written assembly in gecko/; these definitions
 * mirror what that asm touches so readers can follow the logic in C.
 */
#ifndef GALE01_H
#define GALE01_H

typedef unsigned char  u8;
typedef unsigned short u16;
typedef short          s16;
typedef unsigned int   u32;
typedef int            s32;
typedef float          f32;

/* --- Fixed MEM1 addresses --- */

#define GALE01_MEM1_LO  0x80000000u
#define GALE01_MEM1_HI  0x81800000u

#define GALE01_STAGE_ID            (*(volatile u32 *)0x8049E750u)
#define GALE01_TARGET_BONE_TABLE   ((HSD_JObj **)0x8049EC64u)  /* stage_info.x280[199] */
#define GALE01_VI_RETRACE_COUNT    (*(volatile u32 *)0x804D7420u)

#define GALE01_FN_HSD_AOBJ_LOAD_DESC ((void *(*)(void *))0x8036439Cu)
#define GALE01_FN_HSD_AOBJ_REMOVE    ((void (*)(void *))0x803644CCu)
#define GALE01_FN_HSD_AOBJ_REQ_ANIM  ((void (*)(void *, f32))0x8036410Cu)

#define GALE01_AOBJ_LOOP_FLAG  0x20000000u

/* Gecko hooks */
#define MOVER_INSTALL_HOOK  0x801C0A50u  /* Ground epilogue; displaced lwz r0,0x64(r1) */
#define MOVER_ENGINE_HOOK   0x802D8594u  /* itMato_UnkMotion0_Phys; displaced mflr r0 */

/* Target Test internal stage ids (Ground_GetStageId values) */
#define TT_STAGE_ID_MIN  0x28u
#define TT_STAGE_ID_MAX  0x41u   /* inclusive upper guard uses >25 after -0x28 */
#define TT_STAGE_COUNT   25u

enum {
    MOVER_SLOT_BASE   = 199,
    MOVER_SLOT_COUNT  = 21,      /* bits 0..20 in presence / mover masks */
    MOVER_DIR_ENTRIES = 26,      /* directory index = stage_id - TT_STAGE_ID_MIN */
};

/* --- HSD scene graph (partial) --- */

typedef struct HSD_JObj HSD_JObj;

typedef struct HSD_JObj {
    u8  header[8];
    HSD_JObj *next;
    HSD_JObj *parent;
    HSD_JObj *child;
    u32 flags;
    void *dobj;
    u8  pad_to_scale[0x2C - 0x1C];
    f32 scale_x, scale_y, scale_z;
    f32 translate_x, translate_y, translate_z;
    u8  pad_to_mtx[0x50 - 0x44];
    f32 cached_world_tx;
    u8  pad_m[0x60 - 0x54];
    f32 cached_world_ty;
    u8  pad_to_aobj[0x7C - 0x64];
    void *aobj;
} HSD_JObj;

/* Stack-built descriptors (installer builds these once per mover slot). */
typedef struct HSD_FObjDesc {
    struct HSD_FObjDesc *next;
    u32 length;          /* stream byte count */
    f32 startframe;
    u8  type;            /* FO_TRACK_TRAX (5) or FO_TRACK_TRAY (6) */
    u8  frac_value;
    u8  frac_slope;
    u8  dummy0;
    const u8 *ad;        /* points into the Gecko blob */
} HSD_FObjDesc;

typedef struct HSD_AObjDesc {
    u32 flags;           /* GALE01_AOBJ_LOOP_FLAG for looping movers */
    f32 end_frame;
    HSD_FObjDesc *fobjdesc;
    u32 obj_id;
} HSD_AObjDesc;

/* --- Item / target (partial) --- */

typedef struct HSD_GObj HSD_GObj;

typedef struct HSD_GObj {
    u8  pad[0x2C];
    void *user_data;     /* Item* for itMato */
} HSD_GObj;

typedef struct Item {
    u8  pad[0xDD4];
    HSD_JObj *target_bone;   /* itemVar.mato.x0 */
} Item;

#include "blob_contract.h"

static inline int gale01_ptr_ok(const void *ptr)
{
    u32 addr = (u32)(const u8 *)ptr;
    return addr >= GALE01_MEM1_LO && addr < GALE01_MEM1_HI && (addr & 3u) == 0;
}

#endif /* GALE01_H */
