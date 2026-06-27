#include "taym/taym.h"

#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct ChunkRef {
    char tag[4];
    const uint8_t *payload;
    uint32_t size;
} ChunkRef;

typedef struct Writer {
    uint8_t *data;
    size_t size;
    size_t cap;
} Writer;

typedef struct TagList {
    char *tags;
    size_t count;
    size_t cap;
} TagList;

static uint16_t read_u16(const uint8_t *p)
{
    return (uint16_t)(p[0] | ((uint16_t)p[1] << 8));
}

static uint32_t read_u32(const uint8_t *p)
{
    return (uint32_t)p[0]
        | ((uint32_t)p[1] << 8)
        | ((uint32_t)p[2] << 16)
        | ((uint32_t)p[3] << 24);
}

static void write_u16(uint8_t *p, uint16_t v)
{
    p[0] = (uint8_t)(v & 0xFFu);
    p[1] = (uint8_t)((v >> 8) & 0xFFu);
}

static void write_u32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)(v & 0xFFu);
    p[1] = (uint8_t)((v >> 8) & 0xFFu);
    p[2] = (uint8_t)((v >> 16) & 0xFFu);
    p[3] = (uint8_t)((v >> 24) & 0xFFu);
}

static int checked_mul_size(size_t a, size_t b, size_t *out)
{
    if (a != 0 && b > SIZE_MAX / a) {
        return 0;
    }
    *out = a * b;
    return 1;
}

static void *calloc_array(size_t count, size_t elem_size)
{
    size_t bytes;

    if (!checked_mul_size(count, elem_size, &bytes)) {
        return NULL;
    }
    if (bytes == 0) {
        return NULL;
    }
    return calloc(count, elem_size);
}

static uint8_t *copy_payload(const uint8_t *payload, size_t size)
{
    uint8_t *copy;

    if (size == 0) {
        return NULL;
    }
    copy = (uint8_t *)malloc(size);
    if (copy == NULL) {
        return NULL;
    }
    memcpy(copy, payload, size);
    return copy;
}

int taym_tag_equal(const char a[4], const char b[4])
{
    return memcmp(a, b, 4) == 0;
}

int taym_tag_is_zero(const char tag[4])
{
    static const char zero[4] = {0, 0, 0, 0};
    return taym_tag_equal(tag, zero);
}

static int tag_literal_equal(const char tag[4], const char *literal)
{
    return memcmp(tag, literal, 4) == 0;
}

static int is_core_tag(const char tag[4])
{
    return tag_literal_equal(tag, "TRAK")
        || tag_literal_equal(tag, "CHIP")
        || tag_literal_equal(tag, "TIMR")
        || tag_literal_equal(tag, "MODS")
        || tag_literal_equal(tag, "ACTN")
        || tag_literal_equal(tag, "LANE")
        || tag_literal_equal(tag, "TLAN")
        || tag_literal_equal(tag, "VU08")
        || tag_literal_equal(tag, "VU16")
        || tag_literal_equal(tag, "VU32");
}

static int is_info_tag(const char tag[4])
{
    return tag_literal_equal(tag, "INFO");
}

const TaymChunk *taym_find_chunk(const Taym *taym, const char tag[4])
{
    size_t i;

    if (taym == NULL || tag == NULL) {
        return NULL;
    }
    for (i = 0; i < taym->chunk_count; i++) {
        if (taym_tag_equal(taym->chunks[i].tag, tag)) {
            return &taym->chunks[i];
        }
    }
    return NULL;
}

void taym_init(Taym *taym)
{
    if (taym != NULL) {
        memset(taym, 0, sizeof(*taym));
        taym->trak.loop_frame = TAYM_NO_LOOP;
    }
}

void taym_free(Taym *taym)
{
    size_t i;

    if (taym == NULL) {
        return;
    }
    free(taym->chips);
    free(taym->timers);
    free(taym->mods);
    free(taym->actions);
    free(taym->lanes);
    free(taym->tlanes);
    free(taym->vu08);
    free(taym->vu16);
    free(taym->vu32);
    free(taym->info);
    for (i = 0; i < taym->chunk_count; i++) {
        free(taym->chunks[i].data);
    }
    free(taym->chunks);
    taym_init(taym);
}

void taym_free_bytes(uint8_t *data)
{
    free(data);
}

const char *taym_result_string(TaymResult result)
{
    switch (result) {
    case TAYM_OK:
        return "ok";
    case TAYM_ERROR_ARGUMENT:
        return "bad argument";
    case TAYM_ERROR_ALLOC:
        return "allocation failed";
    case TAYM_ERROR_IO:
        return "I/O error";
    case TAYM_ERROR_FORMAT:
        return "malformed TAYM structure";
    case TAYM_ERROR_RANGE:
        return "value out of representable TAYM range";
    default:
        return "unknown TAYM error";
    }
}

uint32_t taym_to_fix16(double value)
{
    if (value <= 0.0) {
        return 0;
    }
    if (value >= 65536.0) {
        return UINT32_MAX;
    }
    return (uint32_t)(value * 65536.0 + 0.5);
}

double taym_from_fix16(uint32_t encoded)
{
    return (double)encoded / 65536.0;
}

