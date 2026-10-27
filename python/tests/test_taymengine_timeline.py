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
