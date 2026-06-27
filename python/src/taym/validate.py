"""TAYM draft 0.1 semantic validation (spec section 14).

validate(taym) -> list[str] of problems (empty = valid).
check(taym) raises ValidationError on the first problem.

Reference-quality: one guard per section-14 bullet, each message naming the
spec section. Structural framing (magic, sizes, strides, chunk_bytes) is the
codec's job (codec.CodecError); this module assumes a Taym that already parsed
and checks the *meaning*: ranges, enums, sentinels, cross-references, reserved
fields, and the MODS state machine.

Note: codec.read_taym builds a model with reserved bytes discarded, so a
"nonzero reserved field" can only be checked on raw bytes -- validate_bytes()
covers that against the original file; validate() works on the model.
"""
from __future__ import annotations

import struct

from . import spec
from .model import Taym


class ValidationError(Exception):
    pass


# --------------------------------------------------------------------------
# Model-level validation (the bulk of section 14).
# --------------------------------------------------------------------------
def validate(t: Taym) -> list[str]:
    p: list[str] = []
    _trak(t, p)
    _chips(t, p)
    _timers(t, p)
    _lanes(t, p)
    _tlanes(t, p)
    _actions(t, p)
    _mods(t, p)
    _frame_data(t, p)
    return p


def check(t: Taym) -> None:
    problems = validate(t)
    if problems:
        raise ValidationError(problems[0])


# --- TRAK (S4) ------------------------------------------------------------
def _trak(t: Taym, p):
    tr = t.trak
    if tr.frame_count == 0:
        p.append("S4: TRAK.frame_count is zero")
    if not spec.fits_fix16(tr.frame_rate_hz) or spec.to_fix16(tr.frame_rate_hz) == 0:
        p.append("S4: TRAK.frame_rate must be nonzero and fit unsigned 16.16")
    if tr.loop_frame != spec.NO_LOOP and tr.loop_frame >= tr.frame_count:
        p.append(f"S4: TRAK.loop_frame {tr.loop_frame} >= frame_count {tr.frame_count}")
    if len(t.mods) != tr.frame_count * len(t.timers):
        p.append(f"S4/S12: MODS has {len(t.mods)} records, expected "
                 f"frame_count*timer_count = {tr.frame_count * len(t.timers)}")
    if len(t.chips) > 0xFF:
        p.append("S4: chip_count exceeds u8")
    if len(t.timers) > 0xFF:
        p.append("S4: timer_count exceeds u8")


# --- CHIP (S6) ------------------------------------------------------------
def _chips(t: Taym, p):
    seen_tags = set()
    for i, c in enumerate(t.chips):
        if c.chip_type_id == spec.CHIP_TYPE_INVALID:
            p.append(f"S6: CHIP[{i}] chip_type_id 0x00 is invalid")
        if c.chip_type_id == spec.CHIP_TYPE_AY and c.variant not in (spec.AY_VARIANT_AY, spec.AY_VARIANT_YM):
            p.append(f"A.1: CHIP[{i}] AY variant {c.variant} undefined (0=AY, 1=YM)")
        if c.chip_type_id == spec.CHIP_TYPE_AY:
            if spec.ay_stereo_layout(c.config) not in spec.AY_LAYOUTS:
                p.append(f"A.1: CHIP[{i}] AY config stereo layout "
                         f"{spec.ay_stereo_layout(c.config)} undefined (0..7)")
            if c.config & ~spec.AY_CFG_STEREO_MASK:
                p.append(f"A.1: CHIP[{i}] AY config sets reserved bits "
                         f"0x{c.config & ~spec.AY_CFG_STEREO_MASK:08X}")
        if c.frame_data_tag:
            if c.frame_data_tag in seen_tags:
                p.append(f"S6.1: repeated frame_data_tag {c.frame_data_tag!r}")
            seen_tags.add(c.frame_data_tag)
            if c.frame_data_tag in (set(spec.CORE_ONCE) | {"INFO"}):
                p.append(f"S6.1: frame_data_tag {c.frame_data_tag!r} reuses a core/INFO tag")


