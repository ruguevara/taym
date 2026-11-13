/*
 * taym_timers and taym_ay unit checks: the rules the sample fixtures don't
 * reach, and the line between the chip-agnostic engine and the AY's rules.
 *
 * The cross-language diff in `make test` covers the main path (a PWM timer
 * against the Python oracle). These pin the edges the spec calls out and that
 * a fixture would not exercise: lane dormancy, timer-lane quiescence, the
 * MODULATE pending-element rule, ownership handoff, and the S11.1 combine.
 */

#include "taym/taym_ay.h"
#include "taym/taym_player.h"
#include "taym/taym_timers.h"

#include <stdio.h>
#include <string.h>

static int failures = 0;

static void check(int condition, const char *message)
{
    if (!condition) {
        fprintf(stderr, "FAIL: %s\n", message);
        failures++;
    }
}

static void check_eq(unsigned long got, unsigned long want, const char *message)
{
    if (got != want) {
        fprintf(stderr, "FAIL: %s (got %lu, want %lu)\n", message, got, want);
        failures++;
    }
}

/* --------------------------------------------------------------------------
 * A fixture: one CHIP_PERIOD timer on R8, driven by a two-element lane.
 * clock 1773400, divider 16, period 222 -> an expiry every 3552 cycles.
 * -------------------------------------------------------------------------- */
#define CLOCK 1773400u
#define DIVIDER 16u
#define PERIOD 222u
#define INTERVAL (DIVIDER * PERIOD)

static uint8_t pool_u8[4] = {15u, 9u, 3u, 0u};
static uint16_t pool_u16[2] = {0x1234u, 0xFFFFu};
static uint32_t pool_u32[4] = {0u, 0u, 0u, 0u};

static TaymActn actions[12];
static TaymLane lanes[4];
static TaymTlan tlanes[4];

static void build_tables(TaymTimerTables *tables)
{
    memset(tables, 0, sizeof(*tables));
    tables->actions = actions;
    tables->action_count = sizeof(actions) / sizeof(actions[0]);
    tables->lanes = lanes;
    tables->lane_count = sizeof(lanes) / sizeof(lanes[0]);
    tables->tlanes = tlanes;
    tables->tlane_count = sizeof(tlanes) / sizeof(tlanes[0]);
    tables->vu08 = pool_u8;
    tables->vu08_count = sizeof(pool_u8) / sizeof(pool_u8[0]);
    tables->vu16 = pool_u16;
    tables->vu16_count = sizeof(pool_u16) / sizeof(pool_u16[0]);
    tables->vu32 = pool_u32;
    tables->vu32_count = sizeof(pool_u32) / sizeof(pool_u32[0]);
}

/* action 0: R8 <- lane 0 ; action 1: R8 <- inline 7
   action 2: R9 <- lane 0 ; action 3: R8 <- lane 1
   lane 0: [15, 9] looping ; lane 1: [15, 9] no loop
   lane 2: [3, 0] looping (same shape as lane 0, for a phase-preserving swap) */
static void build_fixture(TaymTimerBank *bank, size_t timer_count)
{
    TaymTimerTables tables;
    TaymTimerClock clocks[2];
    size_t i;

    memset(actions, 0, sizeof(actions));
    actions[0].target_id = 8u;
    actions[0].source_mode = TAYM_SRC_BIND_LANE;
    actions[0].operand = 0u;
    actions[1].target_id = 8u;
    actions[1].source_mode = TAYM_SRC_INLINE_VALUE;
    actions[1].operand = 7u;
    actions[2].target_id = 9u;
    actions[2].source_mode = TAYM_SRC_BIND_LANE;
    actions[2].operand = 0u;
    actions[3].target_id = 8u;
    actions[3].source_mode = TAYM_SRC_BIND_LANE;
    actions[3].operand = 1u;
    actions[4].target_id = 8u;
    actions[4].source_mode = TAYM_SRC_BIND_LANE;
    actions[4].operand = 2u;

    memset(lanes, 0, sizeof(lanes));
    lanes[0].value_type = TAYM_VT_U8;
    lanes[0].value_offset = 0u;
    lanes[0].length = 2u;
    lanes[0].loop_index = 0u;
    lanes[1].value_type = TAYM_VT_U8;
    lanes[1].value_offset = 0u;
    lanes[1].length = 2u;
    lanes[1].loop_index = TAYM_NO_LOOP;
    lanes[2].value_type = TAYM_VT_U8;
    lanes[2].value_offset = 2u;
    lanes[2].length = 2u;
    lanes[2].loop_index = 0u;

    memset(tlanes, 0, sizeof(tlanes));
    /* tlane 0: two absolute CHIP_PERIOD intervals, no loop -> quiescence. */
    pool_u32[0] = PERIOD;
    pool_u32[1] = PERIOD;
    tlanes[0].timing_mode = TAYM_TM_ABSOLUTE;
    tlanes[0].value_offset = 0u;
    tlanes[0].length = 2u;
    tlanes[0].loop_index = TAYM_NO_LOOP;

    build_tables(&tables);
    for (i = 0u; i < 2u; i++) {
        clocks[i].clock_divider = (uint16_t) DIVIDER;
        clocks[i].clock_mode = TAYM_CLOCK_CHIP_PERIOD;
    }
    check(taym_timers_init(bank, &tables, clocks, timer_count, CLOCK) == TAYM_OK, "init");
}

