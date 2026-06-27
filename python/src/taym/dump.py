"""TAYM txt dump -- two modes.

structural(data)  : faithful field-by-field rendering of every chunk/record,
                    1:1 with the spec layout, for format debugging. Works on
                    raw bytes so it shows reserved fields and exact tags.
timeline(taym)    : higher-level decoded view -- per frame, what each timer
                    does (START/MODULATE/STOP with resolved lanes/actions), for
                    music debugging. Works on the parsed model.
"""
from __future__ import annotations

import struct

from . import spec
from .codec import _records, _split_chunks
from .model import Taym

_CMD = {spec.CMD_EMPTY: "EMPTY", spec.CMD_START: "START",
        spec.CMD_MODULATE: "MODULATE", spec.CMD_STOP: "STOP"}
_VT = {spec.VT_U8: "U8", spec.VT_U16: "U16", spec.VT_U32: "U32"}
_TM = {spec.TM_ABSOLUTE: "ABS", spec.TM_RELATIVE: "REL"}
_SRC = {spec.SRC_INLINE_VALUE: "INLINE", spec.SRC_BIND_LANE: "LANE"}
_AY_REG = {i: f"R{i}" for i in range(spec.AY_TARGET_MAX + 1)}


def _loop(v):
    return "none" if v == spec.NO_LOOP else v


def _resv(v):
    """Trailing ' resv=...' suffix, or '' when reserved bytes are all zero.
    Accepts an int field or a bytes field (rendered as hex)."""
    if isinstance(v, (bytes, bytearray)):
        return "" if not any(v) else f" resv={v.hex()}"
    return "" if v == 0 else f" resv={v}"


# --------------------------------------------------------------------------
# Structural
# --------------------------------------------------------------------------
def structural(data: bytes) -> str:
    out = [f"== TAYM structural dump ({len(data)} bytes) =="]
    magic, version, hsize, flags, chunk_bytes = struct.unpack(
        spec.HEADER_FMT, data[:spec.HEADER_SIZE])
    out.append(f"header: magic={magic!r} version={version} header_size={hsize} "
               f"flags={flags} chunk_bytes={chunk_bytes}")
    chunks, _, _ = _split_chunks(data)

    renderers = {
        "TRAK": _r_trak, "CHIP": _r_chip, "TIMR": _r_timr, "MODS": _r_mods,
        "ACTN": _r_actn, "LANE": _r_lane, "TLAN": _r_tlan,
    }
    strides = {"TRAK": spec.TRAK_SIZE, "CHIP": spec.CHIP_SIZE, "TIMR": spec.TIMR_SIZE,
               "MODS": spec.MODS_SIZE, "ACTN": spec.ACTN_SIZE, "LANE": spec.LANE_SIZE,
               "TLAN": spec.TLAN_SIZE}

    for tag, payload in chunks.items():
        out.append("")
        out.append(f"[{tag}] {len(payload)} bytes")
        if tag in renderers:
            for j, rec in enumerate(_records(payload, strides[tag], tag)):
                out.append(f"  {tag}[{j}] {renderers[tag](rec)}")
        elif tag == "VU08":
            out.append(f"  {list(payload)}")
        elif tag == "VU16":
            out.append(f"  {list(struct.unpack(f'<{len(payload)//2}H', payload))}")
        elif tag == "VU32":
            out.append(f"  {list(struct.unpack(f'<{len(payload)//4}I', payload))}")
        elif tag == "INFO":
            from .codec import _parse_info
            info = _parse_info(payload)
            if not info:
                out.append("  (empty)")
            for k, v in info.items():
                out.append(f"  {k} = {v}")
        else:  # frame-data / unknown
            out.append(f"  ({len(payload)} opaque bytes, head={payload[:8].hex()})")
    return "\n".join(out)