# --- TIMR (S7) ------------------------------------------------------------
def _timers(t: Taym, p):
    for i, tm in enumerate(t.timers):
        if tm.clock_mode not in spec.CLOCK_MODES:
            p.append(f"S7: TIMR[{i}] clock_mode {tm.clock_mode} invalid")
            continue
        if tm.chip_index >= len(t.chips):
            p.append(f"S7: TIMR[{i}] chip_index {tm.chip_index} out of range")
            continue
        if tm.clock_mode == spec.CLOCK_ABS_RATE_HZ:
            if tm.clock_divider != 0:
                p.append(f"S7: TIMR[{i}] ABS_RATE_HZ requires clock_divider==0")
        else:  # CHIP_PERIOD
            if tm.clock_divider == 0:
                p.append(f"S7: TIMR[{i}] CHIP_PERIOD requires nonzero clock_divider")
            if t.chips[tm.chip_index].clock_hz == 0:
                p.append(f"S7: TIMR[{i}] CHIP_PERIOD chip has zero clock_hz")


# --- LANE (S9) ------------------------------------------------------------
def _lanes(t: Taym, p):
    for i, l in enumerate(t.lanes):
        if l.value_type not in spec.VALUE_TYPES:
            p.append(f"S9: LANE[{i}] value_type {l.value_type} invalid")
            continue
        if l.length == 0:
            p.append(f"S9: LANE[{i}] zero length")
            continue
        pool = t.pool_for(l.value_type)
        if l.value_offset + l.length > len(pool):
            p.append(f"S9: LANE[{i}] slice [{l.value_offset},+{l.length}] outside "
                     f"{spec.VALUE_TYPE_POOL[l.value_type][0]} (len {len(pool)})")
        if l.loop_index != spec.NO_LOOP and l.loop_index >= l.length:
            p.append(f"S9: LANE[{i}] loop_index {l.loop_index} >= length {l.length}")


# --- TLAN (S10) -----------------------------------------------------------
def _tlanes(t: Taym, p):
    for i, l in enumerate(t.tlanes):
        if l.timing_mode not in spec.TIMING_MODES:
            p.append(f"S10: TLAN[{i}] timing_mode {l.timing_mode} invalid")
        if l.length == 0:
            p.append(f"S10: TLAN[{i}] zero length")
            continue
        if l.value_offset + l.length > len(t.vu32):
            p.append(f"S10: TLAN[{i}] slice [{l.value_offset},+{l.length}] outside "
                     f"VU32 (len {len(t.vu32)})")
        if l.loop_index != spec.NO_LOOP and l.loop_index >= l.length:
            p.append(f"S10: TLAN[{i}] loop_index {l.loop_index} >= length {l.length}")


# --- ACTN (S11) -----------------------------------------------------------
def _valid_target(tid: int) -> bool:
    lo, hi = spec.TGT_FMT_VIRTUAL_RANGE
    if lo <= tid <= hi:
        return tid in spec.TGT_FMT_VIRTUAL_DEFINED  # 0x83..0xBF reserved -> invalid
    # hardware + engine ranges: openness depends on chip type; AY checks elsewhere.
    return True


def _actions(t: Taym, p):
    for i, a in enumerate(t.actions):
        if a.source_mode not in spec.SOURCE_MODES:
            p.append(f"S11: ACTN[{i}] source_mode {a.source_mode} invalid")
        if not _valid_target(a.target_id):
            p.append(f"S11: ACTN[{i}] target_id 0x{a.target_id:02X} is reserved/invalid")
        if a.source_mode == spec.SRC_BIND_LANE and a.operand >= len(t.lanes):
            p.append(f"S11: ACTN[{i}] BIND_LANE operand {a.operand} out of LANE range")


def _ay_target_ok(tid: int) -> bool:
    # Appendix A.2/A.3: R0..R13 hardware and format-virtual 0x80..0x82 only.
    # All other AY targets are invalid until a later AY registry assigns them.
    if tid <= spec.AY_TARGET_MAX:
        return True
    if spec.TGT_HW_RANGE[0] <= tid <= spec.TGT_HW_RANGE[1]:
        return False  # 0x0E..0x7F unassigned
    if tid in spec.TGT_FMT_VIRTUAL_DEFINED:
        return True
    return False