static TaymMods mods_start(uint32_t base, uint32_t tlan_ref, uint32_t first, uint8_t count)
{
    TaymMods m;
    memset(&m, 0, sizeof(m));
    m.command = TAYM_CMD_START;
    m.base_timer_value = base;
    m.timer_lane_ref = tlan_ref;
    m.first_action = first;
    m.action_count = count;
    return m;
}

static TaymMods mods_modulate(uint32_t base, uint32_t tlan_ref, uint32_t first, uint8_t count)
{
    TaymMods m = mods_start(base, tlan_ref, first, count);
    m.command = TAYM_CMD_MODULATE;
    return m;
}

static TaymMods mods_simple(uint8_t command)
{
    TaymMods m;
    memset(&m, 0, sizeof(m));
    m.command = command;
    return m;
}

/* --------------------------------------------------------------------------
 * Tests
 * -------------------------------------------------------------------------- */

/* S12.2: the first expiry sits at the START cycle and writes element 0
   without advancing; the next is one interval later with element 1. */
static void test_armed_first_write(void)
{
    TaymTimerBank bank;
    TaymTimerWrite writes[32];
    TaymMods m;
    size_t count = 0u;

    build_fixture(&bank, 1u);
    m = mods_start(PERIOD, TAYM_TLAN_NONE, 0u, 1u);
    check(taym_timers_command(&bank, 0u, &m, 1000u) == TAYM_OK, "start");

    check(taym_timers_collect(&bank, 1000u + 2u * INTERVAL, writes, 32u, &count) == TAYM_OK,
          "collect armed");
    check_eq(count, 2u, "armed: two expiries in two intervals");
    check_eq((unsigned long) writes[0].cycle, 1000u, "armed: first at the START cycle");
    check_eq(writes[0].value, 15u, "armed: first writes element 0");
    check_eq((unsigned long) writes[1].cycle, 1000u + INTERVAL, "armed: second one interval on");
    check_eq(writes[1].value, 9u, "armed: second writes element 1");
}

/* Invariant 1: expiries are exact multiples of the interval from START and
   carry across however the caller cuts the timeline. */
static void test_continuous_expiry_clock(void)
{
    TaymTimerBank bank;
    TaymTimerWrite writes[64];
    TaymMods m;
    size_t count = 0u;
    size_t total = 0u;
    uint64_t cut;

    build_fixture(&bank, 1u);
    m = mods_start(PERIOD, TAYM_TLAN_NONE, 0u, 1u);
    check(taym_timers_command(&bank, 0u, &m, 0u) == TAYM_OK, "start");

    /* Pull in ragged chunks that do not line up with the interval. */
    for (cut = 1000u; cut <= 40000u; cut += 1000u) {
        check(taym_timers_collect(&bank, cut, writes + total, 64u - total, &count) == TAYM_OK,
              "collect chunk");
        total += count;
    }
    check_eq(total, 40000u / INTERVAL + 1u, "continuous: expiry count");
    {
        size_t i;
        int ok = 1;
        for (i = 0u; i < total; i++) {
            if (writes[i].cycle != (uint64_t) i * INTERVAL) {
                ok = 0;
            }
            if (writes[i].value != ((i % 2u) == 0u ? 15u : 9u)) {
                ok = 0;
            }
        }
        check(ok, "continuous: exact interval multiples, alternating lane");
    }
}

/* S9.1: a no-loop value lane writes its last value once and then stays on it
   (dormant). It keeps writing that value, which is what the oracle does. */