static TaymResult append_refs(ChunkRef **refs, size_t *count, size_t *cap,
                              const char tag[4], const uint8_t *payload,
                              uint32_t size)
{
    ChunkRef *next;
    size_t i;

    for (i = 0; i < *count; i++) {
        if (taym_tag_equal((*refs)[i].tag, tag)) {
            return TAYM_ERROR_FORMAT;
        }
    }
    if (*count == *cap) {
        size_t next_cap = (*cap == 0) ? 16 : (*cap * 2);
        if (next_cap < *cap || next_cap > SIZE_MAX / sizeof(**refs)) {
            return TAYM_ERROR_ALLOC;
        }
        next = (ChunkRef *)realloc(*refs, next_cap * sizeof(**refs));
        if (next == NULL) {
            return TAYM_ERROR_ALLOC;
        }
        *refs = next;
        *cap = next_cap;
    }
    memcpy((*refs)[*count].tag, tag, 4);
    (*refs)[*count].payload = payload;
    (*refs)[*count].size = size;
    (*count)++;
    return TAYM_OK;
}

static const ChunkRef *find_ref(const ChunkRef *refs, size_t count,
                                const char tag[4])
{
    size_t i;

    for (i = 0; i < count; i++) {
        if (taym_tag_equal(refs[i].tag, tag)) {
            return &refs[i];
        }
    }
    return NULL;
}

static TaymResult require_ref(const ChunkRef *refs, size_t count,
                              const char tag[4], const ChunkRef **out)
{
    *out = find_ref(refs, count, tag);
    return (*out == NULL) ? TAYM_ERROR_FORMAT : TAYM_OK;
}

static TaymResult check_record_size(const ChunkRef *ref, uint32_t stride,
                                    size_t *count)
{
    if (ref->size % stride != 0) {
        return TAYM_ERROR_FORMAT;
    }
    *count = ref->size / stride;
    return TAYM_OK;
}

static TaymResult parse_trak(const ChunkRef *ref, TaymTrak *out)
{
    const uint8_t *p = ref->payload;

    if (ref->size != TAYM_TRAK_SIZE) {
        return TAYM_ERROR_FORMAT;
    }
    out->frame_rate = read_u32(p);
    out->frame_count = read_u32(p + 4);
    out->loop_frame = read_u32(p + 8);
    return TAYM_OK;
}

static TaymResult parse_chips(const ChunkRef *ref, Taym *out)
{
    size_t count;
    size_t i;

    if (check_record_size(ref, TAYM_CHIP_SIZE, &count) != TAYM_OK) {
        return TAYM_ERROR_FORMAT;
    }
    out->chips = (TaymChip *)calloc_array(count, sizeof(*out->chips));
    if (count != 0 && out->chips == NULL) {
        return TAYM_ERROR_ALLOC;
    }
    out->chip_count = count;
    for (i = 0; i < count; i++) {
        const uint8_t *p = ref->payload + i * TAYM_CHIP_SIZE;
        out->chips[i].clock_hz = read_u32(p);
        out->chips[i].chip_type_id = p[4];
        out->chips[i].variant = p[5];
        memcpy(out->chips[i].name, p + 8, 16);
        memcpy(out->chips[i].frame_data_tag, p + 24, 4);
        out->chips[i].config = read_u32(p + 28);
    }
    return TAYM_OK;
}

static TaymResult parse_timers(const ChunkRef *ref, Taym *out)
{
    size_t count;
    size_t i;

    if (check_record_size(ref, TAYM_TIMR_SIZE, &count) != TAYM_OK) {
        return TAYM_ERROR_FORMAT;
    }
    out->timers = (TaymTimr *)calloc_array(count, sizeof(*out->timers));
    if (count != 0 && out->timers == NULL) {
        return TAYM_ERROR_ALLOC;
    }
    out->timer_count = count;
    for (i = 0; i < count; i++) {
        const uint8_t *p = ref->payload + i * TAYM_TIMR_SIZE;
        out->timers[i].clock_divider = read_u16(p);
        out->timers[i].chip_index = p[2];
        out->timers[i].clock_mode = p[3];
    }
    return TAYM_OK;
}

static TaymResult parse_mods(const ChunkRef *ref, Taym *out)
{
    size_t count;
    size_t i;

    if (check_record_size(ref, TAYM_MODS_SIZE, &count) != TAYM_OK) {
        return TAYM_ERROR_FORMAT;
    }
    out->mods = (TaymMods *)calloc_array(count, sizeof(*out->mods));
    if (count != 0 && out->mods == NULL) {
        return TAYM_ERROR_ALLOC;
    }
    out->mod_count = count;
    for (i = 0; i < count; i++) {
        const uint8_t *p = ref->payload + i * TAYM_MODS_SIZE;
        out->mods[i].base_timer_value = read_u32(p);
        out->mods[i].timer_lane_ref = read_u32(p + 4);
        out->mods[i].first_action = read_u32(p + 8);
        out->mods[i].action_count = p[12];
        out->mods[i].command = p[13];
    }
    return TAYM_OK;
}

static TaymResult parse_actions(const ChunkRef *ref, Taym *out)
{
    size_t count;
    size_t i;

    if (check_record_size(ref, TAYM_ACTN_SIZE, &count) != TAYM_OK) {
        return TAYM_ERROR_FORMAT;
    }
    out->actions = (TaymActn *)calloc_array(count, sizeof(*out->actions));
    if (count != 0 && out->actions == NULL) {
        return TAYM_ERROR_ALLOC;
    }
    out->action_count = count;
    for (i = 0; i < count; i++) {
        const uint8_t *p = ref->payload + i * TAYM_ACTN_SIZE;
        out->actions[i].operand = read_u32(p);
        out->actions[i].target_id = p[4];
        out->actions[i].source_mode = p[5];
    }
    return TAYM_OK;
}

