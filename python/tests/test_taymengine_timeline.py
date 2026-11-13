"""The engine's register-write timeline (pytest).

_frame_writes is the engine's whole interpretation of the format: per frame, a
list of (ay_cycle, reg, value) writes that Ayumi then renders. It needs no
numpy/pyayay, so these run in the deps-free CI lane too.
"""
from taym import spec
from taym.engine.engine import _frame_writes
from taym.model import Actn, Chip, Lane, Mods, Taym, Timr, Tlan, Trak

AY_CLOCK = 1_773_400
FPS = 50.0
FRAME_CYCLES = 35_468          # 1773400 / 50


def _pwm_taym(period, frames=3, start_frame=0):
    """One CHIP_PERIOD timer alternating R8 between 15 and 9 every
    16*period cycles, started at `start_frame`; no PSG background."""
    mods = [Mods(command=spec.CMD_EMPTY) for _ in range(frames)]
    mods[start_frame] = Mods(command=spec.CMD_START, base_timer_value=period,
                             timer_lane_ref=spec.TLAN_NONE,
                             first_action=0, action_count=1)
    return Taym(
        trak=Trak(frame_rate_hz=FPS, frame_count=frames, loop_frame=spec.NO_LOOP),
        chips=[Chip(clock_hz=AY_CLOCK, chip_type_id=spec.CHIP_TYPE_AY, name="AY")],
        timers=[Timr(chip_index=0, clock_mode=spec.CLOCK_CHIP_PERIOD, clock_divider=16)],
        mods=mods,
        actions=[Actn(target_id=8, source_mode=spec.SRC_BIND_LANE, operand=0)],
        lanes=[Lane(value_type=spec.VT_U8, value_offset=0, length=2, loop_index=0)],
        tlanes=[], vu08=[15, 9], vu16=[], vu32=[], frame_data={})


def _timer_writes(frames):
    return [(c, v) for writes in frames for (c, r, v) in writes if r == 8]