static void test_value_lane_dormancy(void)
{
    TaymTimerBank bank;
    TaymTimerWrite writes[32];
    TaymMods m;
    size_t count = 0u;

    build_fixture(&bank, 1u);
    /* action 3: R8 <- lane 1, the no-loop [15, 9]. */
    m = mods_start(PERIOD, TAYM_TLAN_NONE, 3u, 1u);
    check(taym_timers_command(&bank, 0u, &m, 0u) == TAYM_OK, "start");

    check(taym_timers_collect(&bank, 4u * INTERVAL, writes, 32u, &count) == TAYM_OK,
          "collect dormant");
    check_eq(count, 4u, "dormancy: four expiries");
    check_eq(writes[0].value, 15u, "dormancy: element 0");
    check_eq(writes[1].value, 9u, "dormancy: element 1");
    check_eq(writes[2].value, 9u, "dormancy: sticks on the final element");
    check_eq(writes[3].value, 9u, "dormancy: still the final element");
}

/* S10.2: a no-loop timer lane runs its final interval and then makes the timer
   quiescent -- no more expiries, but it still owns its targets until a STOP. */
static void test_timer_lane_quiescence(void)
{
    TaymTimerBank bank;
    TaymTimerWrite writes[32];
    TaymMods m;
    size_t count = 0u;

    build_fixture(&bank, 1u);
    m = mods_start(PERIOD, 0u, 0u, 1u); /* tlane 0: two intervals, no loop */
    check(taym_timers_command(&bank, 0u, &m, 0u) == TAYM_OK, "start");

    check(taym_timers_collect(&bank, 20u * INTERVAL, writes, 32u, &count) == TAYM_OK,
          "collect quiescent");
    /* The tlane's two elements give two expiries; its final boundary then makes
       the timer quiescent, performing no advance and no write. Asked for 20
       intervals' worth, we must still get exactly 2 -- a timer that keeps
       firing on a dormant final element (the bug this pins) gives 20. */
    check_eq(count, 2u, "quiescence: exactly the tlane's two expiries");
    check_eq(writes[0].value, 15u, "quiescence: element 0");
    check_eq(writes[1].value, 9u, "quiescence: element 1");
    check(bank.timers[0].quiescent, "quiescence: the flag is set");
    check(taym_timers_owns_target(&bank, 8u), "quiescence: still owns R8");

    /* Quiescent is not stopped, and a further pull adds nothing. */
    check(taym_timers_collect(&bank, 40u * INTERVAL, writes, 32u, &count) == TAYM_OK,
          "collect past quiescence");
    check_eq(count, 0u, "quiescence: no further expiries");
    check(taym_timers_owns_target(&bank, 8u), "quiescence: ownership survives");

    m = mods_simple(TAYM_CMD_STOP);
    check(taym_timers_command(&bank, 0u, &m, 20u * INTERVAL) == TAYM_OK, "stop");
    check(!taym_timers_owns_target(&bank, 8u), "stop: releases R8");
}

/* S10.2's other half: looping on the final timer element explicitly means to
   continue indefinitely. The contrast with quiescence above is the whole rule. */
static void test_timer_lane_looping_runs_on(void)
{
    TaymTimerBank bank;
    TaymTimerWrite writes[64];
    TaymMods m;
    size_t count = 0u;

    build_fixture(&bank, 1u);
    tlanes[0].loop_index = 0u; /* same lane, now looping */
    m = mods_start(PERIOD, 0u, 0u, 1u);
    check(taym_timers_command(&bank, 0u, &m, 0u) == TAYM_OK, "start looping tlane");

    check(taym_timers_collect(&bank, 20u * INTERVAL, writes, 64u, &count) == TAYM_OK,
          "collect looping");
    check_eq(count, 20u, "looping tlane: keeps firing");
    check(!bank.timers[0].quiescent, "looping tlane: never quiescent");

    tlanes[0].loop_index = TAYM_NO_LOOP; /* restore for any later test */
}

/* A timer whose rate drops to zero waits one frame from its last expiry, so it
   keeps its offset within the frame however the caller cuts time. */