static TaymResult parse_lanes(const ChunkRef *ref, Taym *out)
{
    size_t count;
    size_t i;

    if (check_record_size(ref, TAYM_LANE_SIZE, &count) != TAYM_OK) {
        return TAYM_ERROR_FORMAT;
    }
    out->lanes = (TaymLane *)calloc_array(count, sizeof(*out->lanes));
    if (count != 0 && out->lanes == NULL) {
        return TAYM_ERROR_ALLOC;
    }
    out->lane_count = count;
    for (i = 0; i < count; i++) {
        const uint8_t *p = ref->payload + i * TAYM_LANE_SIZE;
        out->lanes[i].value_offset = read_u32(p);
        out->lanes[i].length = read_u32(p + 4);
        out->lanes[i].loop_index = read_u32(p + 8);
        out->lanes[i].value_type = p[12];
    }
    return TAYM_OK;
}

static TaymResult parse_tlanes(const ChunkRef *ref, Taym *out)
{
    size_t count;
    size_t i;

    if (check_record_size(ref, TAYM_TLAN_SIZE, &count) != TAYM_OK) {
        return TAYM_ERROR_FORMAT;
    }
    out->tlanes = (TaymTlan *)calloc_array(count, sizeof(*out->tlanes));
    if (count != 0 && out->tlanes == NULL) {
        return TAYM_ERROR_ALLOC;
    }
    out->tlane_count = count;
    for (i = 0; i < count; i++) {
        const uint8_t *p = ref->payload + i * TAYM_TLAN_SIZE;
        out->tlanes[i].value_offset = read_u32(p);
        out->tlanes[i].length = read_u32(p + 4);
        out->tlanes[i].loop_index = read_u32(p + 8);
        out->tlanes[i].timing_mode = p[12];
    }
    return TAYM_OK;
}

static TaymResult parse_vu08(const ChunkRef *ref, Taym *out)
{
    out->vu08 = copy_payload(ref->payload, ref->size);
    if (ref->size != 0 && out->vu08 == NULL) {
        return TAYM_ERROR_ALLOC;
    }
    out->vu08_count = ref->size;
    return TAYM_OK;
}

static TaymResult parse_vu16(const ChunkRef *ref, Taym *out)
{
    size_t count;
    size_t i;

    if (ref->size % 2 != 0) {
        return TAYM_ERROR_FORMAT;
    }
    count = ref->size / 2;
    out->vu16 = (uint16_t *)calloc_array(count, sizeof(*out->vu16));
    if (count != 0 && out->vu16 == NULL) {
        return TAYM_ERROR_ALLOC;
    }
    out->vu16_count = count;
    for (i = 0; i < count; i++) {
        out->vu16[i] = read_u16(ref->payload + i * 2);
    }
    return TAYM_OK;
}

static TaymResult parse_vu32(const ChunkRef *ref, Taym *out)
{
    size_t count;
    size_t i;

    if (ref->size % 4 != 0) {
        return TAYM_ERROR_FORMAT;
    }
    count = ref->size / 4;
    out->vu32 = (uint32_t *)calloc_array(count, sizeof(*out->vu32));
    if (count != 0 && out->vu32 == NULL) {
        return TAYM_ERROR_ALLOC;
    }
    out->vu32_count = count;
    for (i = 0; i < count; i++) {
        out->vu32[i] = read_u32(ref->payload + i * 4);
    }
    return TAYM_OK;
}

static TaymResult parse_info(const ChunkRef *ref, Taym *out)
{
    out->info = copy_payload(ref->payload, ref->size);
    if (ref->size != 0 && out->info == NULL) {
        return TAYM_ERROR_ALLOC;
    }
    out->info_size = ref->size;
    return TAYM_OK;
}

static TaymResult parse_extra_chunks(const ChunkRef *refs, size_t ref_count, Taym *out)
{
    size_t extra_count = 0;
    size_t i;
    size_t j = 0;

    for (i = 0; i < ref_count; i++) {
        if (!is_core_tag(refs[i].tag) && !is_info_tag(refs[i].tag)) {
            extra_count++;
        }
    }
    out->chunks = (TaymChunk *)calloc_array(extra_count, sizeof(*out->chunks));
    if (extra_count != 0 && out->chunks == NULL) {
        return TAYM_ERROR_ALLOC;
    }
    out->chunk_count = extra_count;
    for (i = 0; i < ref_count; i++) {
        if (is_core_tag(refs[i].tag) || is_info_tag(refs[i].tag)) {
            continue;
        }
        memcpy(out->chunks[j].tag, refs[i].tag, 4);
        out->chunks[j].data = copy_payload(refs[i].payload, refs[i].size);
        if (refs[i].size != 0 && out->chunks[j].data == NULL) {
            return TAYM_ERROR_ALLOC;
        }
        out->chunks[j].size = refs[i].size;
        j++;
    }
    return TAYM_OK;
}

