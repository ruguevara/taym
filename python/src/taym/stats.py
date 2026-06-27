"""TAYM stats -- counts, sizes, MODS command histogram, pool/lane use.

stats(taym) -> dict of plain values; format_stats() renders it. Reference
diagnostics, not a hot path. Sizes are the canonical on-disk byte sizes.
"""
from __future__ import annotations

from . import spec
from .codec import write_taym
from .model import Taym

_CMD_NAME = {spec.CMD_EMPTY: "EMPTY", spec.CMD_START: "START",
             spec.CMD_MODULATE: "MODULATE", spec.CMD_STOP: "STOP"}

# Full chip names per spec appendix A.1 (chip_type_id -> human-readable family).
# Non-AY entries are named but have no register registry yet (status A.4).
_CHIP_TYPE_NAME = {spec.CHIP_TYPE_INVALID: "invalid",
                   spec.CHIP_TYPE_AY: "AY-3-8910 / YM2149 family",
                   **spec.CHIP_TYPE_REGISTRY}

# Per-type variant names. AY (0x0001) selects the DAC curve; see spec A.1.
_VARIANT_NAME = {spec.CHIP_TYPE_AY: {spec.AY_VARIANT_AY: "AY", spec.AY_VARIANT_YM: "YM"}}


def _chip_type_name(type_id: int) -> str:
    name = _CHIP_TYPE_NAME.get(type_id)
    if name:
        return name + (" (registry TBD)" if type_id in spec.CHIP_TYPE_REGISTRY else "")
    lo, hi = spec.CHIP_TYPE_STD_RANGE
    if lo <= type_id <= hi:
        return "undefined standardized"
    lo, hi = spec.CHIP_TYPE_PRIVATE_RANGE
    return "private" if lo <= type_id <= hi else "?"


def _variant_name(type_id: int, variant: int) -> str:
    """Variant label, or '' when the family default (nothing to disambiguate)."""
    if variant == spec.CHIP_VARIANT_DEFAULT:
        return ""
    return _VARIANT_NAME.get(type_id, {}).get(variant, f"variant {variant}")


def stats(t: Taym) -> dict:
    nt = len(t.timers)
    # MODS command histogram (overall + per timer).
    hist = {name: 0 for name in _CMD_NAME.values()}
    per_timer = [{name: 0 for name in _CMD_NAME.values()} for _ in range(nt)]
    for i, m in enumerate(t.mods):
        name = _CMD_NAME.get(m.command, f"?{m.command}")
        hist[name] = hist.get(name, 0) + 1
        if nt:
            per_timer[i % nt][name] = per_timer[i % nt].get(name, 0) + 1

    data = write_taym(t)
    return {
        "file_bytes": len(data),
        "frame_rate_hz": t.trak.frame_rate_hz,
        "frame_count": t.trak.frame_count,
        "loop_frame": None if t.trak.loop_frame == spec.NO_LOOP else t.trak.loop_frame,
        "duration_s": t.trak.frame_count / t.trak.frame_rate_hz if t.trak.frame_rate_hz else None,
        "chip_count": len(t.chips),
        "chips": [{"type_id": c.chip_type_id, "type_name": _chip_type_name(c.chip_type_id),
                   "variant": c.variant, "variant_name": _variant_name(c.chip_type_id, c.variant),
                   "name": c.name, "clock_hz": c.clock_hz} for c in t.chips],
        "timer_count": nt,
        "mods_count": len(t.mods),
        "mods_bytes": len(t.mods) * spec.MODS_SIZE,
        "actn_count": len(t.actions),
        "lane_count": len(t.lanes),
        "tlan_count": len(t.tlanes),
        "pool_elems": {"VU08": len(t.vu08), "VU16": len(t.vu16), "VU32": len(t.vu32)},
        "frame_data": {tag: len(p) for tag, p in t.frame_data.items()},
        "command_hist": hist,
        "command_hist_per_timer": per_timer,
        "active_frames_per_timer": _active_frames(t),
        "info": dict(t.info),
    }


def _active_frames(t: Taym) -> list[int]:
    """Frames each timer spends owning targets (START..STOP span), reference
    estimate from the command stream. Quiescent counts as active (still owns)."""
    nt = len(t.timers)
    if not nt or len(t.mods) != t.trak.frame_count * nt:
        return [0] * nt
    out = [0] * nt
    state = [False] * nt
    for frame in range(t.trak.frame_count):
        for ti in range(nt):
            cmd = t.mods[frame * nt + ti].command
            if cmd == spec.CMD_START:
                state[ti] = True
            elif cmd == spec.CMD_STOP:
                state[ti] = False
            if state[ti]:
                out[ti] += 1
    return out


def format_stats(t: Taym) -> str:
    s = stats(t)
    lines = ["TAYM stats", ""]
    lines.append(f"file        {s['file_bytes']} bytes")
    dur = f"{s['duration_s']:.2f}s" if s['duration_s'] else "?"
    lines.append(f"timeline    {s['frame_count']} frames @ {s['frame_rate_hz']:g} Hz ({dur})")
    lines.append(f"loop_frame  {s['loop_frame']}")
    lines.append(f"chips       {s['chip_count']}")

    for ci, c in enumerate(s["chips"]):
        clk = f"{c['clock_hz']} Hz" if c["clock_hz"] else "?"
        nm = f' "{c["name"]}"' if c["name"] else ""
        var = f" [{c['variant_name']}]" if c["variant_name"] else ""
        lines.append(f"  chip {ci}:   {c['type_name']} (#{c['type_id']:#04x}){var}{nm} ({clk})")

    lines.append(f"timers      {s['timer_count']}")
    lines.append("")
    lines.append(f"MODS        {s['mods_count']} records ({s['mods_bytes']} bytes)")

    for name, n in s["command_hist"].items():
        if n:
            lines.append(f"  {name:<9} {n}")

    lines.append(f"ACTN        {s['actn_count']}")
    lines.append(f"LANE        {s['lane_count']}")
    lines.append(f"TLAN        {s['tlan_count']}")

    pe = s["pool_elems"]
    lines.append("")
    lines.append(f"pools       (elements)")
    lines.append(f"  VU08      {pe['VU08']}")
    lines.append(f"  VU16      {pe['VU16']}")
    lines.append(f"  VU32      {pe['VU32']}")

    if s["frame_data"]:
        lines.append(f"frame_data  (bytes)")
        for tag, n in s["frame_data"].items():
            lines.append(f"  {tag:<9} {n}")

    if s["info"]:
        lines.append("")
        lines.append("INFO")
        width = max(len(k) for k in s["info"])
        for k, v in s["info"].items():
            lines.append(f"  {k:<{width}}  {v}")

    if any(s["active_frames_per_timer"]):
        lines.append("")
        lines.append("per-timer   (active frames / commands):")
        for ti, af in enumerate(s["active_frames_per_timer"]):
            h = s["command_hist_per_timer"][ti]
            cmds = ", ".join(f"{k}={v}" for k, v in h.items() if v and k != "EMPTY")
            lines.append(f"  timer {ti}:  {af} {cmds}")

    return "\n".join(lines)
