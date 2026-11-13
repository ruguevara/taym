/*
 * Frame walker for an AY chip: Taym -> register-write timeline. Mirrors the
 * oracle's engine._frame_writes; read the two together. The chip-agnostic
 * part is taym_timers.c; what is AY here is the frame data (a Bulba .psg) and
 * taym_ay_resolve().
 */

#include "taym/taym_player.h"

#include <stdlib.h>
#include <string.h>

#define TAYM_PSG_REGISTERS TAYM_AY_REGISTERS
#define TAYM_R13 13u
/* A frame's R13 when the stream does not write R13 in that frame. */
#define TAYM_R13_NO_WRITE 0xFFu

uint64_t taym_player_frame_cycle(uint32_t frame, uint32_t clock_hz, uint32_t frame_rate_fix16)
{
    uint64_t numerator;

    if (frame_rate_fix16 == 0u) {
        return 0u;
    }
    /* ceil(frame * clock / fps) with fps as 16.16, done in integers: the
       oracle computes it in double, and for any realistic frame count the two
       agree exactly, while integers stay exact for all of them. */
    numerator = (uint64_t) frame * (uint64_t) clock_hz * 65536u;
    return (numerator + (uint64_t) frame_rate_fix16 - 1u) / (uint64_t) frame_rate_fix16;
}

/* End a frame: store the shadow as row `*frames`. R0..R12 are states and carry
   to the next frame. R13 is an event -- every write of it restarts the
   envelope, the same shape too (S6.2) -- so it goes back to its no-write
   value: a frame holds R13 only when the stream writes it in that frame. */
static TaymResult taym_psg_commit(uint8_t *out,
                                  size_t frame_capacity,
                                  size_t *frames,
                                  uint8_t *shadow)
{
    if (*frames >= frame_capacity) {
        return TAYM_ERROR_RANGE;
    }
    memcpy(out + *frames * TAYM_PSG_REGISTERS, shadow, TAYM_PSG_REGISTERS);
    (*frames)++;
    shadow[TAYM_R13] = TAYM_R13_NO_WRITE;
    return TAYM_OK;
}

TaymResult taym_player_parse_psg(const uint8_t *data,
                                 size_t size,
                                 uint8_t *out,
                                 size_t frame_capacity,
                                 size_t *out_frames)
{
    uint8_t shadow[TAYM_PSG_REGISTERS];
    size_t frames = 0u;
    size_t i;
    int started = 0;

    if (data == NULL || out == NULL || out_frames == NULL) {
        return TAYM_ERROR_ARGUMENT;
    }
    *out_frames = 0u;
    if (size < 16u || memcmp(data, "PSG\x1a", 4) != 0) {
        return TAYM_ERROR_FORMAT;
    }

    memset(shadow, 0, sizeof(shadow));
    shadow[TAYM_R13] = TAYM_R13_NO_WRITE;

    i = 16u;
    while (i < size) {
        const uint8_t b = data[i];
        if (b == 0xFDu) {
            break;
        }
        if (b == 0xFFu) {
            if (started && taym_psg_commit(out, frame_capacity, &frames, shadow) != TAYM_OK) {
                return TAYM_ERROR_RANGE;
            }
            started = 1;
            i += 1u;
        } else if (b == 0xFEu) {
            size_t n;
            if (i + 1u >= size) {
                break;
            }
            n = (size_t) data[i + 1u] * 4u; /* standard Bulba: $FE n is 4*n frames */
            i += 2u;
            started = 1;
            while (n-- > 0u) {
                if (taym_psg_commit(out, frame_capacity, &frames, shadow) != TAYM_OK) {
                    return TAYM_ERROR_RANGE;
                }
            }
        } else {
            if (i + 1u >= size) {
                break;
            }
            if (b < TAYM_PSG_REGISTERS) {
                shadow[b] = data[i + 1u];
            }
            i += 2u;
        }
    }
    if (started && taym_psg_commit(out, frame_capacity, &frames, shadow) != TAYM_OK) {
        return TAYM_ERROR_RANGE;
    }

    *out_frames = frames;
    return TAYM_OK;
}

/*
 * S13.1: latch the background `regs` as writes at the frame's start cycle:
 * R0..R12 when they changed since the last write, R13 also whenever the stream
 * writes it in this frame (`r13_written`).
 *
 * An owned register belongs to its timer, so skip it and mark its shadow stale
 * so the background re-asserts cleanly on release.
 *
 * R13 is not held to the delta rule, because writing it restarts the envelope,
 * the same shape too: a repeated R13 write in the stream is a retrigger and
 * must be kept (S6.2). R0..R12 stay on the delta rule: a repeated write of the
 * value such a register already holds changes nothing audible, so dropping it
 * loses nothing.
 */
static size_t taym_push_background(const TaymTimerBank *bank,
                                   const uint8_t *regs,
                                   int r13_written,
                                   int16_t *shadow,
                                   uint64_t cycle,
                                   TaymTimerWrite *out,
                                   size_t capacity)
{
    size_t produced = 0u;
    uint8_t r;

    for (r = 0u; r < TAYM_PSG_REGISTERS; r++) {
        uint8_t value;

        if (taym_timers_owns_target(bank, r)) {
            shadow[r] = -1; /* stale: force a rewrite when the timer releases it */
            continue;
        }
        value = regs[r];
        if (r == TAYM_R13 && (value & 0x80u) != 0u) {
            continue; /* the stream has not written R13 yet */
        }
        if (shadow[r] == (int16_t) value && !(r == TAYM_R13 && r13_written)) {
            continue;
        }
        if (produced >= capacity) {
            break;
        }
        memset(&out[produced], 0, sizeof(out[produced]));
        out[produced].cycle = cycle;
        out[produced].target = r;
        out[produced].value = value;
        produced++;
        shadow[r] = (int16_t) value;
    }
    return produced;
}