static TaymResult parse_required_chunks(const ChunkRef *refs, size_t ref_count,
                                        Taym *out)
{
    const ChunkRef *ref;
    TaymResult r;

    r = require_ref(refs, ref_count, "TRAK", &ref);
    if (r != TAYM_OK) return r;
    r = parse_trak(ref, &out->trak);
    if (r != TAYM_OK) return r;

    r = require_ref(refs, ref_count, "CHIP", &ref);
    if (r != TAYM_OK) return r;
    r = parse_chips(ref, out);
    if (r != TAYM_OK) return r;

    r = require_ref(refs, ref_count, "TIMR", &ref);
    if (r != TAYM_OK) return r;
    r = parse_timers(ref, out);
    if (r != TAYM_OK) return r;

    r = require_ref(refs, ref_count, "MODS", &ref);
    if (r != TAYM_OK) return r;
    r = parse_mods(ref, out);
    if (r != TAYM_OK) return r;

    r = require_ref(refs, ref_count, "ACTN", &ref);
    if (r != TAYM_OK) return r;
    r = parse_actions(ref, out);
    if (r != TAYM_OK) return r;

    r = require_ref(refs, ref_count, "LANE", &ref);
    if (r != TAYM_OK) return r;
    r = parse_lanes(ref, out);
    if (r != TAYM_OK) return r;

    r = require_ref(refs, ref_count, "TLAN", &ref);
    if (r != TAYM_OK) return r;
    r = parse_tlanes(ref, out);
    if (r != TAYM_OK) return r;

    r = require_ref(refs, ref_count, "VU08", &ref);
    if (r != TAYM_OK) return r;
    r = parse_vu08(ref, out);
    if (r != TAYM_OK) return r;

    r = require_ref(refs, ref_count, "VU16", &ref);
    if (r != TAYM_OK) return r;
    r = parse_vu16(ref, out);
    if (r != TAYM_OK) return r;

    r = require_ref(refs, ref_count, "VU32", &ref);
    if (r != TAYM_OK) return r;
    return parse_vu32(ref, out);
}

TaymResult taym_read_bytes(const uint8_t *data, size_t size, Taym *out)
{
    ChunkRef *refs = NULL;
    size_t ref_count = 0;
    size_t ref_cap = 0;
    uint16_t version;
    uint16_t header_size;
    uint32_t flags;
    uint32_t chunk_bytes;
    size_t end;
    size_t p;
    Taym tmp;
    TaymResult r;
    const ChunkRef *info_ref;

    if (data == NULL || out == NULL) {
        return TAYM_ERROR_ARGUMENT;
    }
    taym_init(out);
    taym_init(&tmp);

    if (size < TAYM_HEADER_SIZE) {
        return TAYM_ERROR_FORMAT;
    }
    if (memcmp(data, "TAYM", 4) != 0) {
        return TAYM_ERROR_FORMAT;
    }
    version = read_u16(data + 4);
    header_size = read_u16(data + 6);
    flags = read_u32(data + 8);
    chunk_bytes = read_u32(data + 12);

    if (version != TAYM_VERSION || header_size != TAYM_HEADER_SIZE) {
        return TAYM_ERROR_FORMAT;
    }
    if ((size_t)header_size > SIZE_MAX - (size_t)chunk_bytes) {
        return TAYM_ERROR_FORMAT;
    }
    end = (size_t)header_size + (size_t)chunk_bytes;
    if (end != size) {
        return TAYM_ERROR_FORMAT;
    }

    p = header_size;
    while (p < end) {
        char tag[4];
        uint32_t payload_size;

        if (end - p < TAYM_CHUNK_HEADER_SIZE) {
            r = TAYM_ERROR_FORMAT;
            goto fail;
        }
        memcpy(tag, data + p, 4);
        payload_size = read_u32(data + p + 4);
        p += TAYM_CHUNK_HEADER_SIZE;
        if ((size_t)payload_size > end - p) {
            r = TAYM_ERROR_FORMAT;
            goto fail;
        }
        r = append_refs(&refs, &ref_count, &ref_cap, tag, data + p, payload_size);
        if (r != TAYM_OK) {
            goto fail;
        }
        p += payload_size;
    }

    tmp.flags = flags;
    r = parse_required_chunks(refs, ref_count, &tmp);
    if (r != TAYM_OK) {
        goto fail;
    }
    info_ref = find_ref(refs, ref_count, "INFO");
    if (info_ref != NULL) {
        r = parse_info(info_ref, &tmp);
        if (r != TAYM_OK) {
            goto fail;
        }
    }
    r = parse_extra_chunks(refs, ref_count, &tmp);
    if (r != TAYM_OK) {
        goto fail;
    }

    free(refs);
    *out = tmp;
    return TAYM_OK;

fail:
    free(refs);
    taym_free(&tmp);
    return r;
}