# --- MODS state machine (S12, S13) ---------------------------------------
def _mods(t: Taym, p):
    nt = len(t.timers)
    if nt == 0:
        return
    if len(t.mods) != t.trak.frame_count * nt:
        return  # count mismatch already reported by _trak; walk would over-index
    # Per-timer active state, replayed frame by frame to enforce S12/S13.
    # active[timer] = None | "active" | "quiescent"; targets per chip for ownership.
    active = [None] * nt
    active_base = [0] * nt
    active_tlan_ref = [None] * nt

    def slice_targets(rec):
        return t.actions[rec.first_action:rec.first_action + rec.action_count]

    for frame in range(t.trak.frame_count):
        owners = {}  # (chip_index, target_id) -> timer, for this frame's active set
        # Rebuild current ownership snapshot from active timers' starts is complex;
        # we instead check per-record legality + same-frame double claims.
        starts_this_frame = {}
        for ti in range(nt):
            rec = t.mods[frame * nt + ti]
            cmd = rec.command
            if cmd not in spec.COMMANDS:
                p.append(f"S12: MODS frame {frame} timer {ti} command {cmd} invalid")
                continue
            if cmd == spec.CMD_START:
                _check_start(t, rec, frame, ti, p)
                active[ti] = "active"
                active_base[ti] = rec.base_timer_value
                active_tlan_ref[ti] = _valid_tlan_ref(t, rec.timer_lane_ref)
                _check_abs_relative_rate(t, ti, active_base[ti],
                                         active_tlan_ref[ti], frame, p)
                chip = t.timers[ti].chip_index
                for a in slice_targets(rec):
                    key = (chip, a.target_id)
                    if key in starts_this_frame:
                        p.append(f"S13.2: frame {frame} two STARTs claim chip "
                                 f"{chip} target 0x{a.target_id:02X}")
                    starts_this_frame[key] = ti
            elif cmd == spec.CMD_MODULATE:
                if active[ti] != "active":
                    p.append(f"S12.3: MODS frame {frame} timer {ti} MODULATE on "
                             f"{'quiescent' if active[ti]=='quiescent' else 'inactive'} timer")
                if rec.timer_lane_ref not in (spec.TLAN_NONE, spec.TLAN_UNCHANGED) \
                        and rec.timer_lane_ref >= len(t.tlanes):
                    p.append(f"S12: MODS frame {frame} timer {ti} timer_lane_ref "
                             f"{rec.timer_lane_ref} out of TLAN range")
                _check_actions_slice(t, rec, frame, ti, p)
                if active[ti] == "active":
                    base = rec.base_timer_value or active_base[ti]
                    tlan_ref = active_tlan_ref[ti]
                    if rec.timer_lane_ref == spec.TLAN_NONE:
                        tlan_ref = None
                    elif rec.timer_lane_ref != spec.TLAN_UNCHANGED:
                        tlan_ref = _valid_tlan_ref(t, rec.timer_lane_ref)
                    _check_abs_relative_rate(t, ti, base, tlan_ref, frame, p)
                    active_base[ti] = base
                    active_tlan_ref[ti] = tlan_ref
            elif cmd == spec.CMD_STOP:
                active[ti] = None
                active_base[ti] = 0
                active_tlan_ref[ti] = None
            # EMPTY: no state change.

    # loop_frame reconstruction (S4): every timer at loop_frame is START or STOP.
    lf = t.trak.loop_frame
    if lf != spec.NO_LOOP and lf < t.trak.frame_count and lf * nt + nt <= len(t.mods):
        for ti in range(nt):
            rec = t.mods[lf * nt + ti]
            if rec.command not in (spec.CMD_START, spec.CMD_STOP):
                p.append(f"S4: timer {ti} at loop_frame {lf} is neither START nor STOP")


def _check_start(t: Taym, rec, frame, ti, p):
    if rec.base_timer_value == 0:
        p.append(f"S12.2: MODS frame {frame} timer {ti} START base_timer_value is zero")
    if rec.timer_lane_ref == spec.TLAN_UNCHANGED:
        p.append(f"S12.2: MODS frame {frame} timer {ti} START timer_lane_ref UNCHANGED invalid")
    elif rec.timer_lane_ref != spec.TLAN_NONE and rec.timer_lane_ref >= len(t.tlanes):
        p.append(f"S12.2: MODS frame {frame} timer {ti} timer_lane_ref "
                 f"{rec.timer_lane_ref} out of TLAN range")
    if rec.action_count < 1:
        p.append(f"S12.2: MODS frame {frame} timer {ti} START with no actions")
    _check_actions_slice(t, rec, frame, ti, p)


def _valid_tlan_ref(t: Taym, ref: int):
    if ref == spec.TLAN_NONE or ref == spec.TLAN_UNCHANGED:
        return None
    if 0 <= ref < len(t.tlanes):
        return ref
    return None