TaymResult taym_player_run(const Taym *taym,
                           size_t chip_index,
                           TaymTimerWrite *scratch,
                           size_t capacity,
                           TaymPlayerSink sink,
                           void *context)
{
    TaymTimerBank bank;
    size_t timer_map[TAYM_MAX_TIMERS];
    size_t bank_timers = 0u;
    int16_t shadow[TAYM_PSG_REGISTERS];
    uint8_t background_r13 = TAYM_R13_NO_WRITE; /* the background's last R13 write */
    uint8_t *psg_frames = NULL;
    size_t psg_frame_count = 0u;
    uint8_t silent[TAYM_PSG_REGISTERS];
    uint32_t frame;
    uint32_t frame_limit;
    TaymResult result;

    if (taym == NULL || scratch == NULL || sink == NULL) {
        return TAYM_ERROR_ARGUMENT;
    }
    if (capacity < TAYM_AY_REGISTERS + TAYM_MAX_TIMER_TARGETS) {
        return TAYM_ERROR_ARGUMENT;
    }
    if (chip_index >= taym->chip_count) {
        return TAYM_ERROR_ARGUMENT;
    }
    if (taym->chips[chip_index].chip_type_id != TAYM_CHIP_TYPE_AY) {
        return TAYM_ERROR_FORMAT; /* this walker knows AY frame data only */
    }

    result = taym_timers_init_from_taym(&bank, taym, chip_index, timer_map, &bank_timers);
    if (result != TAYM_OK) {
        return result;
    }

    /* The chip's background when it has no frame data: all-zero registers,
       R13 included. The 0xFF don't-write sentinel is a *PSG stream's* initial
       shadow (S6.2), not a value a chip without a stream holds -- so here R13
       is a real zero and gets latched like any other register: once, in
       frame 0, since R13 is an event (see taym_push_background). */
    memset(silent, 0, sizeof(silent));
    frame_limit = taym->trak.frame_count;

    if (!taym_tag_is_zero(taym->chips[chip_index].frame_data_tag)) {
        const TaymChunk *chunk = taym_find_chunk(taym, taym->chips[chip_index].frame_data_tag);
        if (chunk != NULL) {
            psg_frames = (uint8_t *) malloc((size_t) frame_limit * TAYM_PSG_REGISTERS);
            if (psg_frames == NULL) {
                return TAYM_ERROR_ALLOC;
            }
            result = taym_player_parse_psg(chunk->data, chunk->size, psg_frames,
                                           frame_limit, &psg_frame_count);
            if (result != TAYM_OK && result != TAYM_ERROR_RANGE) {
                free(psg_frames);
                return result;
            }
            /* A stream longer than the track is clipped; a shorter one ends
               the walk, as the oracle's min(frame_count, len(bg)) does. */
            if (psg_frame_count < (size_t) frame_limit) {
                frame_limit = (uint32_t) psg_frame_count;
            }
        }
    }

    {
        size_t r;
        for (r = 0u; r < TAYM_PSG_REGISTERS; r++) {
            shadow[r] = -1;
        }
    }

    for (frame = 0u; frame < frame_limit; frame++) {
        const uint64_t start = taym_player_frame_cycle(frame, bank.clock_hz,
                                                       taym->trak.frame_rate);
        const uint64_t end = taym_player_frame_cycle(frame + 1u, bank.clock_hz,
                                                     taym->trak.frame_rate);
        uint8_t regs[TAYM_PSG_REGISTERS];
        int r13_written;
        size_t count = 0u;
        size_t produced = 0u;
        size_t i;

        /* 1. Resolve this frame's command for each of the chip's timers. */
        for (i = 0u; i < bank_timers; i++) {
            const size_t record = (size_t) frame * taym->timer_count + timer_map[i];
            if (record >= taym->mod_count) {
                continue;
            }
            result = taym_timers_command(&bank, i, &taym->mods[record], start);
            if (result != TAYM_OK) {
                free(psg_frames);
                return result;
            }
        }

        /* 2-3. Push the changed background, skipping owned registers.

           R13 is an event, not a state: the frame carries it only when the
           stream writes it, and then it is written whatever its value. The
           background's last shape is still remembered (a write under a timer
           included), so a release can hand it back. */
        memcpy(regs, psg_frames != NULL
                   ? psg_frames + (size_t) frame * TAYM_PSG_REGISTERS
                   : silent,
               sizeof(regs));
        silent[TAYM_R13] = TAYM_R13_NO_WRITE;
        r13_written = (regs[TAYM_R13] & 0x80u) == 0u;
        if (r13_written) {
            background_r13 = regs[TAYM_R13];
        }
        regs[TAYM_R13] = background_r13;
        count = taym_push_background(&bank, regs, r13_written, shadow, start, scratch, capacity);

        /* 4. Every expiry that falls inside this frame, in cycle order. A timer
           with no rate fires once per frame. */
        bank.no_rate_interval = end - start;
        result = taym_timers_collect(&bank, end, scratch + count, capacity - count, &produced);
        count += taym_ay_resolve(scratch + count, produced, taym->chips[chip_index].variant);
        if (result != TAYM_OK) {
            sink(context, frame, scratch, count);
            free(psg_frames);
            return result;
        }

        sink(context, frame, scratch, count);
    }

    free(psg_frames);
    return TAYM_OK;
}
