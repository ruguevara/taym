"""TAYM -> PCM reference renderer.

Interprets the format per docs/TAYM-format-draft-0.1.md sections 10-13 for a
single AY chip:

  - PSG0 frame data sets the background register state each frame (S13.1).
  - A timer owns its action targets while active and overrides the background
    for them (S13.1 step 3).
  - Each frame is subdivided by the active timer's rate: at every expiry the
    value lanes advance and the owned targets are (re)written; R13 writes
    retrigger the envelope (appendix A.2).
  - ABS_RATE_HZ timer lanes give the expiry rate directly in Hz; a bare base
    rate (no timer lane) is a constant rate. CHIP_PERIOD is supported via
    rate = clock/(divider*period).

Scope: the subset the converters emit (one active timer per chip is the common
case; multiple are rendered independently and the last writer to a shared
target wins within a frame, which the validator already forbids). The
format-virtual range (0x80..0xBF) is reserved in draft 0.1 and not modeled --
this is an AY-register oracle.
"""
from __future__ import annotations

from pathlib import Path

from .. import spec
from ..codec import read_taym
from ..model import Taym
from ..psg import parse_psg as _parse_psg


class EngineError(Exception):
    pass


# --------------------------------------------------------------------------
# Per-timer playback state (the running interpretation of the MODS stream).
# --------------------------------------------------------------------------
class _TimerState:
    __slots__ = ("active", "quiescent", "targets", "lane_idx", "tlan", "tlan_idx",
                 "base_rate", "pending", "samples_to_expiry", "armed")

    def __init__(self):
        self.reset()

    def reset(self):
        self.active = False
        self.quiescent = False
        self.targets = []          # list of (target_id, lane_or_None, inline_value)
        self.lane_idx = {}         # target_id -> running value-lane index
        self.tlan = None           # Tlan descriptor or None
        self.tlan_idx = 0
        self.base_rate = 0.0       # Hz
        self.pending = set()       # targets whose lane element 0 is pending (S12.3)
        # Continuous sub-frame expiry clock (does NOT reset at frame boundaries):
        self.samples_to_expiry = 0.0   # samples until the next expiry fires
        self.armed = False             # True right after START/install: the next
                                       # expiry writes element 0 without advancing


def _lane_values(t: Taym, lane):
    pool = t.pool_for(lane.value_type)
    return pool[lane.value_offset:lane.value_offset + lane.length]


def _advance(idx: int, lane) -> int:
    """Step a lane index honoring loop / dormancy (S9.1). Returns new index;
    a no-loop lane sticks on its last element (dormant -- caller suppresses the
    rewrite for write-sensitive targets if desired; the oracle rewrites)."""
    nxt = idx + 1
    if nxt < lane.length:
        return nxt
    if lane.loop_index == spec.NO_LOOP:
        return lane.length - 1     # dormant: stay on final
    return lane.loop_index


def _timer_rate_hz(t: Taym, timer, st: _TimerState) -> float:
    """Effective expiry rate in Hz from base + active timer lane (S10)."""
    base = st.base_rate
    if st.tlan is None:
        return base
    val = t.vu32[st.tlan.value_offset + st.tlan_idx]
    if st.tlan.timing_mode == spec.TM_ABSOLUTE:
        if timer.clock_mode == spec.CLOCK_ABS_RATE_HZ:
            return spec.from_fix16(val)
        # CHIP_PERIOD: val is an integer period.
        chip = t.chips[timer.chip_index]
        return chip.clock_hz / (timer.clock_divider * val) if val else base
    # RELATIVE: 16.16 multiplier on the base rate.
    return base * spec.from_fix16(val)


def _base_rate_hz(t: Taym, timer, base_timer_value: int) -> float:
    if timer.clock_mode == spec.CLOCK_ABS_RATE_HZ:
        return spec.from_fix16(base_timer_value)
    chip = t.chips[timer.chip_index]
    return chip.clock_hz / (timer.clock_divider * base_timer_value) if base_timer_value else 0.0