def test_chip_period_expiries_are_exact_cycles_across_frames():
    """Invariant 1 (continuous expiry clock): expiries sit at exact multiples of
    16*period AY cycles from START, straight through the frame boundaries."""
    period = 222                          # 3552 cycles, not a divisor of a frame
    got = _timer_writes(list(_frame_writes(_pwm_taym(period), 0, AY_CLOCK)))
    interval = 16 * period
    want = [(k * interval, (15, 9)[k % 2]) for k in range(3 * FRAME_CYCLES // interval + 1)]
    assert got == want


def test_start_fires_at_frame_start_without_advancing():
    """Invariant 2 (armed first write): START at frame 1 writes lane element 0
    at that frame's first cycle; the second expiry writes element 1."""
    frames = list(_frame_writes(_pwm_taym(222, start_frame=1), 0, AY_CLOCK))
    got = _timer_writes(frames[1:])       # frame 0 is background only
    assert got[:2] == [(FRAME_CYCLES, 15), (FRAME_CYCLES + 16 * 222, 9)]


def test_writes_are_time_ordered():
    """Ayumi's render() needs non-decreasing cycles within each frame, and each
    frame's writes stay inside [frame start, next frame start]."""
    for f, writes in enumerate(_frame_writes(_pwm_taym(100), 0, AY_CLOCK)):
        cycles = [c for (c, _r, _v) in writes]
        assert cycles == sorted(cycles)
        assert all(f * FRAME_CYCLES <= c <= (f + 1) * FRAME_CYCLES for c in cycles)


def _tlane_taym(loop_index, frames=6):
    """The PWM timer driven by a two-element ABSOLUTE timer lane, looping or
    not. Both elements hold the same period, so only completion differs."""
    t = _pwm_taym(222, frames=frames)
    t.mods[0] = Mods(command=spec.CMD_START, base_timer_value=222,
                     timer_lane_ref=0, first_action=0, action_count=1)
    t.tlanes = [Tlan(timing_mode=spec.TM_ABSOLUTE, value_offset=0, length=2,
                     loop_index=loop_index)]
    t.vu32 = [222, 222]
    return t


def _r8_writes(taym):
    return [(c, v) for writes in _frame_writes(taym, 0, AY_CLOCK)
            for (c, r, v) in writes if r == 8]


def test_no_loop_timer_lane_goes_quiescent():
    """S10.2: a no-loop timer lane runs its final interval and then makes the
    timer quiescent -- no further expiries, no write at that final boundary.

    The bug this pins: treating a dormant final tlan element as "keep firing at
    that interval", which runs the timer forever (60 expiries over 6 frames
    instead of 2) and was found by diffing the C engine against this one."""
    got = _r8_writes(_tlane_taym(spec.NO_LOOP))
    assert got == [(0, 15), (16 * 222, 9)]


def test_looping_timer_lane_runs_indefinitely():
    """S10.2's other half: looping on the final timer element explicitly means
    to continue indefinitely. The contrast is the whole rule."""
    interval = 16 * 222
    got = _r8_writes(_tlane_taym(0))
    # Every expiry at a multiple of the interval inside the 6-frame walk.
    want = [(k * interval, (15, 9)[k % 2])
            for k in range(6 * FRAME_CYCLES // interval + 1)]
    assert got == want


def test_engine_fixture_is_valid_and_reaches_every_register_kind():
    """The engine-rules fixture (`taym sample --engine`) is what other engines
    are diffed against, so it must be a valid file and must keep exercising
    what it claims to: both timers, the 0x80 pairing on R9, R13 retriggers."""
    from taym import write_taym
    from taym.sample import build_engine_fixture
    from taym.validate import validate, validate_bytes

    t = build_engine_fixture()
    assert validate(t) + validate_bytes(write_taym(t)) == []

    writes = [w for frame in _frame_writes(t, 0, AY_CLOCK) for w in frame]
    by_reg = {}
    for (_c, r, _v) in writes:
        by_reg[r] = by_reg.get(r, 0) + 1
    for reg in (8, 9, 10, 11, 13):          # every timer-driven register
        assert by_reg.get(reg, 0) > 10, f"R{reg} barely written: {by_reg}"


def test_no_rate_timer_keeps_its_offset():
    """A timer whose rate drops to zero fires once per frame, a frame after its
    last expiry -- not on the frame grid. The fixture's frame 10 starts such a
    timer: one real interval, then one expiry a frame later."""
    from taym.sample import build_engine_fixture

    frames = list(_frame_writes(build_engine_fixture(), 0, AY_CLOCK))
    r8 = [(c, v) for f in (10, 11) for (c, r, v) in frames[f] if r == 8]
    start = 10 * FRAME_CYCLES
    assert r8 == [(start, 15), (start + 16 * 222, 9),
                  (start + 16 * 222 + FRAME_CYCLES, 15)]


def test_writes_cli_format(tmp_path, capsys):
    """`python -m taym writes` is the C engine's cross-language oracle: one
    "frame cycle reg value" line per write, decimal, in timeline order. The C
    side (c/tests/taym_timeline.c) prints the same and `cd c && make test`
    diffs them, so this format is a contract -- changing it breaks that diff."""
    from taym import write_taym
    from taym.__main__ import main

    path = tmp_path / "pwm.taym"
    path.write_bytes(write_taym(_pwm_taym(222)))

    assert main(["writes", str(path)]) == 0
    lines = capsys.readouterr().out.strip().splitlines()

    assert lines, "expected a write timeline"
    for line in lines:
        fields = line.split()
        assert len(fields) == 4, f"expected 4 fields, got {line!r}"
        for field in fields:
            assert field.isdigit(), f"expected decimal fields, got {line!r}"

    # The PWM timer's R8 writes: element 0 at the START cycle, then one
    # interval apart, matching _frame_writes itself.
    r8 = [(int(c), int(v)) for (_f, c, r, v) in (l.split() for l in lines) if int(r) == 8]
    assert r8[:2] == [(0, 15), (16 * 222, 9)]