static void test_no_rate_waits_one_frame(void)
{
    const uint64_t frame = 35468u;
    TaymTimerBank bank;
    TaymTimerWrite writes[32];
    TaymMods m;
    size_t count = 0u;
    size_t total = 0u;
    uint64_t cut;

    build_fixture(&bank, 1u);
    /* tlane 1: RELATIVE [x1, x0] looping on the zero. */
    pool_u32[2] = 65536u;
    pool_u32[3] = 0u;
    tlanes[1].timing_mode = TAYM_TM_RELATIVE;
    tlanes[1].value_offset = 2u;
    tlanes[1].length = 2u;
    tlanes[1].loop_index = 1u;
    bank.no_rate_interval = frame;

    m = mods_start(PERIOD, 1u, 0u, 1u);
    check(taym_timers_command(&bank, 0u, &m, 0u) == TAYM_OK, "start");

    /* Pulls that line up with nothing: the result must not depend on them. */
    for (cut = 5000u; cut <= 3u * frame; cut += 5000u) {
        check(taym_timers_collect(&bank, cut, writes + total, 32u - total, &count) == TAYM_OK,
              "collect no-rate");
        total += count;
    }
    check_eq(total, 4u, "no rate: one expiry per frame after the first interval");
    check_eq((unsigned long) writes[0].cycle, 0u, "no rate: START");
    check_eq((unsigned long) writes[1].cycle, INTERVAL, "no rate: the one real interval");
    check_eq((unsigned long) writes[2].cycle, INTERVAL + frame, "no rate: a frame later");
    check_eq((unsigned long) writes[3].cycle, INTERVAL + 2u * frame, "no rate: and another");
}

/* S12.3: a lane replacing an inline source starts at element 0 and that
   element is pending -- the next expiry writes it without advancing. */
static void test_modulate_pending_element(void)
{
    TaymTimerBank bank;
    TaymTimerWrite writes[32];
    TaymMods m;
    size_t count = 0u;

    build_fixture(&bank, 1u);
    /* Start with the inline source (action 1: R8 <- 7). */
    m = mods_start(PERIOD, TAYM_TLAN_NONE, 1u, 1u);
    check(taym_timers_command(&bank, 0u, &m, 0u) == TAYM_OK, "start inline");
    check(taym_timers_collect(&bank, INTERVAL, writes, 32u, &count) == TAYM_OK, "collect inline");
    check_eq(count, 1u, "pending: one expiry before the modulate");
    check_eq(writes[0].value, 7u, "pending: inline value");

    /* MODULATE to lane 0 ([15, 9]): writes nothing now, and the next expiry
       writes element 0, not element 1. */
    m = mods_modulate(0u, TAYM_TLAN_UNCHANGED, 0u, 1u);
    check(taym_timers_command(&bank, 0u, &m, INTERVAL) == TAYM_OK, "modulate to lane");

    check(taym_timers_collect(&bank, 3u * INTERVAL, writes, 32u, &count) == TAYM_OK,
          "collect after modulate");
    check_eq(count, 2u, "pending: two more expiries");
    check_eq(writes[0].value, 15u, "pending: element 0 without advancing");
    check_eq(writes[1].value, 9u, "pending: then element 1");
}

/* S12.3: a lane replacing an *active* lane preserves phase -- the running
   index carries over, and the next expiry advances normally. */
static void test_modulate_preserves_phase(void)
{
    TaymTimerBank bank;
    TaymTimerWrite writes[32];
    TaymMods m;
    size_t count = 0u;

    build_fixture(&bank, 1u);
    m = mods_start(PERIOD, TAYM_TLAN_NONE, 0u, 1u);   /* lane 0: [15, 9] */
    check(taym_timers_command(&bank, 0u, &m, 0u) == TAYM_OK, "start");
    check(taym_timers_collect(&bank, INTERVAL, writes, 32u, &count) == TAYM_OK, "collect");
    check_eq(writes[0].value, 15u, "phase: at element 0");

    /* Swap to lane 2 ([3, 0]), same length and loop. Index 0 is preserved, so
       the next expiry advances to element 1 of the NEW lane: 0. */
    m = mods_modulate(0u, TAYM_TLAN_UNCHANGED, 4u, 1u);
    check(taym_timers_command(&bank, 0u, &m, INTERVAL) == TAYM_OK, "modulate swap");
    check(taym_timers_collect(&bank, 2u * INTERVAL, writes, 32u, &count) == TAYM_OK,
          "collect swapped");
    check_eq(count, 1u, "phase: one expiry");
    check_eq(writes[0].value, 0u, "phase: advanced into the new lane, not restarted");
}

/* S13.2: one timer may release a target while another acquires it in the same
   frame. Only the owner writes it. */