TaymResult taym_read_file(const char *path, Taym *out)
{
    FILE *fp;
    long file_size;
    uint8_t *data;
    size_t got;
    TaymResult r;

    if (path == NULL || out == NULL) {
        return TAYM_ERROR_ARGUMENT;
    }
    fp = fopen(path, "rb");
    if (fp == NULL) {
        return TAYM_ERROR_IO;
    }
    if (fseek(fp, 0, SEEK_END) != 0) {
        fclose(fp);
        return TAYM_ERROR_IO;
    }
    file_size = ftell(fp);
    if (file_size < 0) {
        fclose(fp);
        return TAYM_ERROR_IO;
    }
    if (fseek(fp, 0, SEEK_SET) != 0) {
        fclose(fp);
        return TAYM_ERROR_IO;
    }
    data = (uint8_t *)malloc((size_t)file_size == 0 ? 1 : (size_t)file_size);
    if (data == NULL) {
        fclose(fp);
        return TAYM_ERROR_ALLOC;
    }
    got = fread(data, 1, (size_t)file_size, fp);
    if (got != (size_t)file_size || ferror(fp)) {
        free(data);
        fclose(fp);
        return TAYM_ERROR_IO;
    }
    fclose(fp);
    r = taym_read_bytes(data, (size_t)file_size, out);
    free(data);
    return r;
}

static TaymResult writer_reserve(Writer *w, size_t add)
{
    uint8_t *next;
    size_t need;
    size_t cap;

    if (add > SIZE_MAX - w->size) {
        return TAYM_ERROR_ALLOC;
    }
    need = w->size + add;
    if (need <= w->cap) {
        return TAYM_OK;
    }
    cap = (w->cap == 0) ? 256 : w->cap;
    while (cap < need) {
        if (cap > SIZE_MAX / 2) {
            cap = need;
            break;
        }
        cap *= 2;
    }
    next = (uint8_t *)realloc(w->data, cap);
    if (next == NULL) {
        return TAYM_ERROR_ALLOC;
    }
    w->data = next;
    w->cap = cap;
    return TAYM_OK;
}

static TaymResult writer_append(Writer *w, const void *data, size_t size)
{
    TaymResult r;

    if (size == 0) {
        return TAYM_OK;
    }
    r = writer_reserve(w, size);
    if (r != TAYM_OK) {
        return r;
    }
    memcpy(w->data + w->size, data, size);
    w->size += size;
    return TAYM_OK;
}

static TaymResult writer_append_zeroes(Writer *w, size_t size)
{
    TaymResult r;

    if (size == 0) {
        return TAYM_OK;
    }
    r = writer_reserve(w, size);
    if (r != TAYM_OK) {
        return r;
    }
    memset(w->data + w->size, 0, size);
    w->size += size;
    return TAYM_OK;
}

static TaymResult writer_append_u8(Writer *w, uint8_t v)
{
    return writer_append(w, &v, 1);
}

static TaymResult writer_append_u16(Writer *w, uint16_t v)
{
    uint8_t bytes[2];
    write_u16(bytes, v);
    return writer_append(w, bytes, sizeof(bytes));
}

static TaymResult writer_append_u32(Writer *w, uint32_t v)
{
    uint8_t bytes[4];
    write_u32(bytes, v);
    return writer_append(w, bytes, sizeof(bytes));
}

static int taglist_contains(const TagList *tags, const char tag[4])
{
    size_t i;

    for (i = 0; i < tags->count; i++) {
        if (memcmp(tags->tags + i * 4, tag, 4) == 0) {
            return 1;
        }
    }
    return 0;
}

static TaymResult taglist_add(TagList *tags, const char tag[4])
{
    char *next;
    size_t next_cap;

    if (taglist_contains(tags, tag)) {
        return TAYM_ERROR_FORMAT;
    }
    if (tags->count == tags->cap) {
        next_cap = (tags->cap == 0) ? 16 : tags->cap * 2;
        if (next_cap < tags->cap || next_cap > SIZE_MAX / 4) {
            return TAYM_ERROR_ALLOC;
        }
        next = (char *)realloc(tags->tags, next_cap * 4);
        if (next == NULL) {
            return TAYM_ERROR_ALLOC;
        }
        tags->tags = next;
        tags->cap = next_cap;
    }
    memcpy(tags->tags + tags->count * 4, tag, 4);
    tags->count++;
    return TAYM_OK;
}

static TaymResult chunk_begin(Writer *w, TagList *tags, const char tag[4],
                              size_t payload_size)
{
    TaymResult r;

    if (payload_size > UINT32_MAX) {
        return TAYM_ERROR_RANGE;
    }
    r = taglist_add(tags, tag);
    if (r != TAYM_OK) {
        return r;
    }
    r = writer_append(w, tag, 4);
    if (r != TAYM_OK) {
        return r;
    }
    return writer_append_u32(w, (uint32_t)payload_size);
}

static TaymResult record_payload_size(size_t count, size_t stride, size_t *payload)
{
    if (!checked_mul_size(count, stride, payload)) {
        return TAYM_ERROR_RANGE;
    }
    if (*payload > UINT32_MAX) {
        return TAYM_ERROR_RANGE;
    }
    return TAYM_OK;
}

static TaymResult write_trak_chunk(Writer *w, TagList *tags, const Taym *taym)
{
    TaymResult r;

    if (taym->chip_count > UINT8_MAX || taym->timer_count > UINT8_MAX) {
        return TAYM_ERROR_RANGE;
    }
    r = chunk_begin(w, tags, "TRAK", TAYM_TRAK_SIZE);
    if (r != TAYM_OK) return r;
    r = writer_append_u32(w, taym->trak.frame_rate);
    if (r != TAYM_OK) return r;
    r = writer_append_u32(w, taym->trak.frame_count);
    if (r != TAYM_OK) return r;
    r = writer_append_u32(w, taym->trak.loop_frame);
    if (r != TAYM_OK) return r;
    r = writer_append_u8(w, (uint8_t)taym->chip_count);
    if (r != TAYM_OK) return r;
    r = writer_append_u8(w, (uint8_t)taym->timer_count);
    if (r != TAYM_OK) return r;
    return writer_append_u16(w, 0);
}

