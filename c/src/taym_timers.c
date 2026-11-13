/*
 * TAYM timer engine (S9..S13). Read this next to python/src/taym/engine/engine.py:
 * the two are deliberately the same algorithm, and the Python one is the oracle.
 */

#include "taym/taym_timers.h"

#include <math.h>
#include <string.h>

/* AY/YM 16-step volume DAC curves (appendix A.3), used only by the 0x80
   sample-amplitude combine. Both chips pick the volume level from a 4-bit
   curve; the YM's is closer to a true exponential. The YM's 32-step table is
   the *envelope* curve, not this one. */
static const double taym_ay_dac[16] = {
    0.0,
    0.00999465934234,
    0.0144502937362,
    0.0210574502174,
    0.0307011520562,
    0.0455481803616,
    0.0644998855573,
    0.107362478065,
    0.126588845655,
    0.20498970016,
    0.292210269322,
    0.372838941024,
    0.492530708782,
    0.635324635691,
    0.805584802014,
    1.0
};

static const double taym_ym_dac[16] = {
    0.0,
    0.00772106507973,
    0.0139620050355,
    0.0200198367285,
    0.029694056611,
    0.0403906309606,
    0.0583352407111,
    0.0777752346075,
    0.111085679408,
    0.148485542077,
    0.211551079576,
    0.281101701381,
    0.400427252613,
    0.53443198291,
    0.75800717174,
    1.0
};

/* AY R8/R9/R10: the volume register a 0x80 amplitude combines into (S11.1). */
#define TAYM_TGT_SAMPLE_AMPLITUDE 0x80u

static int taym_is_amp_reg(uint8_t target_id)
{
    return target_id == 0x08u || target_id == 0x09u || target_id == 0x0Au;
}

static const double *taym_dac_table(uint8_t variant)
{
    return variant == TAYM_AY_VARIANT_YM ? taym_ym_dac : taym_ay_dac;
}

uint8_t taym_timers_combine_amplitude(uint8_t variant,
                                      uint8_t volume_code,
                                      uint32_t amplitude,
                                      uint32_t full_scale)
{
    const double *table = taym_dac_table(variant);
    double linear;
    double best_distance;
    unsigned best_code;
    unsigned code;

    linear = table[volume_code & 0x0Fu];
    if (full_scale != 0u) {
        linear *= (double) amplitude / (double) full_scale;
    } else {
        linear = 0.0;
    }

    best_code = 0u;
    best_distance = fabs(table[0] - linear);
    for (code = 0u; code < 16u; code++) {
        const double distance = fabs(table[code] - linear);
        /* <= so a tie prefers the higher code, as the oracle does. */
        if (distance <= best_distance) {
            best_code = code;
            best_distance = distance;
        }
    }
    return (uint8_t) best_code;
}

/* ------------------------------------------------------------------------- */
/* Lanes                                                                      */
/* ------------------------------------------------------------------------- */

/* One lane element, widened to u32. The pool follows value_type (S9). */
static TaymResult taym_lane_value(const TaymTimerBank *bank,
                                  const TaymLane *lane,
                                  uint32_t step,
                                  uint32_t *out_value)
{
    const TaymTimerTables *t = &bank->tables;
    const size_t index = (size_t) lane->value_offset + (size_t) step;

    switch (lane->value_type) {
        case TAYM_VT_U8:
            if (t->vu08 == NULL || index >= t->vu08_count) {
                return TAYM_ERROR_RANGE;
            }
            *out_value = t->vu08[index];
            return TAYM_OK;
        case TAYM_VT_U16:
            if (t->vu16 == NULL || index >= t->vu16_count) {
                return TAYM_ERROR_RANGE;
            }
            *out_value = t->vu16[index];
            return TAYM_OK;
        case TAYM_VT_U32:
            if (t->vu32 == NULL || index >= t->vu32_count) {
                return TAYM_ERROR_RANGE;
            }
            *out_value = t->vu32[index];
            return TAYM_OK;
        default:
            return TAYM_ERROR_FORMAT;
    }
}

static uint32_t taym_lane_full_scale(uint8_t value_type)
{
    switch (value_type) {
        case TAYM_VT_U16:
            return 0xFFFFu;
        case TAYM_VT_U32:
            return 0xFFFFFFFFu;
        case TAYM_VT_U8:
        default:
            return 0xFFu;
    }
}