def _r_trak(rec):
    fr, fc, lf, cc, tc, resv = struct.unpack(spec.TRAK_FMT, rec)
    return (f"frame_rate={spec.from_fix16(fr):g}Hz frame_count={fc} "
            f"loop_frame={_loop(lf)} chip_count={cc} timer_count={tc}{_resv(resv)}")


def _r_chip(rec):
    clk, tid, variant, resv, name, tag, config = struct.unpack(spec.CHIP_FMT, rec)
    name = name.split(b"\0", 1)[0].decode("ascii", "replace")
    tag = "none" if tag == b"\0\0\0\0" else tag.decode("ascii", "replace")
    return (f"clock={clk}Hz type=0x{tid:02X} variant={variant} name={name!r} "
            f"frame_data={tag} config=0x{config:08X}{_resv(resv)}")


def _r_timr(rec):
    div, ci, mode, resv = struct.unpack(spec.TIMR_FMT, rec)
    mn = {spec.CLOCK_ABS_RATE_HZ: "ABS_RATE_HZ", spec.CLOCK_CHIP_PERIOD: "CHIP_PERIOD"}.get(mode, mode)
    return f"chip={ci} clock_mode={mn} divider={div}{_resv(resv)}"


def _r_mods(rec):
    base, tlref, fa, ac, cmd, resv = struct.unpack(spec.MODS_FMT, rec)
    tl = {spec.TLAN_NONE: "NONE", spec.TLAN_UNCHANGED: "UNCHANGED"}.get(tlref, tlref)
    return (f"{_CMD.get(cmd, cmd):<8} base={base} tlan={tl} "
            f"actn=[{fa},+{ac}]{_resv(resv)}")


def _r_actn(rec):
    operand, tid, smode = struct.unpack(spec.ACTN_FMT, rec)
    return f"target=0x{tid:02X} src={_SRC.get(smode, smode)} operand={operand}"


def _r_lane(rec):
    vo, ln, li, vt, resv = struct.unpack(spec.LANE_FMT, rec)
    return f"type={_VT.get(vt, vt)} off={vo} len={ln} loop={_loop(li)}{_resv(resv)}"


def _r_tlan(rec):
    vo, ln, li, tm, resv = struct.unpack(spec.TLAN_FMT, rec)
    return f"mode={_TM.get(tm, tm)} off={vo} len={ln} loop={_loop(li)}{_resv(resv)}"


# --------------------------------------------------------------------------
# Timeline
# --------------------------------------------------------------------------
def _lane_values(t: Taym, lane_idx: int) -> list[int]:
    l = t.lanes[lane_idx]
    pool = t.pool_for(l.value_type)
    return pool[l.value_offset:l.value_offset + l.length]


def _tlan_values(t: Taym, tl_idx: int) -> list[int]:
    l = t.tlanes[tl_idx]
    return t.vu32[l.value_offset:l.value_offset + l.length]


_TLAN_UNIT = {spec.CLOCK_ABS_RATE_HZ: "Hz", spec.CLOCK_CHIP_PERIOD: "period"}


def _tlan_decoded(t: Taym, timer_idx: int, tl_idx: int) -> list[str]:
    """Decode each raw timer-lane value the way the engine does (S10):
    ABSOLUTE+ABS_RATE_HZ -> Hz (16.16); ABSOLUTE+CHIP_PERIOD -> raw period plus
    its effective Hz (clock/(divider*period)); RELATIVE -> 16.16 multiplier."""
    tlan = t.tlanes[tl_idx]
    timer = t.timers[timer_idx]
    out = []
    for val in _tlan_values(t, tl_idx):
        if tlan.timing_mode == spec.TM_RELATIVE:
            out.append(f"x{spec.from_fix16(val):g}")
        elif timer.clock_mode == spec.CLOCK_ABS_RATE_HZ:
            out.append(f"{spec.from_fix16(val):g}Hz")
        else:  # ABSOLUTE + CHIP_PERIOD
            chip = t.chips[timer.chip_index]
            hz = chip.clock_hz / (timer.clock_divider * val) if val else 0.0
            out.append(f"{val}p={hz:g}Hz")
    return out


