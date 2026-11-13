#ifndef TAYM_TAYM_PLAYER_H
#define TAYM_TAYM_PLAYER_H

/*
 * Frame walker: a whole Taym for one AY chip -> the register-write timeline.
 *
 * This is the C counterpart of the oracle's engine._frame_writes, and the two
 * must agree write for write. It is the thin part: taym_timers.c holds the
 * behavior, this adds the frame loop, the PSG background and the background
 * push of S13.1.
 *
 * It needs no audio. A renderer hands these writes to an emulator; the test
 * compares them to the oracle's as text.
 *
 * Unlike taym_timers.c this allocates (one decoded PSG background) and is an
 * offline walker, not an audio-thread component. A real-time host drives
 * taym_timers.c directly from its own frame source.
 */

#include <stddef.h>
#include <stdint.h>

#include "taym/taym.h"
#include "taym/taym_ay.h"
#include "taym/taym_timers.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Register writes of one frame, in cycle order: `target` is R0..R13 and
   `value` the byte. */
typedef void (*TaymPlayerSink)(void *context,
                               uint32_t frame,
                               const TaymTimerWrite *writes,
                               size_t count);

/*
 * Walk `taym`'s chip `chip_index` from frame 0 to TRAK.frame_count (or the end
 * of its PSG stream, whichever comes first) and report each frame's writes.
 *
 * The chip must be an AY (TAYM_ERROR_FORMAT otherwise): its frame data is a
 * Bulba .psg and its targets follow taym_ay.h.
 *
 * Needs scratch space for one frame's writes: `capacity` must be at least
 * TAYM_AY_REGISTERS + TAYM_MAX_TIMER_TARGETS, and more for a fast timer --
 * a 5 kHz timer at 50 fps is 100 expiries a frame. On overflow the call returns
 * TAYM_ERROR_RANGE after reporting what fitted.
 */
TaymResult taym_player_run(const Taym *taym,
                           size_t chip_index,
                           TaymTimerWrite *scratch,
                           size_t capacity,
                           TaymPlayerSink sink,
                           void *context);

/*
 * The absolute cycle frame `frame` starts at: ceil(frame * clock / fps), so a
 * frame's writes never land before its time.
 */
uint64_t taym_player_frame_cycle(uint32_t frame, uint32_t clock_hz, uint32_t frame_rate_fix16);

/*
 * Decode a Bulba .psg into 14-register frames (S6.2). `out` holds
 * `frame_capacity * 14` bytes, one frame per row. Returns the frame count in
 * `out_frames`. R0..R12 are absolute states. R13 is an event: a row holds the
 * value the stream writes in that frame, a repeated shape included, and 0xFF
 * (no write) in a frame that does not write it.
 */
TaymResult taym_player_parse_psg(const uint8_t *data,
                                 size_t size,
                                 uint8_t *out,
                                 size_t frame_capacity,
                                 size_t *out_frames);

#ifdef __cplusplus
}
#endif

#endif