/*
 * Step a lane index honoring loop and dormancy (S9.1). A looping lane wraps to
 * loop_index; a no-loop lane sticks on its final element, which is dormancy --
 * it keeps its value and its ownership. The oracle rewrites the final value
 * each expiry rather than suppressing the write, and so do we: suppressing it
 * is only observable on a write-sensitive target, and the two must agree.
 */
static uint32_t taym_lane_advance(uint32_t step, uint32_t length, uint32_t loop_index)
{
    const uint32_t next = step + 1u;
    if (next < length) {
        return next;
    }
    if (loop_index == TAYM_NO_LOOP) {
        return length > 0u ? length - 1u : 0u;
    }
    return loop_index;
}

/* ------------------------------------------------------------------------- */
/* Timing                                                                     */
/* ------------------------------------------------------------------------- */

/*
 * One timer value as an interval in chip cycles (S10), or 0 when it gives no
 * rate. CHIP_PERIOD is exact: divider * period cycles. ABS_RATE_HZ is
 * clock / rate, where rate is unsigned 16.16 Hz -- fractional, and the only
 * place rounding matters.
 */
static double taym_period_cycles(const TaymTimerClock *clock,
                                 uint32_t value,
                                 uint32_t clock_hz)
{
    if (clock->clock_mode == TAYM_CLOCK_ABS_RATE_HZ) {
        const double hz = taym_from_fix16(value);
        return hz > 0.0 ? (double) clock_hz / hz : 0.0;
    }
    return (double) clock->clock_divider * (double) value;
}

/* The effective interval from the persistent base plus the active timer lane
   (S10): no lane uses the base, ABSOLUTE overrides it, RELATIVE multiplies the
   rate and so divides the interval. */
static double taym_timer_interval(const TaymTimerBank *bank,
                                  size_t timer)
{
    const TaymTimerState *st = &bank->timers[timer];
    const TaymTimerClock *clock = &bank->clocks[timer];
    const TaymTlan *tlan;
    uint32_t value;
    double multiplier;

    if (!st->has_tlan) {
        return st->base_interval;
    }
    tlan = &bank->tables.tlanes[st->tlan_index];
    if ((size_t) tlan->value_offset + (size_t) st->tlan_step >= bank->tables.vu32_count) {
        return st->base_interval;
    }
    value = bank->tables.vu32[tlan->value_offset + st->tlan_step];

    if (tlan->timing_mode == TAYM_TM_ABSOLUTE) {
        if (clock->clock_mode == TAYM_CLOCK_CHIP_PERIOD && value == 0u) {
            return st->base_interval;
        }
        return taym_period_cycles(clock, value, bank->clock_hz);
    }
    multiplier = taym_from_fix16(value);
    return multiplier > 0.0 ? st->base_interval / multiplier : 0.0;
}

/* ------------------------------------------------------------------------- */
/* Setup                                                                      */
/* ------------------------------------------------------------------------- */

static int taym_tables_ok(const TaymTimerTables *tables)
{
    if (tables == NULL) {
        return 0;
    }
    if (tables->action_count > 0u && tables->actions == NULL) {
        return 0;
    }
    if (tables->lane_count > 0u && tables->lanes == NULL) {
        return 0;
    }
    if (tables->tlane_count > 0u && tables->tlanes == NULL) {
        return 0;
    }
    if (tables->vu08_count > 0u && tables->vu08 == NULL) {
        return 0;
    }
    if (tables->vu16_count > 0u && tables->vu16 == NULL) {
        return 0;
    }
    if (tables->vu32_count > 0u && tables->vu32 == NULL) {
        return 0;
    }
    return 1;
}

TaymResult taym_timers_init(TaymTimerBank *bank,
                            const TaymTimerTables *tables,
                            const TaymTimerClock *clocks,
                            size_t timer_count,
                            uint32_t clock_hz,
                            uint8_t variant)
{
    size_t i;

    if (bank == NULL || !taym_tables_ok(tables) || timer_count > TAYM_MAX_TIMERS) {
        return TAYM_ERROR_ARGUMENT;
    }
    if (timer_count > 0u && clocks == NULL) {
        return TAYM_ERROR_ARGUMENT;
    }

    memset(bank, 0, sizeof(*bank));
    bank->tables = *tables;
    bank->clock_hz = clock_hz != 0u ? clock_hz : 1u;
    bank->variant = variant;
    bank->timer_count = timer_count;
    for (i = 0u; i < timer_count; i++) {
        bank->clocks[i] = clocks[i];
    }
    return TAYM_OK;
}