static TaymResult write_info_chunk(Writer *w, TagList *tags, const Taym *taym)
{
    TaymResult r;

    if (taym->info_size == 0) {
        return TAYM_OK;
    }
    if (taym->info == NULL) {
        return TAYM_ERROR_ARGUMENT;
    }
    r = chunk_begin(w, tags, "INFO", taym->info_size);
    if (r != TAYM_OK) return r;
    return writer_append(w, taym->info, taym->info_size);
}

static TaymResult write_chips_chunk(Writer *w, TagList *tags, const Taym *taym)
{
    TaymResult r;
    size_t payload;
    size_t i;

    r = record_payload_size(taym->chip_count, TAYM_CHIP_SIZE, &payload);
    if (r != TAYM_OK) return r;
    if (taym->chip_count != 0 && taym->chips == NULL) {
        return TAYM_ERROR_ARGUMENT;
    }
    r = chunk_begin(w, tags, "CHIP", payload);
    if (r != TAYM_OK) return r;
    for (i = 0; i < taym->chip_count; i++) {
        const TaymChip *c = &taym->chips[i];
        r = writer_append_u32(w, c->clock_hz);
        if (r != TAYM_OK) return r;
        r = writer_append_u8(w, c->chip_type_id);
        if (r != TAYM_OK) return r;
        r = writer_append_u8(w, c->variant);
        if (r != TAYM_OK) return r;
        r = writer_append_u16(w, 0);
        if (r != TAYM_OK) return r;
        r = writer_append(w, c->name, 16);
        if (r != TAYM_OK) return r;
        r = writer_append(w, c->frame_data_tag, 4);
        if (r != TAYM_OK) return r;
        r = writer_append_u32(w, c->config);
        if (r != TAYM_OK) return r;
    }
    return TAYM_OK;
}

static TaymResult write_timers_chunk(Writer *w, TagList *tags, const Taym *taym)
{
    TaymResult r;
    size_t payload;
    size_t i;

    r = record_payload_size(taym->timer_count, TAYM_TIMR_SIZE, &payload);
    if (r != TAYM_OK) return r;
    if (taym->timer_count != 0 && taym->timers == NULL) {
        return TAYM_ERROR_ARGUMENT;
    }
    r = chunk_begin(w, tags, "TIMR", payload);
    if (r != TAYM_OK) return r;
    for (i = 0; i < taym->timer_count; i++) {
        const TaymTimr *t = &taym->timers[i];
        r = writer_append_u16(w, t->clock_divider);
        if (r != TAYM_OK) return r;
        r = writer_append_u8(w, t->chip_index);
        if (r != TAYM_OK) return r;
        r = writer_append_u8(w, t->clock_mode);
        if (r != TAYM_OK) return r;
        r = writer_append_u16(w, 0);
        if (r != TAYM_OK) return r;
    }
    return TAYM_OK;
}

static TaymResult write_mods_chunk(Writer *w, TagList *tags, const Taym *taym)
{
    TaymResult r;
    size_t payload;
    size_t i;

    r = record_payload_size(taym->mod_count, TAYM_MODS_SIZE, &payload);
    if (r != TAYM_OK) return r;
    if (taym->mod_count != 0 && taym->mods == NULL) {
        return TAYM_ERROR_ARGUMENT;
    }
    r = chunk_begin(w, tags, "MODS", payload);
    if (r != TAYM_OK) return r;
    for (i = 0; i < taym->mod_count; i++) {
        const TaymMods *m = &taym->mods[i];
        if (m->command == TAYM_CMD_EMPTY || m->command == TAYM_CMD_STOP) {
            r = writer_append_zeroes(w, 12);
            if (r != TAYM_OK) return r;
            r = writer_append_u8(w, 0);
            if (r != TAYM_OK) return r;
            r = writer_append_u8(w, m->command);
            if (r != TAYM_OK) return r;
            r = writer_append_u16(w, 0);
            if (r != TAYM_OK) return r;
            continue;
        }
        r = writer_append_u32(w, m->base_timer_value);
        if (r != TAYM_OK) return r;
        r = writer_append_u32(w, m->timer_lane_ref);
        if (r != TAYM_OK) return r;
        r = writer_append_u32(w, m->first_action);
        if (r != TAYM_OK) return r;
        r = writer_append_u8(w, m->action_count);
        if (r != TAYM_OK) return r;
        r = writer_append_u8(w, m->command);
        if (r != TAYM_OK) return r;
        r = writer_append_u16(w, 0);
        if (r != TAYM_OK) return r;
    }
    return TAYM_OK;
}

static TaymResult write_actions_chunk(Writer *w, TagList *tags, const Taym *taym)
{
    TaymResult r;
    size_t payload;
    size_t i;

    r = record_payload_size(taym->action_count, TAYM_ACTN_SIZE, &payload);
    if (r != TAYM_OK) return r;
    if (taym->action_count != 0 && taym->actions == NULL) {
        return TAYM_ERROR_ARGUMENT;
    }
    r = chunk_begin(w, tags, "ACTN", payload);
    if (r != TAYM_OK) return r;
    for (i = 0; i < taym->action_count; i++) {
        const TaymActn *a = &taym->actions[i];
        r = writer_append_u32(w, a->operand);
        if (r != TAYM_OK) return r;
        r = writer_append_u8(w, a->target_id);
        if (r != TAYM_OK) return r;
        r = writer_append_u8(w, a->source_mode);
        if (r != TAYM_OK) return r;
    }
    return TAYM_OK;
}

