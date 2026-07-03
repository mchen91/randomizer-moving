/*
 * blob_contract.h — Data layout appended to mover_install.s by gen_moving_gct.py
 *
 * Reference documentation for the packed blob consumed at stage load. The
 * installer (gecko/mover_install.s) reads this from the Gecko codelist after
 * the PPC instructions. See also the BLOB CONTRACT comment in mover_install.s.
 *
 * Targets Melee NTSC 1.02 (GALE01). Stage directory index = internal_id - 0x28.
 */
#ifndef BLOB_CONTRACT_H
#define BLOB_CONTRACT_H

#include <stdint.h>

/* Gecko hook: Ground_801C0800 epilogue (same site as flip_items). */
#define MOVER_INSTALL_HOOK 0x801C0A50u

/* Max payload bytes (installer + data); Dolphin codespace cap ~3248 B total. */
#define MOVER_BLOB_BUDGET 3224u

/* FObj track types written into HSD_FObjDesc.type */
#define FO_TRACK_TRAX 5u
#define FO_TRACK_TRAY 6u

/* Mover spec flags (byte 0 of each mover entry) */
#define MOVER_FLAG_TRAX 0x01u
#define MOVER_FLAG_TRAY 0x02u
#define MOVER_FLAG_SYNTH 0x80u   /* template synthesis; see mover_synth.inc */

#define MOVER_DIR_ENTRIES 26u    /* one u16 per internal stage slot 0x28..0x41 */
#define MOVER_DIR_ABSENT  0xFFFFu
#define MOVER_SLOT_BASE   199u   /* It_Kind_Mato slots on Target Test stages */
#define MOVER_SLOT_COUNT  21u    /* bits 0..20 in presence/mover masks */

/*
 * Top-level layout (big-endian on disk / in codelist):
 *
 *   [PPC code from mover_install.s]
 *   [branch patched to skip over data]
 *   directory[26]: uint16_t offset from start of directory; MOVER_DIR_ABSENT = skip
 *   stage_blob[]: concatenated per-stage records (each 4-byte aligned)
 *
 * Per-stage blob:
 *   uint32_t presence;   // bit i => slot MOVER_SLOT_BASE + i exists
 *   uint32_t movers;     // subset of presence
 *   int16_t  base_x, base_y;
 *   for each set bit in presence (low to high index):
 *       int8_t rel_x, rel_y;   // world = base + 2 * rel
 *   for each set bit in movers (same order):
 *       uint8_t flags, end4;   // loop length = end4 * 4 frames
 *       per track (TRAX first if both): uint8_t frac, uint8_t len, uint8_t stream[len]
 *       OR synth: uint8_t 0x80|pattern, uint8_t end4, pattern params
 */

typedef struct MoverDirEntry {
    uint16_t stage_offset; /* from first byte of directory */
} MoverDirEntry;

typedef struct MoverStageHeader {
    uint32_t presence;
    uint32_t movers;
    int16_t  base_x;
    int16_t  base_y;
} MoverStageHeader;

#endif /* BLOB_CONTRACT_H */