void taym_timers_reset(TaymTimerBank *bank)
{
    size_t i;

    if (bank == NULL) {
        return;
    }
    for (i = 0u; i < TAYM_MAX_TIMERS; i++) {
        memset(&bank->timers[i], 0, sizeof(bank->timers[i]));
    }
}

TaymResult taym_timers_init_from_taym(TaymTimerBank *bank,
                                      const Taym *taym,
                                      size_t chip_index,
                                      size_t *timer_map,
                                      size_t *out_count)
{
    TaymTimerTables tables;
    TaymTimerClock clocks[TAYM_MAX_TIMERS];
    size_t count = 0u;
    size_t i;

    if (bank == NULL || taym == NULL || chip_index >= taym->chip_count) {
        return TAYM_ERROR_ARGUMENT;
    }

    memset(&tables, 0, sizeof(tables));
    tables.actions = taym->actions;
    tables.action_count = taym->action_count;
    tables.lanes = taym->lanes;
    tables.lane_count = taym->lane_count;
    tables.tlanes = taym->tlanes;
    tables.tlane_count = taym->tlane_count;
    tables.vu08 = taym->vu08;
    tables.vu08_count = taym->vu08_count;
    tables.vu16 = taym->vu16;
    tables.vu16_count = taym->vu16_count;
    tables.vu32 = taym->vu32;
    tables.vu32_count = taym->vu32_count;

    for (i = 0u; i < taym->timer_count; i++) {
        if (taym->timers[i].chip_index != (uint8_t) chip_index) {
            continue;
        }
        if (count >= TAYM_MAX_TIMERS) {
            return TAYM_ERROR_RANGE;
        }
        clocks[count].clock_divider = taym->timers[i].clock_divider;
        clocks[count].clock_mode = taym->timers[i].clock_mode;
        if (timer_map != NULL) {
            timer_map[count] = i;
        }
        count++;
    }

    if (out_count != NULL) {
        *out_count = count;
    }
    return taym_timers_init(bank, &tables, clocks, count,
                            taym->chips[chip_index].clock_hz,
                            taym->chips[chip_index].variant);
}

/* ------------------------------------------------------------------------- */
/* Commands (S12)                                                             */
/* ------------------------------------------------------------------------- */

/* Check an action slice and the lanes it binds before touching timer state, so
   a rejected command leaves the timer as it was. */
static TaymResult taym_check_actions(const TaymTimerBank *bank,
                                     uint32_t first_action,
                                     uint8_t action_count)
{
    const TaymTimerTables *t = &bank->tables;
    size_t i;

    if (action_count > TAYM_MAX_TIMER_TARGETS) {
        return TAYM_ERROR_RANGE;
    }
    if ((size_t) first_action + (size_t) action_count > t->action_count) {
        return TAYM_ERROR_RANGE;
    }
    for (i = 0u; i < (size_t) action_count; i++) {
        const TaymActn *a = &t->actions[first_action + i];
        if (a->source_mode == TAYM_SRC_BIND_LANE) {
            const TaymLane *lane;
            uint32_t probe;
            TaymResult r;
            if (a->operand >= t->lane_count) {
                return TAYM_ERROR_RANGE;
            }
            lane = &t->lanes[a->operand];
            if (lane->length == 0u) {
                return TAYM_ERROR_FORMAT;
            }
            if (lane->loop_index != TAYM_NO_LOOP && lane->loop_index >= lane->length) {
                return TAYM_ERROR_FORMAT;
            }
            /* The last element too: a lane whose slice runs off its pool must
               be caught here, not mid-render. */
            r = taym_lane_value(bank, lane, lane->length - 1u, &probe);
            if (r != TAYM_OK) {
                return r;
            }
        }
    }
    return TAYM_OK;
}

static TaymResult taym_check_tlan(const TaymTimerBank *bank, uint32_t ref)
{
    const TaymTlan *tlan;

    if (ref == TAYM_TLAN_NONE || ref == TAYM_TLAN_UNCHANGED) {
        return TAYM_OK;
    }
    if (ref >= bank->tables.tlane_count) {
        return TAYM_ERROR_RANGE;
    }
    tlan = &bank->tables.tlanes[ref];
    if (tlan->length == 0u) {
        return TAYM_ERROR_FORMAT;
    }
    if (tlan->loop_index != TAYM_NO_LOOP && tlan->loop_index >= tlan->length) {
        return TAYM_ERROR_FORMAT;
    }
    if ((size_t) tlan->value_offset + (size_t) tlan->length > bank->tables.vu32_count) {
        return TAYM_ERROR_RANGE;
    }
    return TAYM_OK;
}