static TaymResult write_lanes_chunk(Writer *w, TagList *tags, const Taym *taym)
{
    TaymResult r;
    size_t payload;
    size_t i;

    r = record_payload_size(taym->lane_count, TAYM_LANE_SIZE, &payload);
    if (r != TAYM_OK) return r;
    if (taym->lane_count != 0 && taym->lanes == NULL) {
        return TAYM_ERROR_ARGUMENT;
    }
    r = chunk_begin(w, tags, "LANE", payload);
    if (r != TAYM_OK) return r;
    for (i = 0; i < taym->lane_count; i++) {
        const TaymLane *l = &taym->lanes[i];
        r = writer_append_u32(w, l->value_offset);
        if (r != TAYM_OK) return r;
        r = writer_append_u32(w, l->length);
        if (r != TAYM_OK) return r;
        r = writer_append_u32(w, l->loop_index);
        if (r != TAYM_OK) return r;
        r = writer_append_u8(w, l->value_type);
        if (r != TAYM_OK) return r;
        r = writer_append_zeroes(w, 3);
        if (r != TAYM_OK) return r;
    }
    return TAYM_OK;
}

static TaymResult write_tlanes_chunk(Writer *w, TagList *tags, const Taym *taym)
{
    TaymResult r;
    size_t payload;
    size_t i;

    r = record_payload_size(taym->tlane_count, TAYM_TLAN_SIZE, &payload);
    if (r != TAYM_OK) return r;
    if (taym->tlane_count != 0 && taym->tlanes == NULL) {
        return TAYM_ERROR_ARGUMENT;
    }
    r = chunk_begin(w, tags, "TLAN", payload);
    if (r != TAYM_OK) return r;
    for (i = 0; i < taym->tlane_count; i++) {
        const TaymTlan *t = &taym->tlanes[i];
        r = writer_append_u32(w, t->value_offset);
        if (r != TAYM_OK) return r;
        r = writer_append_u32(w, t->length);
        if (r != TAYM_OK) return r;
        r = writer_append_u32(w, t->loop_index);
        if (r != TAYM_OK) return r;
        r = writer_append_u8(w, t->timing_mode);
        if (r != TAYM_OK) return r;
        r = writer_append_zeroes(w, 3);
        if (r != TAYM_OK) return r;
    }
    return TAYM_OK;
}

static TaymResult write_vu08_chunk(Writer *w, TagList *tags, const Taym *taym)
{
    TaymResult r;

    if (taym->vu08_count > UINT32_MAX) {
        return TAYM_ERROR_RANGE;
    }
    if (taym->vu08_count != 0 && taym->vu08 == NULL) {
        return TAYM_ERROR_ARGUMENT;
    }
    r = chunk_begin(w, tags, "VU08", taym->vu08_count);
    if (r != TAYM_OK) return r;
    return writer_append(w, taym->vu08, taym->vu08_count);
}

static TaymResult write_vu16_chunk(Writer *w, TagList *tags, const Taym *taym)
{
    TaymResult r;
    size_t payload;
    size_t i;

    r = record_payload_size(taym->vu16_count, 2, &payload);
    if (r != TAYM_OK) return r;
    if (taym->vu16_count != 0 && taym->vu16 == NULL) {
        return TAYM_ERROR_ARGUMENT;
    }
    r = chunk_begin(w, tags, "VU16", payload);
    if (r != TAYM_OK) return r;
    for (i = 0; i < taym->vu16_count; i++) {
        r = writer_append_u16(w, taym->vu16[i]);
        if (r != TAYM_OK) return r;
    }
    return TAYM_OK;
}

static TaymResult write_vu32_chunk(Writer *w, TagList *tags, const Taym *taym)
{
    TaymResult r;
    size_t payload;
    size_t i;

    r = record_payload_size(taym->vu32_count, 4, &payload);
    if (r != TAYM_OK) return r;
    if (taym->vu32_count != 0 && taym->vu32 == NULL) {
        return TAYM_ERROR_ARGUMENT;
    }
    r = chunk_begin(w, tags, "VU32", payload);
    if (r != TAYM_OK) return r;
    for (i = 0; i < taym->vu32_count; i++) {
        r = writer_append_u32(w, taym->vu32[i]);
        if (r != TAYM_OK) return r;
    }
    return TAYM_OK;
}

static int model_chunks_have_duplicate_tags(const Taym *taym)
{
    size_t i;
    size_t j;

    if (taym->chunk_count != 0 && taym->chunks == NULL) {
        return 1;
    }
    for (i = 0; i < taym->chunk_count; i++) {
        for (j = i + 1; j < taym->chunk_count; j++) {
            if (taym_tag_equal(taym->chunks[i].tag, taym->chunks[j].tag)) {
                return 1;
            }
        }
    }
    return 0;
}