static void test_ownership_handoff(void)
{
    TaymTimerBank bank;
    TaymTimerWrite writes[32];
    TaymMods m;
    size_t count = 0u;

    build_fixture(&bank, 2u);
    m = mods_start(PERIOD, TAYM_TLAN_NONE, 0u, 1u);
    check(taym_timers_command(&bank, 0u, &m, 0u) == TAYM_OK, "start timer 0");
    check(taym_timers_owns_target(&bank, 8u), "handoff: timer 0 owns R8");

    /* Timer 0 stops, timer 1 starts on the same target in the same frame. */
    m = mods_simple(TAYM_CMD_STOP);
    check(taym_timers_command(&bank, 0u, &m, INTERVAL) == TAYM_OK, "stop timer 0");
    m = mods_start(PERIOD, TAYM_TLAN_NONE, 1u, 1u); /* inline 7 */
    check(taym_timers_command(&bank, 1u, &m, INTERVAL) == TAYM_OK, "start timer 1");

    check(taym_timers_collect(&bank, INTERVAL + 1u, writes, 32u, &count) == TAYM_OK,
          "collect handoff");
    check_eq(count, 1u, "handoff: only the new owner writes");
    check_eq(writes[0].value, 7u, "handoff: the new owner's value");
    check(taym_timers_owns_target(&bank, 8u), "handoff: R8 still owned");
}

/* A STOP releases ownership so the caller's background may write again. */
static void test_stop_releases(void)
{
    TaymTimerBank bank;
    TaymMods m;

    build_fixture(&bank, 1u);
    m = mods_start(PERIOD, TAYM_TLAN_NONE, 0u, 1u);
    check(taym_timers_command(&bank, 0u, &m, 0u) == TAYM_OK, "start");
    check(taym_timers_owns_target(&bank, 8u), "owns R8 while active");
    check(!taym_timers_owns_target(&bank, 9u), "does not own R9");

    m = mods_simple(TAYM_CMD_STOP);
    check(taym_timers_command(&bank, 0u, &m, INTERVAL) == TAYM_OK, "stop");
    check(!taym_timers_owns_target(&bank, 8u), "releases R8 on stop");
}

/* Bad references are refused and leave the timer untouched (not half-started). */
static void test_range_checks(void)
{
    TaymTimerBank bank;
    TaymMods m;

    build_fixture(&bank, 1u);

    m = mods_start(PERIOD, TAYM_TLAN_NONE, 0u, 1u);
    m.first_action = 999u;
    check(taym_timers_command(&bank, 0u, &m, 0u) == TAYM_ERROR_RANGE, "action out of range");
    check(!taym_timers_owns_target(&bank, 8u), "refused start owns nothing");

    m = mods_start(PERIOD, 999u, 0u, 1u);
    check(taym_timers_command(&bank, 0u, &m, 0u) == TAYM_ERROR_RANGE, "tlan out of range");

    m = mods_start(PERIOD, TAYM_TLAN_NONE, 0u, 1u);
    check(taym_timers_command(&bank, 5u, &m, 0u) == TAYM_ERROR_ARGUMENT, "timer out of range");

    /* MODULATE on an inactive timer is forbidden by the spec; we ignore it. */
    m = mods_modulate(0u, TAYM_TLAN_UNCHANGED, 0u, 1u);
    check(taym_timers_command(&bank, 0u, &m, 0u) == TAYM_OK, "modulate inactive is ignored");
    check(!taym_timers_owns_target(&bank, 8u), "ignored modulate owns nothing");
}

/* The engine is chip-agnostic: a target it knows nothing about, fed by a
   16-bit lane, comes out with its id and its whole value, and says where the
   value came from. */