/* S12.2: START replaces the whole state, resets every lane to 0, and arms the
   timer so its first expiry -- at this very cycle -- writes element 0 without
   advancing. */
static void taym_apply_start(TaymTimerBank *bank,
                             size_t timer,
                             const TaymMods *mods,
                             uint64_t cycle)
{
    TaymTimerState *st = &bank->timers[timer];
    size_t i;

    memset(st, 0, sizeof(*st));
    st->active = 1u;
    st->base_interval = taym_period_cycles(&bank->clocks[timer],
                                           mods->base_timer_value,
                                           bank->clock_hz);
    if (mods->timer_lane_ref != TAYM_TLAN_NONE
        && mods->timer_lane_ref != TAYM_TLAN_UNCHANGED) {
        st->has_tlan = 1u;
        st->tlan_index = mods->timer_lane_ref;
    }
    st->tlan_step = 0u;

    for (i = 0u; i < (size_t) mods->action_count; i++) {
        const TaymActn *a = &bank->tables.actions[mods->first_action + i];
        TaymTimerTarget *target = &st->targets[i];
        target->target_id = a->target_id;
        if (a->source_mode == TAYM_SRC_BIND_LANE) {
            target->has_lane = 1u;
            target->lane_index = a->operand;
        } else {
            target->inline_value = a->operand;
        }
        target->lane_step = 0u;
        target->lane_pending = 0u;
    }
    st->target_count = (size_t) mods->action_count;

    st->armed = 1u;
    st->next_expiry = (double) cycle;
}

/* S12.3: MODULATE patches running state, writes nothing, and never retriggers.
   It cannot add or remove targets -- only replace the source of one it owns. */
static void taym_apply_modulate(TaymTimerBank *bank,
                                size_t timer,
                                const TaymMods *mods)
{
    TaymTimerState *st = &bank->timers[timer];
    size_t i;

    /* The spec forbids MODULATE on an inactive or quiescent timer; be
       defensive and ignore it, as the oracle does. */
    if (!st->active || st->quiescent) {
        return;
    }

    if (mods->base_timer_value != 0u) {
        st->base_interval = taym_period_cycles(&bank->clocks[timer],
                                               mods->base_timer_value,
                                               bank->clock_hz);
    }
    if (mods->timer_lane_ref == TAYM_TLAN_NONE) {
        st->has_tlan = 0u;
    } else if (mods->timer_lane_ref != TAYM_TLAN_UNCHANGED) {
        /* Phase is preserved: tlan_step stays. The validator guarantees the
           replacement has the same length and loop_index. */
        st->has_tlan = 1u;
        st->tlan_index = mods->timer_lane_ref;
    }

    for (i = 0u; i < (size_t) mods->action_count; i++) {
        const TaymActn *a = &bank->tables.actions[mods->first_action + i];
        size_t j;
        for (j = 0u; j < st->target_count; j++) {
            TaymTimerTarget *target = &st->targets[j];
            if (target->target_id != a->target_id) {
                continue;
            }
            if (a->source_mode == TAYM_SRC_BIND_LANE) {
                if (!target->has_lane) {
                    /* A lane replacing an inline source starts at element 0,
                       and that element is pending: the next expiry writes it
                       without advancing first. */
                    target->lane_step = 0u;
                    target->lane_pending = 1u;
                }
                /* A lane replacing an active lane keeps its index and advances
                   normally, so nothing is pending. */
                target->has_lane = 1u;
                target->lane_index = a->operand;
            } else {
                target->has_lane = 0u;
                target->lane_pending = 0u;
                target->inline_value = a->operand;
            }
            break;
        }
    }
}

TaymResult taym_timers_command(TaymTimerBank *bank,
                               size_t timer,
                               const TaymMods *mods,
                               uint64_t cycle)
{
    TaymResult result;

    if (bank == NULL || mods == NULL || timer >= bank->timer_count) {
        return TAYM_ERROR_ARGUMENT;
    }

    switch (mods->command) {
        case TAYM_CMD_EMPTY:
            return TAYM_OK;

        case TAYM_CMD_STOP:
            memset(&bank->timers[timer], 0, sizeof(bank->timers[timer]));
            return TAYM_OK;

        case TAYM_CMD_START:
            result = taym_check_actions(bank, mods->first_action, mods->action_count);
            if (result != TAYM_OK) {
                return result;
            }
            result = taym_check_tlan(bank, mods->timer_lane_ref);
            if (result != TAYM_OK) {
                return result;
            }
            taym_apply_start(bank, timer, mods, cycle);
            return TAYM_OK;

        case TAYM_CMD_MODULATE:
            result = taym_check_actions(bank, mods->first_action, mods->action_count);
            if (result != TAYM_OK) {
                return result;
            }
            result = taym_check_tlan(bank, mods->timer_lane_ref);
            if (result != TAYM_OK) {
                return result;
            }
            taym_apply_modulate(bank, timer, mods);
            return TAYM_OK;

        default:
            return TAYM_ERROR_FORMAT;
    }
}