# --------------------------------------------------------------------------
# Render
# --------------------------------------------------------------------------
# AY stereo layouts (appendix A.1) -> per-channel pan (0=left, 1=right) for
# tone channels A,B,C. "center" channels map to 0.5. MONO is all-center, which
# makes the mono mix (L+R)/2 identical to the panned mix -- so the bit-exact
# oracle path (render(), MONO) is unchanged.
#
# ST_MONO is deliberately NOT here: the Atari ST combined-DAC mix is a
# non-linear function of the three channel volumes, not a pan, and the installed
# pyayay wheel exposes no `is_st` flag to reach Ayumi's native ST path. The
# engine warns and falls back to plain MONO for ST_MONO (the .get default).
# Per-channel pan direction for each layout: -1 = left, 0 = center, +1 = right.
# Actual pan = 0.5 + dir * stereo_width, so width controls how far from center
# the side channels sit. Bitphase's tracker mix uses width 60/200 = 0.30 (side
# channels at 0.20 / 0.80, not hard 0.0 / 1.0); DEFAULT_STEREO_WIDTH matches it
# so a default stereo render reproduces bitphase's balance.
DEFAULT_STEREO_WIDTH = 60.0 / 200.0

_AY_LAYOUT_DIRS = {
    spec.AY_LAYOUT_MONO: (0, 0, 0),
    spec.AY_LAYOUT_ABC:  (-1, 0, +1),
    spec.AY_LAYOUT_ACB:  (-1, +1, 0),
    spec.AY_LAYOUT_BAC:  (0, -1, +1),
    spec.AY_LAYOUT_BCA:  (+1, -1, 0),
    spec.AY_LAYOUT_CAB:  (0, +1, -1),
    spec.AY_LAYOUT_CBA:  (+1, 0, -1),
}


def _layout_pans(layout, stereo_width):
    dirs = _AY_LAYOUT_DIRS.get(layout, (0, 0, 0))
    return tuple(min(1.0, max(0.0, 0.5 + d * stereo_width)) for d in dirs)


def render(taym, sample_rate: int = 44100, chip_index: int = 0, remove_dc: bool = True):
    """TAYM (path/bytes/Taym) -> mono float32 numpy array at sample_rate.

    Mono is the bit-exact oracle path: all channels are panned center, so the
    output is layout-independent. For a stereo mix honoring CHIP.config (S6,
    A.1) use render_stereo(). Set remove_dc=False to bypass Ayumi's DC-blocking
    filter (raw DAC output -- useful for comparing renderers)."""
    return _render(taym, sample_rate, chip_index, stereo=False, remove_dc=remove_dc)


def render_stereo(taym, sample_rate: int = 44100, chip_index: int = 0, remove_dc: bool = True,
                  stereo_width: float = DEFAULT_STEREO_WIDTH):
    """TAYM -> (left, right) float32 arrays honoring the chip's stereo layout
    (CHIP.config bits 0..2, appendix A.1). Unknown layout falls back to MONO.
    Set remove_dc=False to bypass Ayumi's DC-blocking filter. stereo_width is the
    side-channel separation from center (0.0 = mono, 0.5 = hard pan); the default
    matches Bitphase's tracker mix."""
    return _render(taym, sample_rate, chip_index, stereo=True, remove_dc=remove_dc,
                   stereo_width=stereo_width)