static void test_engine_reports_raw_targets(void)
{
    TaymTimerBank bank;
    TaymTimerWrite writes[32];
    TaymMods m;
    size_t count = 0u;

    build_fixture(&bank, 1u);
    lanes[3].value_type = TAYM_VT_U16;   /* [0x1234, 0xFFFF] looping */
    lanes[3].value_offset = 0u;
    lanes[3].length = 2u;
    lanes[3].loop_index = 0u;
    actions[5].target_id = 0x20u;        /* no such AY register */
    actions[5].source_mode = TAYM_SRC_BIND_LANE;
    actions[5].operand = 3u;
    actions[6].target_id = 0x21u;
    actions[6].source_mode = TAYM_SRC_INLINE_VALUE;
    actions[6].operand = 0x00ABCDEFu;

    m = mods_start(PERIOD, TAYM_TLAN_NONE, 5u, 2u);
    check(taym_timers_command(&bank, 0u, &m, 0u) == TAYM_OK, "start raw");
    check(taym_timers_collect(&bank, 2u * INTERVAL, writes, 32u, &count) == TAYM_OK, "collect raw");

    check_eq(count, 4u, "raw: two targets, two expiries");
    check_eq(writes[0].target, 0x20u, "raw: target id kept");
    check_eq(writes[0].value, 0x1234u, "raw: 16-bit value not cut");
    check_eq(writes[0].value_type, TAYM_VT_U16, "raw: lane type reported");
    check_eq(writes[1].target, 0x21u, "raw: second target");
    check_eq(writes[1].value, 0x00ABCDEFu, "raw: inline operand whole");
    check_eq(writes[1].value_type, 0u, "raw: inline has no lane type");
    check_eq(writes[2].value, 0xFFFFu, "raw: next lane element");
    check(writes[0].expiry_start && !writes[1].expiry_start
          && writes[2].expiry_start && !writes[3].expiry_start,
          "raw: each expiry marks its first write");

    /* The AY has neither target, so its step drops them all. */
    check_eq(taym_ay_resolve(writes, count, TAYM_AY_VARIANT_AY), 0u, "raw: not AY registers");
}

/* S11.1 across the line: the engine reports each timer's own sample amplitude
   (it is not exclusively owned), and the AY step combines it into the volume
   register of the same expiry. */
static void test_amplitude_pairs_within_its_own_expiry(void)
{
    TaymTimerBank bank;
    TaymTimerWrite writes[32];
    TaymMods m;
    size_t count = 0u;

    build_fixture(&bank, 2u);
    lanes[3].value_type = TAYM_VT_U16;
    lanes[3].value_offset = 0u;
    lanes[3].length = 2u;
    lanes[3].loop_index = 0u;
    actions[5].target_id = 0x08u;                       /* timer 0: R8 volume 12 */
    actions[5].source_mode = TAYM_SRC_INLINE_VALUE;
    actions[5].operand = 12u;
    actions[6].target_id = TAYM_TGT_SAMPLE_AMPLITUDE;
    actions[6].source_mode = TAYM_SRC_BIND_LANE;
    actions[6].operand = 3u;
    actions[7].target_id = 0x09u;                       /* timer 1: R9 volume 10 */
    actions[7].source_mode = TAYM_SRC_INLINE_VALUE;
    actions[7].operand = 10u;
    actions[8].target_id = TAYM_TGT_SAMPLE_AMPLITUDE;
    actions[8].source_mode = TAYM_SRC_BIND_LANE;
    actions[8].operand = 3u;

    m = mods_start(PERIOD, TAYM_TLAN_NONE, 5u, 2u);
    check(taym_timers_command(&bank, 0u, &m, 0u) == TAYM_OK, "start sample 0");
    m = mods_start(PERIOD, TAYM_TLAN_NONE, 7u, 2u);
    check(taym_timers_command(&bank, 1u, &m, 0u) == TAYM_OK, "start sample 1");

    check(taym_timers_collect(&bank, 1u, writes, 32u, &count) == TAYM_OK, "collect samples");
    check_eq(count, 4u, "amplitude: both timers report theirs");
    check_eq(writes[1].target, TAYM_TGT_SAMPLE_AMPLITUDE, "amplitude: timer 0's");
    check_eq(writes[3].target, TAYM_TGT_SAMPLE_AMPLITUDE, "amplitude: timer 1's");
    check_eq(writes[1].timer, 0u, "amplitude: from timer 0");
    check_eq(writes[3].timer, 1u, "amplitude: from timer 1");

    count = taym_ay_resolve(writes, count, TAYM_AY_VARIANT_AY);
    check_eq(count, 2u, "amplitude: two register writes");
    check_eq(writes[0].target, 0x08u, "amplitude: R8");
    check_eq(writes[0].value,
             taym_ay_combine_amplitude(TAYM_AY_VARIANT_AY, 12u, 0x1234u, 0xFFFFu),
             "amplitude: R8 combined at 16-bit full scale");
    check_eq(writes[1].target, 0x09u, "amplitude: R9");
    check_eq(writes[1].value,
             taym_ay_combine_amplitude(TAYM_AY_VARIANT_AY, 10u, 0x1234u, 0xFFFFu),
             "amplitude: R9 combined with its own timer's amplitude");
}

/* The AY's own rules, on hand-made writes: bytes, the 4-bit shape, and no
   amplitude leaking from one expiry into the next. */
