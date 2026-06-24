"""TAYM read/write -- the only module that touches struct layout besides spec.

write_taym(taym) -> bytes : canonical-order, byte-packed, recomputed counts.
read_taym(data)  -> Taym  : structural parse only (no semantic validation;
                            that is validate.py). Raises CodecError on
                            structurally impossible input.

The writer is canonical: chunk order is spec.CHUNK_ORDER then frame-data
chunks (CHIP order), counts/sizes derived from the model. Round-tripping a
canonical file reproduces it byte-for-byte.
"""
from __future__ import annotations

import struct

from . import spec
from .model import Actn, Chip, Lane, Mods, Taym, Timr, Tlan, Trak


class CodecError(Exception):
    pass


# --------------------------------------------------------------------------
# Write
# --------------------------------------------------------------------------
def _chunk(tag: str, payload: bytes) -> bytes:
    return struct.pack(spec.CHUNK_HEADER_FMT, tag.encode("ascii"), len(payload)) + payload


def _tag4(s: str) -> bytes:
    b = s.encode("ascii")
    if len(b) != 4:
        raise CodecError(f"chunk tag must be 4 ASCII chars: {s!r}")
    return b


def _pack_trak(t: Trak, chip_count: int, timer_count: int) -> bytes:
    return struct.pack(spec.TRAK_FMT, spec.to_fix16(t.frame_rate_hz),
                       t.frame_count, t.loop_frame, chip_count, timer_count, 0)


def _pack_chip(c: Chip) -> bytes:
    name = c.name.encode("ascii")[:16]
    tag = c.frame_data_tag.encode("ascii") if c.frame_data_tag else b"\0\0\0\0"
    if len(tag) != 4:
        raise CodecError(f"frame_data_tag must be 4 chars or empty: {c.frame_data_tag!r}")
    return struct.pack(spec.CHIP_FMT, c.clock_hz, c.chip_type_id, c.variant, 0,
                       name, tag, c.config)


def _pack_timr(t: Timr) -> bytes:
    return struct.pack(spec.TIMR_FMT, t.clock_divider, t.chip_index, t.clock_mode, 0)


def _pack_mods(m: Mods) -> bytes:
    # S12.1/12.4: EMPTY and STOP carry no indices; a canonical writer zeroes
    # the four interpreted fields so a reader never mis-reads them as TLAN[0]/etc.
    if m.command in (spec.CMD_EMPTY, spec.CMD_STOP):
        return struct.pack(spec.MODS_FMT, 0, 0, 0, 0, m.command, 0)
    return struct.pack(spec.MODS_FMT, m.base_timer_value, m.timer_lane_ref,
                       m.first_action, m.action_count, m.command, 0)


def _pack_actn(a: Actn) -> bytes:
    return struct.pack(spec.ACTN_FMT, a.operand, a.target_id, a.source_mode)


def _pack_lane(l: Lane) -> bytes:
    return struct.pack(spec.LANE_FMT, l.value_offset, l.length, l.loop_index,
                       l.value_type, b"\0\0\0")


def _pack_tlan(t: Tlan) -> bytes:
    return struct.pack(spec.TLAN_FMT, t.value_offset, t.length, t.loop_index,
                       t.timing_mode, b"\0\0\0")


def _pack_info(info: dict[str, str]) -> bytes:
    if not info:
        return b""
    body = b"".join(f"{k}={v}".encode("utf-8") + b"\0" for k, v in info.items())
    return body + b"\0"


def write_taym(t: Taym) -> bytes:
    payloads: list[tuple[str, bytes]] = [
        ("TRAK", _pack_trak(t.trak, len(t.chips), len(t.timers))),
    ]
    if t.info:
        payloads.append(("INFO", _pack_info(t.info)))
    payloads += [
        ("CHIP", b"".join(_pack_chip(c) for c in t.chips)),
        ("TIMR", b"".join(_pack_timr(x) for x in t.timers)),
        ("MODS", b"".join(_pack_mods(m) for m in t.mods)),
        ("ACTN", b"".join(_pack_actn(a) for a in t.actions)),
        ("LANE", b"".join(_pack_lane(x) for x in t.lanes)),
        ("TLAN", b"".join(_pack_tlan(x) for x in t.tlanes)),
        ("VU08", bytes(t.vu08)),
        ("VU16", struct.pack(f"<{len(t.vu16)}H", *t.vu16)),
        ("VU32", struct.pack(f"<{len(t.vu32)}I", *t.vu32)),
    ]
    # Frame-data chunks trail the core chunks, in chip order.
    for c in t.chips:
        if c.frame_data_tag:
            tag = c.frame_data_tag
            if tag not in t.frame_data:
                raise CodecError(f"chip references frame_data_tag {tag!r} with no payload")
            payloads.append((tag, t.frame_data[tag]))

    chunks = b"".join(_chunk(tag, p) for tag, p in payloads)
    header = struct.pack(spec.HEADER_FMT, spec.MAGIC, spec.VERSION,
                         spec.HEADER_SIZE, t.flags, len(chunks))
    return header + chunks