def _render(taym, sample_rate: int, chip_index: int, stereo: bool, remove_dc: bool = True,
            stereo_width: float = DEFAULT_STEREO_WIDTH):
    try:
        import numpy as np
        from pyayay import Ayumi, ChipType
    except ImportError as e:  # noqa: F841
        raise EngineError("engine deps missing; install with: pip install taym[engine]")

    t = _load(taym)
    chip = t.chips[chip_index]
    if chip.chip_type_id != spec.CHIP_TYPE_AY:
        raise EngineError(f"chip {chip_index} is not AY (0x{chip.chip_type_id:04X})")

    # Background register frames from the chip's PSG stream (if any).
    if chip.frame_data_tag and chip.frame_data_tag in t.frame_data:
        bg = _parse_psg(t.frame_data[chip.frame_data_tag])
    else:
        bg = [[0] * 14 for _ in range(t.trak.frame_count)]
    n = min(t.trak.frame_count, len(bg))

    fps = t.trak.frame_rate_hz
    spf = max(1, round(sample_rate / fps))
    chip_type = ChipType.YM if chip.variant == spec.AY_VARIANT_YM else ChipType.AY
    ay = Ayumi(sample_rate=sample_rate, clock=chip.clock_hz or 1773400, type=chip_type)
    # Stereo: pan A/B/C per CHIP.config layout (A.1). Mono: all center, so the
    # (L+R)/2 mix is layout-independent and bit-exact.
    if stereo:
        layout = spec.ay_stereo_layout(chip.config)
        if layout == spec.AY_LAYOUT_ST_MONO:
            import warnings
            warnings.warn("ST_MONO: Atari ST combined-DAC mix unavailable "
                          "(pyayay has no is_st); rendering plain MONO",
                          RuntimeWarning, stacklevel=2)
        pans = _layout_pans(layout, stereo_width)
    else:
        pans = (0.5, 0.5, 0.5)
    # Equal-power panning (Ayumi is_eqp): pan_left=sqrt(1-pan), pan_right=sqrt(pan).
    # A center channel maps to sqrt(0.5)~=0.707 on each side, matching trackers
    # that drive Ayumi this way; the mono (L+R)/2 mix stays layout-independent.
    for ch in range(3):
        ay.set_pan(ch, pans[ch], True)

    # Timers belonging to this chip.
    timer_ids = [i for i, tm in enumerate(t.timers) if tm.chip_index == chip_index]
    states = {i: _TimerState() for i in timer_ids}
    nt = len(t.timers)

    outL = np.zeros(n * spf, dtype=np.float32)
    outR = np.zeros(n * spf, dtype=np.float32) if stereo else None
    bufL = np.zeros(spf, dtype=np.float32)
    bufR = np.zeros(spf, dtype=np.float32)
    pos = 0
    shadow = [None] * 14    # last value actually latched per reg (delta push)

    def render_block(length):
        nonlocal pos
        ay.process_block(bufL[:length], bufR[:length], length, remove_dc)
        if stereo:
            outL[pos:pos + length] = bufL[:length]
            outR[pos:pos + length] = bufR[:length]
        else:                              # mono oracle: unbiased (L+R)/2
            outL[pos:pos + length] = (bufL[:length] + bufR[:length]) * 0.5
        pos += length

    for frame in range(n):
        regs = list(bg[frame])
        # 1. resolve MODS commands for this chip's timers (S13.2 order: STOP/
        #    replaced first, then START, then MODULATE -- simplified: apply in
        #    timer order; the validator forbids conflicting same-frame claims).
        for ti in timer_ids:
            _apply_command(t, ti, states[ti], t.mods[frame * nt + ti])

        # 2. owned targets override background; collect active timers' writes.
        owned = {}   # target_id -> timer index owning it
        for ti in timer_ids:
            st = states[ti]
            if st.active or st.quiescent:
                for (tid, _lane, _inline) in st.targets:
                    owned[tid] = ti

        # Write only CHANGED background regs (S13.1). Re-latching an unchanged
        # R8/R11/R12 every frame would reset the envelope generator mid-buzz --
        # the AY only latches on an actual write, and the .psg stream is itself
        # delta-coded, so a steady background re-writes nothing. Owned regs are
        # the timer's; never push them as background.
        _push_regs_delta(ay, regs, owned, shadow)

        active = [ti for ti in timer_ids
                  if states[ti].active and not states[ti].quiescent]
        if not active:
            # no active timer this frame: render the background frame straight.
            render_block(spf)
            continue

        # Walk the frame as a sequence of sub-blocks bounded by timer expiries.
        # Each active timer carries its own continuous expiry clock across the
        # frame boundary (no per-frame reset -> no 50 Hz phase glitch).
        s = 0
        while s < spf:
            # fire any expiries due now (samples_to_expiry <= 0).
            for ti in active:
                st = states[ti]
                if st.samples_to_expiry <= 0.0:
                    _expiry(t, ay, regs, ti, st, owned)
                    rate = _timer_rate_hz(t, t.timers[ti], st)
                    st.samples_to_expiry += max(1.0, sample_rate / rate) if rate > 0 else spf
            # render up to the nearest upcoming expiry or the frame end.
            nxt = min((states[ti].samples_to_expiry for ti in active), default=spf)
            chunk = int(min(max(1.0, nxt), spf - s))
            render_block(chunk)
            for ti in active:
                states[ti].samples_to_expiry -= chunk
            s += chunk

    if stereo:
        return outL[:pos], outR[:pos]
    return outL[:pos]