static TaymResult write_payload_chunk(Writer *w, TagList *tags,
                                      const TaymChunk *chunk)
{
    TaymResult r;

    if (is_core_tag(chunk->tag) || is_info_tag(chunk->tag)) {
        return TAYM_ERROR_FORMAT;
    }
    if (chunk->size != 0 && chunk->data == NULL) {
        return TAYM_ERROR_ARGUMENT;
    }
    r = chunk_begin(w, tags, chunk->tag, chunk->size);
    if (r != TAYM_OK) return r;
    return writer_append(w, chunk->data, chunk->size);
}

static TaymResult write_extra_chunks(Writer *w, TagList *tags, const Taym *taym)
{
    size_t i;
    const TaymChunk *chunk;
    TaymResult r;

    if (model_chunks_have_duplicate_tags(taym)) {
        return TAYM_ERROR_FORMAT;
    }

    for (i = 0; i < taym->chip_count; i++) {
        if (taym_tag_is_zero(taym->chips[i].frame_data_tag)) {
            continue;
        }
        if (taglist_contains(tags, taym->chips[i].frame_data_tag)) {
            return TAYM_ERROR_FORMAT;
        }
        chunk = taym_find_chunk(taym, taym->chips[i].frame_data_tag);
        if (chunk == NULL) {
            return TAYM_ERROR_FORMAT;
        }
        r = write_payload_chunk(w, tags, chunk);
        if (r != TAYM_OK) return r;
    }

    for (i = 0; i < taym->chunk_count; i++) {
        if (taglist_contains(tags, taym->chunks[i].tag)) {
            continue;
        }
        r = write_payload_chunk(w, tags, &taym->chunks[i]);
        if (r != TAYM_OK) return r;
    }
    return TAYM_OK;
}

TaymResult taym_write_bytes(const Taym *taym, uint8_t **out_data, size_t *out_size)
{
    Writer w;
    TagList tags;
    TaymResult r;
    uint32_t chunk_bytes;

    if (taym == NULL || out_data == NULL || out_size == NULL) {
        return TAYM_ERROR_ARGUMENT;
    }
    *out_data = NULL;
    *out_size = 0;
    memset(&w, 0, sizeof(w));
    memset(&tags, 0, sizeof(tags));

    r = writer_append(&w, "TAYM", 4);
    if (r != TAYM_OK) goto fail;
    r = writer_append_u16(&w, TAYM_VERSION);
    if (r != TAYM_OK) goto fail;
    r = writer_append_u16(&w, TAYM_HEADER_SIZE);
    if (r != TAYM_OK) goto fail;
    r = writer_append_u32(&w, taym->flags);
    if (r != TAYM_OK) goto fail;
    r = writer_append_u32(&w, 0);
    if (r != TAYM_OK) goto fail;

    r = write_trak_chunk(&w, &tags, taym);
    if (r != TAYM_OK) goto fail;
    r = write_info_chunk(&w, &tags, taym);
    if (r != TAYM_OK) goto fail;
    r = write_chips_chunk(&w, &tags, taym);
    if (r != TAYM_OK) goto fail;
    r = write_timers_chunk(&w, &tags, taym);
    if (r != TAYM_OK) goto fail;
    r = write_mods_chunk(&w, &tags, taym);
    if (r != TAYM_OK) goto fail;
    r = write_actions_chunk(&w, &tags, taym);
    if (r != TAYM_OK) goto fail;
    r = write_lanes_chunk(&w, &tags, taym);
    if (r != TAYM_OK) goto fail;
    r = write_tlanes_chunk(&w, &tags, taym);
    if (r != TAYM_OK) goto fail;
    r = write_vu08_chunk(&w, &tags, taym);
    if (r != TAYM_OK) goto fail;
    r = write_vu16_chunk(&w, &tags, taym);
    if (r != TAYM_OK) goto fail;
    r = write_vu32_chunk(&w, &tags, taym);
    if (r != TAYM_OK) goto fail;
    r = write_extra_chunks(&w, &tags, taym);
    if (r != TAYM_OK) goto fail;

    if (w.size < TAYM_HEADER_SIZE || w.size - TAYM_HEADER_SIZE > UINT32_MAX) {
        r = TAYM_ERROR_RANGE;
        goto fail;
    }
    chunk_bytes = (uint32_t)(w.size - TAYM_HEADER_SIZE);
    write_u32(w.data + 12, chunk_bytes);

    free(tags.tags);
    *out_data = w.data;
    *out_size = w.size;
    return TAYM_OK;

fail:
    free(tags.tags);
    free(w.data);
    return r;
}

TaymResult taym_write_file(const char *path, const Taym *taym)
{
    uint8_t *data = NULL;
    size_t size = 0;
    FILE *fp;
    TaymResult r;
    size_t wrote;

    if (path == NULL || taym == NULL) {
        return TAYM_ERROR_ARGUMENT;
    }
    r = taym_write_bytes(taym, &data, &size);
    if (r != TAYM_OK) {
        return r;
    }
    fp = fopen(path, "wb");
    if (fp == NULL) {
        free(data);
        return TAYM_ERROR_IO;
    }
    wrote = fwrite(data, 1, size, fp);
    if (wrote != size || ferror(fp)) {
        free(data);
        fclose(fp);
        return TAYM_ERROR_IO;
    }
    if (fclose(fp) != 0) {
        free(data);
        return TAYM_ERROR_IO;
    }
    free(data);
    return TAYM_OK;
}