def _check_abs_relative_rate(t: Taym, ti: int, base: int, tlan_ref, frame: int, p):
    if ti >= len(t.timers) or t.timers[ti].clock_mode != spec.CLOCK_ABS_RATE_HZ:
        return
    if tlan_ref is None or tlan_ref >= len(t.tlanes) or base == 0:
        return
    lane = t.tlanes[tlan_ref]
    if lane.timing_mode != spec.TM_RELATIVE:
        return
    if lane.value_offset + lane.length > len(t.vu32):
        return
    for value_index in range(lane.value_offset, lane.value_offset + lane.length):
        multiplier = t.vu32[value_index]
        if not spec.fix16_product_fits(base, multiplier):
            p.append(f"S10/S14: MODS frame {frame} timer {ti} ABS_RATE_HZ "
                     "relative timer lane effective rate exceeds unsigned 16.16")
            return


def _check_actions_slice(t: Taym, rec, frame, ti, p):
    if rec.action_count == 0:
        return
    if rec.first_action + rec.action_count > len(t.actions):
        p.append(f"S12: MODS frame {frame} timer {ti} action slice "
                 f"[{rec.first_action},+{rec.action_count}] out of ACTN range")
        return
    sl = t.actions[rec.first_action:rec.first_action + rec.action_count]
    chip_type = None
    chip = t.timers[ti].chip_index if ti < len(t.timers) else None
    if chip is not None and chip < len(t.chips):
        chip_type = t.chips[chip].chip_type_id
    prev = -1
    for a in sl:
        if a.target_id <= prev:
            p.append(f"S11: MODS frame {frame} timer {ti} action slice not strictly "
                     f"sorted / duplicate target 0x{a.target_id:02X}")
        prev = a.target_id
        if chip_type == spec.CHIP_TYPE_AY and not _ay_target_ok(a.target_id):
            p.append(f"AppA: MODS frame {frame} timer {ti} target 0x{a.target_id:02X} "
                     f"invalid for AY chip")
        # target/lane scalar-type mismatch (S9): AY hardware regs are u8.
        if a.source_mode == spec.SRC_BIND_LANE and a.operand < len(t.lanes):
            lane = t.lanes[a.operand]
            if chip_type == spec.CHIP_TYPE_AY and a.target_id <= spec.AY_TARGET_MAX \
                    and lane.value_type != spec.VT_U8:
                p.append(f"S9/AppA: MODS frame {frame} timer {ti} AY reg "
                         f"0x{a.target_id:02X} bound to non-U8 lane")


# --- frame data (S6.2) ----------------------------------------------------
def _frame_data(t: Taym, p):
    for i, c in enumerate(t.chips):
        if not c.frame_data_tag:
            continue
        payload = t.frame_data.get(c.frame_data_tag)
        if payload is None:
            # external sidecar is allowed (S6.1); nothing to check in-model.
            continue
        if c.chip_type_id == spec.CHIP_TYPE_AY:
            if payload[:4] != b"PSG\x1a":
                p.append(f"S6.2: CHIP[{i}] frame data {c.frame_data_tag!r} lacks PSG header")


# --------------------------------------------------------------------------
# Reserved-field check -- needs raw bytes (the model drops reserved).
# --------------------------------------------------------------------------
def validate_bytes(data: bytes) -> list[str]:
    """Section-14 'nonzero reserved field' check against the raw file.

    Walks the same chunks codec does and verifies every record's reserved
    bytes are zero. Returns problems; combine with validate(read_taym(data))
    for the full picture.
    """
    from .codec import _split_chunks, _records
    p: list[str] = []
    try:
        chunks, _, _ = _split_chunks(data)
    except Exception as e:  # noqa: BLE001 -- structural error surfaced as a problem
        return [f"codec: {e}"]

    def resv_check(tag, stride, fmt, resv_index):
        if tag not in chunks:
            return
        for j, rec in enumerate(_records(chunks[tag], stride, tag)):
            fields = struct.unpack(fmt, rec)
            r = fields[resv_index]
            if (isinstance(r, bytes) and r.strip(b"\0")) or (isinstance(r, int) and r):
                p.append(f"S14: {tag}[{j}] nonzero reserved field")

    resv_check("TRAK", spec.TRAK_SIZE, spec.TRAK_FMT, 5)
    resv_check("CHIP", spec.CHIP_SIZE, spec.CHIP_FMT, 3)
    resv_check("TIMR", spec.TIMR_SIZE, spec.TIMR_FMT, 3)
    resv_check("MODS", spec.MODS_SIZE, spec.MODS_FMT, 5)
    resv_check("LANE", spec.LANE_SIZE, spec.LANE_FMT, 4)
    resv_check("TLAN", spec.TLAN_SIZE, spec.TLAN_FMT, 4)
    return p
