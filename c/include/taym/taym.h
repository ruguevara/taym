#ifndef TAYM_TAYM_H
#define TAYM_TAYM_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* File header and chunk container. */
#define TAYM_VERSION 1u
#define TAYM_HEADER_SIZE 16u
#define TAYM_CHUNK_HEADER_SIZE 8u

/* On-disk record sizes. */
#define TAYM_TRAK_SIZE 16u
#define TAYM_CHIP_SIZE 32u
#define TAYM_TIMR_SIZE 6u
#define TAYM_MODS_SIZE 16u
#define TAYM_ACTN_SIZE 6u
#define TAYM_LANE_SIZE 16u
#define TAYM_TLAN_SIZE 16u

/* Sentinels. */
#define TAYM_NO_LOOP UINT32_C(0xFFFFFFFF)
#define TAYM_TLAN_NONE UINT32_C(0xFFFFFFFF)
#define TAYM_TLAN_UNCHANGED UINT32_C(0xFFFFFFFE)

/* clock_mode */
#define TAYM_CLOCK_ABS_RATE_HZ 0u
#define TAYM_CLOCK_CHIP_PERIOD 1u

/* value_type */
#define TAYM_VT_U8 1u
#define TAYM_VT_U16 2u
#define TAYM_VT_U32 3u

/* timing_mode */
#define TAYM_TM_ABSOLUTE 0u
#define TAYM_TM_RELATIVE 1u

/* source_mode */
#define TAYM_SRC_INLINE_VALUE 0u
#define TAYM_SRC_BIND_LANE 1u

/* command */
#define TAYM_CMD_EMPTY 0u
#define TAYM_CMD_START 1u
#define TAYM_CMD_MODULATE 2u
#define TAYM_CMD_STOP 3u

/* Chip and target registry constants fixed by draft 0.1 appendix A. */
#define TAYM_CHIP_TYPE_AY 0x01u
#define TAYM_CHIP_VARIANT_DEFAULT 0x00u
#define TAYM_AY_VARIANT_AY 0x00u
#define TAYM_AY_VARIANT_YM 0x01u
#define TAYM_CHIP_CONFIG_DEFAULT UINT32_C(0)

/* Format-virtual target range 0x80..0xBF is reserved -> invalid in draft 0.1
   (no sample/wavetable model yet); a later draft will assign it. */
#define TAYM_AY_TARGET_MAX 0x0Du
#define TAYM_AY_R13_SHAPE 0x0Du

typedef enum TaymResult {
    TAYM_OK = 0,
    TAYM_ERROR_ARGUMENT,
    TAYM_ERROR_ALLOC,
    TAYM_ERROR_IO,
    TAYM_ERROR_FORMAT,
    TAYM_ERROR_RANGE
} TaymResult;

typedef struct TaymTrak {
    uint32_t frame_rate;   /* unsigned 16.16 Hz */
    uint32_t frame_count;
    uint32_t loop_frame;   /* frame index or TAYM_NO_LOOP */
} TaymTrak;

typedef struct TaymChip {
    uint32_t clock_hz;
    uint8_t chip_type_id;
    uint8_t variant;
    char name[16];          /* ASCII, NUL-padded; may fill all 16 bytes */
    char frame_data_tag[4]; /* four zero bytes means no frame data */
    uint32_t config;
} TaymChip;

typedef struct TaymTimr {
    uint16_t clock_divider;
    uint8_t chip_index;
    uint8_t clock_mode;
} TaymTimr;

typedef struct TaymMods {
    uint32_t base_timer_value;
    uint32_t timer_lane_ref;
    uint32_t first_action;
    uint8_t action_count;
    uint8_t command;
} TaymMods;

typedef struct TaymActn {
    uint32_t operand;
    uint8_t target_id;
    uint8_t source_mode;
} TaymActn;

typedef struct TaymLane {
    uint32_t value_offset;
    uint32_t length;
    uint32_t loop_index;
    uint8_t value_type;
} TaymLane;

typedef struct TaymTlan {
    uint32_t value_offset;
    uint32_t length;
    uint32_t loop_index;
    uint8_t timing_mode;
} TaymTlan;

typedef struct TaymChunk {
    char tag[4];
    uint8_t *data;
    size_t size;
} TaymChunk;

typedef struct Taym {
    TaymTrak trak;
    uint32_t flags;

    TaymChip *chips;
    size_t chip_count;
    TaymTimr *timers;
    size_t timer_count;
    TaymMods *mods;
    size_t mod_count;
    TaymActn *actions;
    size_t action_count;
    TaymLane *lanes;
    size_t lane_count;
    TaymTlan *tlanes;
    size_t tlane_count;

    uint8_t *vu08;
    size_t vu08_count;
    uint16_t *vu16;
    size_t vu16_count;
    uint32_t *vu32;
    size_t vu32_count;

    uint8_t *info;
    size_t info_size;

    /*
     * Non-core chunks: embedded frame-data chunks such as PSG0 plus any
     * extension chunks the reader preserved. Tags are four raw bytes, not
     * NUL-terminated strings.
     */
    TaymChunk *chunks;
    size_t chunk_count;
} Taym;

void taym_init(Taym *taym);
void taym_free(Taym *taym);

TaymResult taym_read_bytes(const uint8_t *data, size_t size, Taym *out);
TaymResult taym_read_file(const char *path, Taym *out);

TaymResult taym_write_bytes(const Taym *taym, uint8_t **out_data, size_t *out_size);
TaymResult taym_write_file(const char *path, const Taym *taym);
void taym_free_bytes(uint8_t *data);

const char *taym_result_string(TaymResult result);

uint32_t taym_to_fix16(double value);
double taym_from_fix16(uint32_t encoded);

int taym_tag_is_zero(const char tag[4]);
int taym_tag_equal(const char a[4], const char b[4]);
const TaymChunk *taym_find_chunk(const Taym *taym, const char tag[4]);

#ifdef __cplusplus
}
#endif

#endif