/* ------------------------------------------------------------------------- */
/* Ownership (S13.1)                                                          */
/* ------------------------------------------------------------------------- */

int taym_timers_owns_target(const TaymTimerBank *bank, uint8_t target_id)
{
    size_t i;
    size_t j;

    if (bank == NULL) {
        return 0;
    }
    for (i = 0u; i < bank->timer_count; i++) {
        const TaymTimerState *st = &bank->timers[i];
        if (!st->active && !st->quiescent) {
            continue;
        }
        for (j = 0u; j < st->target_count; j++) {
            if (st->targets[j].target_id == target_id) {
                return 1;
            }
        }
    }
    return 0;
}

/* ------------------------------------------------------------------------- */
/* Expiries (S10.1, S13)                                                      */
/* ------------------------------------------------------------------------- */

/* Which timer owns `target_id`, or `count` if none does. Mirrors the oracle's
   per-frame `owned` map: at a handoff two timers can both list a target, and
   only the owner writes it. */
static size_t taym_owner_of(const TaymTimerBank *bank, uint8_t target_id)
{
    size_t i;
    size_t j;

    for (i = 0u; i < bank->timer_count; i++) {
        const TaymTimerState *st = &bank->timers[i];
        if (!st->active && !st->quiescent) {
            continue;
        }
        for (j = 0u; j < st->target_count; j++) {
            if (st->targets[j].target_id == target_id) {
                return i;
            }
        }
    }
    return bank->timer_count;
}

/* The current value of one target's source. */
static TaymResult taym_target_value(const TaymTimerBank *bank,
                                    const TaymTimerTarget *target,
                                    uint32_t *out_value,
                                    uint32_t *out_full_scale)
{
    if (!target->has_lane) {
        *out_value = target->inline_value;
        if (out_full_scale != NULL) {
            *out_full_scale = 0xFFu; /* an inline operand is byte-wide here */
        }
        return TAYM_OK;
    }
    if (out_full_scale != NULL) {
        *out_full_scale = taym_lane_full_scale(bank->tables.lanes[target->lane_index].value_type);
    }
    return taym_lane_value(bank, &bank->tables.lanes[target->lane_index],
                           target->lane_step, out_value);
}

/*
 * One atomic expiry (S10.1): advance every lane, then write the owned targets.
 * `armed` makes the post-START expiry write element 0 as-is.
 */