def _tlan_str(t: Taym, timer_idx: int, tl_idx: int, decode: bool = False) -> str:
    """Values plus a header showing rel/abs timing and unit (from the timer's
    clock_mode: ABS_RATE_HZ -> Hz, CHIP_PERIOD -> chip-period counts). With
    decode=True, values are shown decoded (Hz / multipliers) instead of raw."""
    mode = _TM.get(t.tlanes[tl_idx].timing_mode, t.tlanes[tl_idx].timing_mode)
    unit = _TLAN_UNIT.get(t.timers[timer_idx].clock_mode, "?")
    vals = _tlan_decoded(t, timer_idx, tl_idx) if decode else _tlan_values(t, tl_idx)
    return f"({mode},{unit})[{', '.join(map(str, vals))}]"


def _target_name(t: Taym, chip_index: int, tid: int) -> str:
    if chip_index < len(t.chips) and t.chips[chip_index].chip_type_id == spec.CHIP_TYPE_AY:
        if tid in _AY_REG:
            return _AY_REG[tid]
    return f"0x{tid:02X}"


def _action_str(t: Taym, chip_index: int, a) -> str:
    name = _target_name(t, chip_index, a.target_id)
    if a.source_mode == spec.SRC_INLINE_VALUE:
        return f"{name}={a.operand}"
    if a.operand < len(t.lanes):
        return f"{name}<-lane{a.operand}{_lane_values(t, a.operand)}"
    return f"{name}<-lane{a.operand}(oob)"


def parse_frame_bound(spec_str: str, frame_rate_hz: float) -> int:
    """Parse a frame range bound: bare number = frame index, suffix 's' = seconds.

    e.g. "100" -> frame 100; "2.5s" -> round(2.5 * frame_rate_hz). Raises
    ValueError on malformed input.
    """
    s = spec_str.strip().lower()
    if s.endswith("s"):
        return round(float(s[:-1]) * frame_rate_hz)
    return int(s)


def timeline(t: Taym, first: int | None = None, last: int | None = None,
             decode_tlan: bool = False) -> str:
    nt = len(t.timers)
    fc = t.trak.frame_count
    lo = 0 if first is None else max(0, first)
    hi = fc - 1 if last is None else min(fc - 1, last)
    rng = "" if first is None and last is None else f", frames {lo}..{hi}"
    out = [f"== TAYM timeline ({fc} frames, {nt} timers"
           f" @ {t.trak.frame_rate_hz:g}Hz{rng}) =="]
    if not nt or len(t.mods) != fc * nt:
        out.append("  (no timers or MODS count mismatch)")
        return "\n".join(out)
    for frame in range(lo, hi + 1):
        events = []
        for ti in range(nt):
            m = t.mods[frame * nt + ti]
            if m.command == spec.CMD_EMPTY:
                continue
            chip = t.timers[ti].chip_index
            tag = "*loop*" if frame == t.trak.loop_frame else ""
            if m.command == spec.CMD_STOP:
                events.append(f"  T{ti} STOP")
            elif m.command in (spec.CMD_START, spec.CMD_MODULATE):
                tlref = m.timer_lane_ref
                if tlref == spec.TLAN_NONE:
                    tl = f"base={m.base_timer_value}"
                elif tlref == spec.TLAN_UNCHANGED:
                    tl = "tlan=keep"
                elif tlref < len(t.tlanes):
                    tl = f"tlan{tlref}{_tlan_str(t, ti, tlref, decode_tlan)}"
                else:
                    tl = f"tlan{tlref}(oob)"
                acts = [_action_str(t, chip, a) for a in
                        t.actions[m.first_action:m.first_action + m.action_count]]
                events.append(f"  T{ti} {_CMD[m.command]:<8} {tl}  "
                              + " ".join(acts) + (f"  {tag}" if tag else ""))
        if events:
            out.append(f"frame {frame}:")
            out.extend(events)
    return "\n".join(out)