static void test_ay_resolve_rules(void)
{
    TaymTimerWrite writes[5];
    size_t count;

    memset(writes, 0, sizeof(writes));
    writes[0].cycle = 10u; writes[0].target = 0x0Du; writes[0].value = 0xFEu;
    writes[0].expiry_start = 1u;
    writes[1].cycle = 20u; writes[1].target = 0x08u; writes[1].value = 15u;
    writes[1].expiry_start = 1u;
    writes[2].cycle = 20u; writes[2].target = TAYM_TGT_SAMPLE_AMPLITUDE; writes[2].value = 0u;
    writes[2].value_type = TAYM_VT_U8;
    writes[3].cycle = 30u; writes[3].target = 0x08u; writes[3].value = 0x1FFu;
    writes[3].expiry_start = 1u;
    writes[4].cycle = 30u; writes[4].target = 0x00u; writes[4].value = 0x2A5u;

    count = taym_ay_resolve(writes, 5u, TAYM_AY_VARIANT_AY);
    check_eq(count, 4u, "ay: the amplitude itself is not a write");
    check_eq(writes[0].target, 0x0Du, "ay: R13");
    check_eq(writes[0].value, 0x0Eu, "ay: R13 keeps four bits");
    check_eq(writes[1].value, 0u, "ay: volume 15 at zero amplitude is the floor");
    check_eq((unsigned long) writes[1].cycle, 20u, "ay: cycle kept");
    check_eq(writes[2].value, 0xFFu, "ay: the next expiry's R8 is not combined");
    check_eq(writes[3].target, 0x00u, "ay: R0");
    check_eq(writes[3].value, 0xA5u, "ay: a register is a byte");
}

/* S11.1: delog the volume code, scale by the linear amplitude, requantize
   once. Full amplitude keeps the code; zero amplitude floors it. */
static void test_amplitude_combine(void)
{
    check_eq(taym_ay_combine_amplitude(TAYM_AY_VARIANT_AY, 15u, 255u, 255u), 15u,
             "combine: full scale keeps the code");
    check_eq(taym_ay_combine_amplitude(TAYM_AY_VARIANT_AY, 15u, 0u, 255u), 0u,
             "combine: zero amplitude is the floor");
    check_eq(taym_ay_combine_amplitude(TAYM_AY_VARIANT_YM, 15u, 255u, 255u), 15u,
             "combine: YM full scale keeps the code");
    /* Half of full scale is below the top code but above the floor. */
    {
        const uint8_t half = taym_ay_combine_amplitude(TAYM_AY_VARIANT_AY, 15u, 128u, 255u);
        check(half > 0u && half < 15u, "combine: half scale lands between");
    }
    check_eq(taym_ay_combine_amplitude(TAYM_AY_VARIANT_AY, 15u, 128u, 0u), 0u,
             "combine: zero full scale is the floor");
}

/* An expiry is atomic: a buffer too small to hold one stops short rather than
   splitting it, and the next pull delivers it whole. */
static void test_capacity_never_splits_an_expiry(void)
{
    TaymTimerBank bank;
    TaymTimerWrite writes[TAYM_MAX_TIMER_TARGETS * 2u];
    TaymMods m;
    size_t count = 0u;

    build_fixture(&bank, 1u);
    /* Two targets in one slice: R8 and R9, both on lane 0. */
    actions[6].target_id = 8u;
    actions[6].source_mode = TAYM_SRC_BIND_LANE;
    actions[6].operand = 0u;
    actions[7].target_id = 9u;
    actions[7].source_mode = TAYM_SRC_BIND_LANE;
    actions[7].operand = 0u;
    m = mods_start(PERIOD, TAYM_TLAN_NONE, 6u, 2u);
    check(taym_timers_command(&bank, 0u, &m, 0u) == TAYM_OK, "start two targets");

    /* Capacity below one expiry's worth: refuse, write nothing. */
    check(taym_timers_collect(&bank, 10u * INTERVAL, writes, TAYM_MAX_TIMER_TARGETS - 1u,
                              &count) == TAYM_ERROR_RANGE,
          "capacity: too small refuses");
    check_eq(count, 0u, "capacity: nothing written when refused");

    /* Room for one expiry: exactly one expiry's writes come out, whole. */
    check(taym_timers_collect(&bank, 10u * INTERVAL, writes, TAYM_MAX_TIMER_TARGETS,
                              &count) == TAYM_ERROR_RANGE,
          "capacity: one expiry then stops");
    check_eq(count, 2u, "capacity: both targets of the expiry");
    check_eq(writes[0].target, 8u, "capacity: R8");
    check_eq(writes[1].target, 9u, "capacity: R9");
    check_eq(writes[0].cycle, writes[1].cycle, "capacity: same expiry cycle");
}