static TaymResult taym_fire_expiry(TaymTimerBank *bank,
                                   size_t timer,
                                   uint64_t cycle,
                                   TaymTimerWrite *out,
                                   size_t capacity,
                                   size_t *out_count)
{
    TaymTimerState *st = &bank->timers[timer];
    size_t i;
    int has_amplitude = 0;
    uint32_t amplitude = 0u;
    uint32_t amplitude_scale = 0xFFu;
    size_t produced = 0u;

    if (st->armed) {
        st->armed = 0u;
    } else {
        /* The timer lane first: S10.2 says a no-loop one runs its final
           interval and then makes the timer quiescent, and that final boundary
           performs no lane advance and no target write. So leave the value
           lanes alone and write nothing; the timer keeps its targets and its
           final values until a STOP. */
        if (st->has_tlan) {
            const TaymTlan *tlan = &bank->tables.tlanes[st->tlan_index];
            if (st->tlan_step + 1u >= tlan->length && tlan->loop_index == TAYM_NO_LOOP) {
                st->quiescent = 1u;
                *out_count = 0u;
                return TAYM_OK;
            }
            st->tlan_step = taym_lane_advance(st->tlan_step, tlan->length, tlan->loop_index);
        }
        for (i = 0u; i < st->target_count; i++) {
            TaymTimerTarget *target = &st->targets[i];
            const TaymLane *lane;
            if (!target->has_lane) {
                continue;
            }
            if (target->lane_pending) {
                /* S12.3: write element 0 without advancing. */
                target->lane_pending = 0u;
                continue;
            }
            lane = &bank->tables.lanes[target->lane_index];
            target->lane_step = taym_lane_advance(target->lane_step, lane->length,
                                                  lane->loop_index);
        }
    }

    /* S11.1: if this timer drives 0x80, take its unquantized value first so
       the paired volume register's write can combine it. */
    for (i = 0u; i < st->target_count; i++) {
        const TaymTimerTarget *target = &st->targets[i];
        if (target->target_id != TAYM_TGT_SAMPLE_AMPLITUDE) {
            continue;
        }
        if (taym_target_value(bank, target, &amplitude, &amplitude_scale) == TAYM_OK) {
            has_amplitude = 1;
        }
    }

    for (i = 0u; i < st->target_count; i++) {
        const TaymTimerTarget *target = &st->targets[i];
        uint32_t value;
        uint8_t byte;

        if (target->target_id == TAYM_TGT_SAMPLE_AMPLITUDE) {
            continue; /* virtual: reaches the chip through its volume register */
        }
        if (taym_owner_of(bank, target->target_id) != timer) {
            continue; /* another timer owns it this frame (handoff edge) */
        }
        if (target->target_id > TAYM_AY_TARGET_MAX) {
            continue; /* not a hardware register we can write */
        }
        if (taym_target_value(bank, target, &value, NULL) != TAYM_OK) {
            return TAYM_ERROR_RANGE;
        }
        byte = (uint8_t) (value & 0xFFu);

        if (produced >= capacity) {
            *out_count = produced;
            return TAYM_ERROR_RANGE;
        }
        if (target->target_id == TAYM_AY_R13_SHAPE) {
            byte = (uint8_t) (byte & 0x0Fu); /* every R13 write retriggers */
        } else if (taym_is_amp_reg(target->target_id) && has_amplitude) {
            byte = taym_timers_combine_amplitude(bank->variant, byte,
                                                 amplitude, amplitude_scale);
        }
        out[produced].cycle = cycle;
        out[produced].reg = target->target_id;
        out[produced].value = byte;
        produced++;
    }

    *out_count = produced;
    return TAYM_OK;
}

TaymResult taym_timers_collect(TaymTimerBank *bank,
                               uint64_t end_cycle,
                               TaymTimerWrite *out,
                               size_t capacity,
                               size_t *out_count)
{
    size_t total = 0u;

    if (bank == NULL || out == NULL || out_count == NULL) {
        return TAYM_ERROR_ARGUMENT;
    }
    *out_count = 0u;

    for (;;) {
        size_t next = bank->timer_count;
        double next_cycle = 0.0;
        size_t i;
        size_t produced = 0u;
        double interval;
        TaymResult result;

        /* The earliest pending expiry across the bank's timers. */
        for (i = 0u; i < bank->timer_count; i++) {
            const TaymTimerState *st = &bank->timers[i];
            if (!st->active || st->quiescent) {
                continue;
            }
            if (st->next_expiry >= (double) end_cycle) {
                continue;
            }
            if (next == bank->timer_count || st->next_expiry < next_cycle) {
                next = i;
                next_cycle = st->next_expiry;
            }
        }
        if (next == bank->timer_count) {
            *out_count = total;
            return TAYM_OK;
        }

        /* An expiry is atomic (S10.1) and advances lanes as it fires, so it
           cannot be split across two calls without doubling a write. Stop
           before one that would not fit whole; the caller pulls again with the
           same end_cycle and gets it from the top. */
        if (capacity - total < TAYM_MAX_TIMER_TARGETS) {
            *out_count = total;
            return TAYM_ERROR_RANGE;
        }

        result = taym_fire_expiry(bank, next, (uint64_t) ceil(next_cycle),
                                  out + total, capacity - total, &produced);
        total += produced;
        if (result != TAYM_OK) {
            *out_count = total;
            return result;
        }

        interval = taym_timer_interval(bank, next);
        if (interval > 0.0) {
            /* Never faster than once per cycle, as the oracle clamps. */
            bank->timers[next].next_expiry += interval > 1.0 ? interval : 1.0;
        } else if (bank->no_rate_interval > 0u) {
            /* No rate: wait one frame from this expiry, as the oracle does, so
               the timer keeps its offset within the frame. */
            bank->timers[next].next_expiry += (double) bank->no_rate_interval;
        } else {
            /* No rate and no frame length given: once per pull, so the loop
               still ends. */
            bank->timers[next].next_expiry = (double) end_cycle;
        }
    }
}