def _load(taym) -> Taym:
    if isinstance(taym, Taym):
        return taym
    if isinstance(taym, (bytes, bytearray)):
        return read_taym(bytes(taym))
    return read_taym(Path(taym).read_bytes())


def _apply_command(t: Taym, ti: int, st: _TimerState, m):
    cmd = m.command
    if cmd == spec.CMD_EMPTY:
        return
    if cmd == spec.CMD_STOP:
        st.reset()
        return
    if cmd == spec.CMD_START:
        st.reset()
        st.active = True
        st.base_rate = _base_rate_hz(t, t.timers[ti], m.base_timer_value)
        st.tlan = None if m.timer_lane_ref in (spec.TLAN_NONE, spec.TLAN_UNCHANGED) \
            else t.tlanes[m.timer_lane_ref]
        st.tlan_idx = 0
        st.targets = []
        for a in t.actions[m.first_action:m.first_action + m.action_count]:
            lane = t.lanes[a.operand] if a.source_mode == spec.SRC_BIND_LANE else None
            inline = a.operand if a.source_mode == spec.SRC_INLINE_VALUE else 0
            st.targets.append((a.target_id, lane, inline))
            st.lane_idx[a.target_id] = 0
        # S12.2: the first expiry (fired immediately) writes element 0 + loads
        # interval 0 WITHOUT advancing. armed makes that one write a no-advance.
        st.armed = True
        st.samples_to_expiry = 0.0
        return
    if cmd == spec.CMD_MODULATE:
        if not st.active:
            return  # validator forbids; be defensive
        if m.base_timer_value:
            st.base_rate = _base_rate_hz(t, t.timers[ti], m.base_timer_value)
        if m.timer_lane_ref == spec.TLAN_NONE:
            st.tlan = None
        elif m.timer_lane_ref != spec.TLAN_UNCHANGED:
            new = t.tlanes[m.timer_lane_ref]
            st.tlan = new  # phase preserved: keep tlan_idx (validator checks shape)


def _push_regs_delta(ay, regs, owned, shadow):
    """Latch only background regs that changed since the last write (S13.1).

    An owned reg belongs to the timer: skip it and mark its shadow stale (None)
    so the background re-asserts cleanly once the timer releases it. A reg whose
    value equals its shadow is not re-written -- this is what keeps a steady
    tone/buzz envelope from being reset at every 50Hz frame."""
    idxs, vals = [], []
    for r in range(14):
        if r in owned:
            shadow[r] = None          # timer owns it; force a background rewrite on release
            continue
        v = regs[r] & 0xFF
        if r == 13 and (regs[r] & 0x80):
            continue                  # no-write sentinel: leave R13 untouched
        if shadow[r] != v:
            idxs.append(r); vals.append(v)
            shadow[r] = v
    if idxs:
        ay.set_registers(idxs, vals)


def _expiry(t: Taym, ay, regs, ti, st, owned):
    """One atomic timer expiry (S10.1, S13): advance lanes (unless armed = the
    immediate post-START expiry), then write owned targets. The timer lane
    advances with the value lanes; the new interval is loaded by the caller
    from the post-advance tlan index."""
    if st.armed:
        st.armed = False                 # this expiry writes element 0 as-is
    else:
        for (tid, lane, _inline) in st.targets:
            if lane is not None:
                st.lane_idx[tid] = _advance(st.lane_idx[tid], lane)
        if st.tlan is not None:
            st.tlan_idx = _advance(st.tlan_idx, st.tlan)
    for (tid, lane, inline) in st.targets:
        if owned.get(tid) != ti:
            continue  # another timer owns it this frame (handoff edge)
        v = (_lane_values(t, lane)[st.lane_idx[tid]] if lane is not None else inline) & 0xFF
        if tid == spec.AY_R13_SHAPE:
            ay.set_envelope_shape(v & 0x0F)   # write retriggers
        elif tid <= 13:
            ay.set_registers([tid], [v])