/* S6.2: `$FE n` is 4*n frames, and R13 is an event: a frame holds it only
   where the stream writes it. Mirrors python tests/test_taymengine_timeline.py. */
static void test_psg_parse(void)
{
    static const uint8_t psg[] = {
        'P', 'S', 'G', 0x1A, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
        0xFF, 0, 0x55, 13, 9,   /* frame 0: R0, R13 */
        0xFE, 1,                /* frames 0..3 */
        0xFF,                   /* frame 4 */
        13, 9,                  /* frame 5: the same shape again */
        0xFF, 0xFD
    };
    static const uint8_t r13[7] = {9u, 0xFFu, 0xFFu, 0xFFu, 0xFFu, 9u, 0xFFu};
    uint8_t out[8 * 14];
    size_t frames = 0u;
    size_t i;

    check_eq(taym_player_parse_psg(psg, sizeof(psg), out, 8u, &frames), TAYM_OK, "parse ok");
    check_eq(frames, 7u, "frame count with a 4-frame pause");
    for (i = 0u; i < 7u && i < frames; i++) {
        check_eq(out[i * 14u + 13u], r13[i], "R13 per frame");
        check_eq(out[i * 14u + 0u], 0x55u, "R0 carried");
    }
}

/* loop_start marks the expiry a timer's output repeats from: each lane on the
   element it loops back to. A timer without lanes repeats at every expiry; a
   no-loop lane counts once it rests on its final element. */
static void test_loop_start(void)
{
    TaymTimerBank bank;
    TaymTimerWrite writes[32];
    TaymMods m;
    size_t count = 0u;

    build_fixture(&bank, 1u);
    m = mods_start(PERIOD, TAYM_TLAN_NONE, 0u, 1u); /* R8 <- lane 0, [15, 9] looping */
    check(taym_timers_command(&bank, 0u, &m, 0u) == TAYM_OK, "start");
    check(taym_timers_collect(&bank, 4u * INTERVAL, writes, 32u, &count) == TAYM_OK, "collect");
    check_eq(count, 4u, "loop start: four expiries");
    check(writes[0].loop_start == 1u && writes[1].loop_start == 0u
              && writes[2].loop_start == 1u && writes[3].loop_start == 0u,
          "loop start: once per two-element loop, from the first expiry");

    build_fixture(&bank, 1u);
    m = mods_start(PERIOD, TAYM_TLAN_NONE, 1u, 1u); /* R8 <- inline 7 */
    check(taym_timers_command(&bank, 0u, &m, 0u) == TAYM_OK, "start inline");
    check(taym_timers_collect(&bank, 3u * INTERVAL, writes, 32u, &count) == TAYM_OK, "collect inline");
    check(count == 3u && writes[0].loop_start == 1u && writes[1].loop_start == 1u
              && writes[2].loop_start == 1u,
          "loop start: no lanes, every expiry");

    build_fixture(&bank, 1u);
    m = mods_start(PERIOD, TAYM_TLAN_NONE, 3u, 1u); /* R8 <- lane 1, [15, 9] no loop */
    check(taym_timers_command(&bank, 0u, &m, 0u) == TAYM_OK, "start no-loop");
    check(taym_timers_collect(&bank, 3u * INTERVAL, writes, 32u, &count) == TAYM_OK, "collect no-loop");
    check(count == 3u && writes[0].loop_start == 0u && writes[1].loop_start == 1u
              && writes[2].loop_start == 1u,
          "loop start: a no-loop lane, once it rests on its final element");
}

int main(void)
{
    test_armed_first_write();
    test_continuous_expiry_clock();
    test_value_lane_dormancy();
    test_timer_lane_quiescence();
    test_timer_lane_looping_runs_on();
    test_no_rate_waits_one_frame();
    test_modulate_pending_element();
    test_modulate_preserves_phase();
    test_ownership_handoff();
    test_stop_releases();
    test_range_checks();
    test_engine_reports_raw_targets();
    test_amplitude_pairs_within_its_own_expiry();
    test_ay_resolve_rules();
    test_amplitude_combine();
    test_capacity_never_splits_an_expiry();
    test_loop_start();
    test_psg_parse();

    if (failures != 0) {
        fprintf(stderr, "%d check(s) failed\n", failures);
        return 1;
    }
    printf("taym_timers: all checks passed\n");
    return 0;
}
