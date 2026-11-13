#ifndef TAYM_TAYM_TIMERS_H
#define TAYM_TAYM_TIMERS_H

/*
 * TAYM timer engine -- the interpretation of S9..S13 as a reusable C core.
 *
 * This is the format's *behavior*, apart from any file walking: feed it timer
 * commands and it tells you which targets get which values, at which absolute
 * chip cycle. `taym.c` reads bytes; this reads MODS records (from a file, or
 * synthesized live by a host) and produces target writes.
 *
 * It is chip-agnostic. A target is an 8-bit id and a value up to 32 bits wide;
 * what a target means -- which are registers, how wide they are, what a virtual
 * target does -- belongs to the chip. For AY that is taym_ay.h, which turns
 * these writes into register writes.
 *
 * Who uses it:
 *   - taym_player.c, which walks an AY Taym frame by frame and prints the write
 *     timeline. Its test diffs that against the Python oracle's _frame_writes.
 *   - a real-time host (an audio plugin, firmware), which gets commands from
 *     its own UI or MIDI instead of a file and pulls writes per audio block.
 *
 * Properties the design relies on:
 *   - No allocation and no I/O. All state is in TaymTimerBank, which the caller
 *     owns; lanes and actions are borrowed, not copied. Safe on an audio thread.
 *   - Absolute cycle timeline. Expiries carry across frame boundaries and
 *     across whatever block boundaries the caller has; cutting the timeline
 *     differently never changes the writes (the spec's invariant 1).
 *   - The caller owns the background. The bank reports which targets a timer
 *     owns so the caller can suppress its own writes to them and re-assert
 *     them on release (S13.1); it holds no register values itself.
 *
 * Exactness against the Python oracle: expiry positions are accumulated in
 * `double` and rounded up to a whole cycle, matching engine.py's arithmetic
 * exactly, so the C player and the oracle agree write-for-write. A CHIP_PERIOD
 * interval is a whole number of cycles and is therefore exact regardless; an
 * ABS_RATE_HZ interval is clock/rate, genuinely fractional, and this is the one
 * place the two implementations must agree on rounding. A fixed-point rewrite
 * would be welcome only together with the oracle's.
 * ponytail: double to match the oracle; move both to fixed-point if a target
 * without an FPU needs this engine.
 */

#include <stddef.h>
#include <stdint.h>

#include "taym/taym.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Targets one timer may own at once (one action slice, S11/S12.2). */
#define TAYM_MAX_TIMER_TARGETS 8u

/* Timers one bank tracks. One per chip voice in practice; TAYM allows 255. */
#define TAYM_MAX_TIMERS 16u

/*
 * One target's value at one timer expiry. Raw: the value is the lane element
 * or inline operand as stored, and the target may be a virtual one. A chip's
 * step (taym_ay_resolve for AY) turns an expiry's writes into register writes,
 * reusing this struct with `target` as the register and `value` as its byte.
 */
typedef struct TaymTimerWrite {
    uint64_t cycle;       /* absolute chip cycle, non-decreasing within a pull */
    uint32_t value;
    uint8_t target;       /* chip-local target id (S11) */
    uint8_t value_type;   /* TAYM_VT_* of the lane it came from; 0 = inline */
    uint8_t timer;        /* bank slot of the timer that fired */
    uint8_t expiry_start; /* 1 on the first write of an expiry: an expiry is
                             atomic (S10.1), and a chip may need its targets
                             together */
    uint8_t loop_start;   /* 1 on every write of an expiry that begins a repeat
                             of the timer's output: each lane it runs, the
                             timer lane included, is on the element it loops
                             back to (a no-loop lane: on its final one). What
                             a scope triggers on. A timer with no lanes
                             repeats at every expiry. */
} TaymTimerWrite;

/*
 * Where a bank reads lanes, actions and pool values. A file-backed host points
 * these at a Taym's arrays; a live host points them at its own tables. The bank
 * borrows them and must not outlive them.
 */
typedef struct TaymTimerTables {
    const TaymActn *actions;
    size_t action_count;
    const TaymLane *lanes;
    size_t lane_count;
    const TaymTlan *tlanes;
    size_t tlane_count;

    const uint8_t *vu08;
    size_t vu08_count;
    const uint16_t *vu16;
    size_t vu16_count;
    const uint32_t *vu32;
    size_t vu32_count;
} TaymTimerTables;

/* Per-timer timing, from its TIMR record. */
typedef struct TaymTimerClock {
    uint16_t clock_divider;
    uint8_t clock_mode; /* TAYM_CLOCK_ABS_RATE_HZ | TAYM_CLOCK_CHIP_PERIOD */
} TaymTimerClock;

/* One target's running source within an active timer. */
typedef struct TaymTimerTarget {
    uint8_t target_id;
    uint8_t has_lane;    /* 0 = inline operand, 1 = bound lane */
    uint8_t lane_pending; /* S12.3: write element 0 without advancing first */
    uint32_t lane_index;  /* LANE index when has_lane */
    uint32_t lane_step;   /* running element index within that lane */
    uint32_t inline_value;
} TaymTimerTarget;