# --------------------------------------------------------------------------
# Read
# --------------------------------------------------------------------------
def _split_chunks(data: bytes) -> dict[str, bytes]:
    """Walk chunk headers -> {tag: payload}. Structural checks only."""
    if len(data) < spec.HEADER_SIZE:
        raise CodecError("file shorter than header")
    magic, version, hsize, flags, chunk_bytes = struct.unpack(
        spec.HEADER_FMT, data[:spec.HEADER_SIZE])
    if magic != spec.MAGIC:
        raise CodecError(f"bad magic {magic!r}")
    if hsize != spec.HEADER_SIZE:
        raise CodecError(f"bad header_size {hsize}")
    end = hsize + chunk_bytes
    if end != len(data):
        raise CodecError(f"chunk_bytes says file ends at {end}, got {len(data)}")
    chunks: dict[str, bytes] = {}
    p = hsize
    while p < end:
        if p + spec.CHUNK_HEADER_SIZE > end:
            raise CodecError(f"truncated chunk header at {p}")
        tag, size = struct.unpack(spec.CHUNK_HEADER_FMT, data[p:p + spec.CHUNK_HEADER_SIZE])
        tag = tag.decode("ascii", "replace")
        p += spec.CHUNK_HEADER_SIZE
        if p + size > end:
            raise CodecError(f"chunk {tag!r} payload runs past end")
        if tag in chunks:
            raise CodecError(f"duplicate chunk tag {tag!r}")
        chunks[tag] = data[p:p + size]
        p += size
    return chunks, version, flags


def _records(payload: bytes, stride: int, tag: str):
    if len(payload) % stride:
        raise CodecError(f"{tag} size {len(payload)} not a multiple of stride {stride}")
    for off in range(0, len(payload), stride):
        yield payload[off:off + stride]


def _parse_info(payload: bytes) -> dict[str, str]:
    info: dict[str, str] = {}
    if not payload:
        return info
    text = payload.rstrip(b"\0").decode("utf-8")
    for entry in text.split("\0"):
        if not entry:
            continue
        k, _, v = entry.partition("=")
        info[k] = v
    return info


def read_taym(data: bytes) -> Taym:
    chunks, version, flags = _split_chunks(data)

    def need(tag):
        if tag not in chunks:
            raise CodecError(f"missing core chunk {tag!r}")
        return chunks[tag]

    fr, fc, lf, chip_count, timer_count, _ = struct.unpack(spec.TRAK_FMT, need("TRAK"))
    trak = Trak(frame_rate_hz=spec.from_fix16(fr), frame_count=fc, loop_frame=lf)

    chips = []
    for rec in _records(need("CHIP"), spec.CHIP_SIZE, "CHIP"):
        clock_hz, type_id, variant, _, name, tag, config = struct.unpack(spec.CHIP_FMT, rec)
        chips.append(Chip(
            clock_hz=clock_hz, chip_type_id=type_id,
            name=name.split(b"\0", 1)[0].decode("ascii", "replace"),
            frame_data_tag="" if tag == b"\0\0\0\0" else tag.decode("ascii", "replace"),
            variant=variant, config=config,
        ))

    timers = []
    for rec in _records(need("TIMR"), spec.TIMR_SIZE, "TIMR"):
        div, chip_index, mode, _ = struct.unpack(spec.TIMR_FMT, rec)
        timers.append(Timr(chip_index=chip_index, clock_mode=mode, clock_divider=div))

    mods = []
    for rec in _records(need("MODS"), spec.MODS_SIZE, "MODS"):
        base, tlref, fa, ac, cmd, _ = struct.unpack(spec.MODS_FMT, rec)
        mods.append(Mods(command=cmd, base_timer_value=base, timer_lane_ref=tlref,
                         first_action=fa, action_count=ac))

    actions = []
    for rec in _records(need("ACTN"), spec.ACTN_SIZE, "ACTN"):
        operand, tid, smode = struct.unpack(spec.ACTN_FMT, rec)
        actions.append(Actn(target_id=tid, source_mode=smode, operand=operand))

    lanes = []
    for rec in _records(need("LANE"), spec.LANE_SIZE, "LANE"):
        vo, ln, li, vt, _ = struct.unpack(spec.LANE_FMT, rec)
        lanes.append(Lane(value_type=vt, value_offset=vo, length=ln, loop_index=li))

    tlanes = []
    for rec in _records(need("TLAN"), spec.TLAN_SIZE, "TLAN"):
        vo, ln, li, tm, _ = struct.unpack(spec.TLAN_FMT, rec)
        tlanes.append(Tlan(timing_mode=tm, value_offset=vo, length=ln, loop_index=li))

    vu08 = list(need("VU08"))
    p16 = need("VU16")
    if len(p16) % 2:
        raise CodecError("VU16 size not a multiple of 2")
    vu16 = list(struct.unpack(f"<{len(p16) // 2}H", p16))
    p32 = need("VU32")
    if len(p32) % 4:
        raise CodecError("VU32 size not a multiple of 4")
    vu32 = list(struct.unpack(f"<{len(p32) // 4}I", p32))

    info = _parse_info(chunks.get("INFO", b""))

    core = set(spec.CORE_ONCE) | {"INFO"}
    frame_data = {tag: payload for tag, payload in chunks.items() if tag not in core}

    return Taym(trak=trak, chips=chips, timers=timers, mods=mods, actions=actions,
                lanes=lanes, tlanes=tlanes, vu08=vu08, vu16=vu16, vu32=vu32,
                info=info, frame_data=frame_data, flags=flags)