/* One timer's whole running state. */
typedef struct TaymTimerState {
    uint8_t active;
    uint8_t quiescent; /* S10.2: owns its targets but fires no more expiries */
    uint8_t armed;     /* S12.2: next expiry writes element 0 as-is */
    uint8_t has_tlan;

    TaymTimerTarget targets[TAYM_MAX_TIMER_TARGETS];
    size_t target_count;

    uint32_t tlan_index;
    uint32_t tlan_step;

    double base_interval; /* chip cycles between expiries; 0 = no rate */
    double next_expiry;   /* absolute chip cycle of the next expiry */
} TaymTimerState;

/*
 * The engine's whole state for one chip. Plain data: copy it to snapshot, zero
 * it to reset. Hold one per chip.
 */
typedef struct TaymTimerBank {
    TaymTimerTables tables;
    uint32_t clock_hz;

    TaymTimerClock clocks[TAYM_MAX_TIMERS];
    TaymTimerState timers[TAYM_MAX_TIMERS];
    size_t timer_count;

    /* How long a timer whose rate is zero waits before it fires again: one
       frame, in chip cycles. The engine has no frames of its own, so the
       caller sets this -- a file walker per frame, a live host once. While it
       is 0 such a timer fires once per taym_timers_collect() call, which
       depends on how the caller cuts time. */
    uint64_t no_rate_interval;
} TaymTimerBank;

/*
 * Prepare a bank for `timer_count` timers on a chip at `clock_hz`. Zeroes all
 * timer state (every timer stopped, as S12 requires before frame 0) and borrows
 * `tables`. `clocks` is copied. Returns TAYM_ERROR_ARGUMENT if timer_count
 * exceeds TAYM_MAX_TIMERS or any pointer needed by the tables is missing.
 */
TaymResult taym_timers_init(TaymTimerBank *bank,
                            const TaymTimerTables *tables,
                            const TaymTimerClock *clocks,
                            size_t timer_count,
                            uint32_t clock_hz);

/* Stop every timer and release all ownership, without touching the tables. */
void taym_timers_reset(TaymTimerBank *bank);

/*
 * Convenience: fill a bank from a parsed Taym for one chip index, taking the
 * tables, the chip's clock, and the TIMR records belonging to it.
 * `timer_map`, if given, receives the global timer index of each bank slot, so
 * the caller can find the right MODS column; it must hold TAYM_MAX_TIMERS
 * entries. Returns the number of timers taken in `out_count`.
 */
TaymResult taym_timers_init_from_taym(TaymTimerBank *bank,
                                      const Taym *taym,
                                      size_t chip_index,
                                      size_t *timer_map,
                                      size_t *out_count);

/*
 * Apply one MODS record to one timer at absolute `cycle` (S12). A START arms
 * the timer with its first expiry at `cycle`; MODULATE patches running state
 * and writes nothing; STOP releases ownership; EMPTY does nothing.
 *
 * Call this for every timer of a frame before pulling that frame's writes, in
 * the order the caller chooses: S13.2's handoff rules make a frame's commands
 * independent of timer index, and this applies one record at a time.
 *
 * Returns TAYM_ERROR_RANGE if the record references a lane, action or pool
 * element outside the tables, or claims more targets than
 * TAYM_MAX_TIMER_TARGETS. Invalid-but-defensible cases the spec forbids (a
 * MODULATE on an inactive timer) are ignored rather than rejected, matching
 * the oracle.
 */
TaymResult taym_timers_command(TaymTimerBank *bank,
                               size_t timer,
                               const TaymMods *mods,
                               uint64_t cycle);

/*
 * Whether any active or quiescent timer owns `target_id`, i.e. the caller must
 * not write that target from its own background (S13.1 step 3).
 */
int taym_timers_owns_target(const TaymTimerBank *bank, uint8_t target_id);

/*
 * Collect every timer write strictly before `end_cycle`, in cycle order, into
 * `out` (capacity `capacity`), and advance the bank past them. Each expiry
 * reports every target its timer owns, plus its sample amplitude (0x80) if it
 * carries one, which is never exclusively owned (S11.1).
 *
 * Returns the number written in `out_count`. An expiry is atomic and advances
 * its lanes as it fires, so it is never split: when fewer than
 * TAYM_MAX_TIMER_TARGETS slots are left the call stops short and returns
 * TAYM_ERROR_RANGE, and calling again with the same `end_cycle` resumes
 * without losing or doubling a write. `capacity` must therefore be at least
 * TAYM_MAX_TIMER_TARGETS. A timer with no rate waits bank->no_rate_interval
 * between expiries.
 */
TaymResult taym_timers_collect(TaymTimerBank *bank,
                               uint64_t end_cycle,
                               TaymTimerWrite *out,
                               size_t capacity,
                               size_t *out_count);

#ifdef __cplusplus
}
#endif

#endif
