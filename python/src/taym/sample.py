"""Hand-built minimal valid TAYM file + an annotated byte dump.

Built directly with struct (NOT through the codec we have not written yet), so
this is an independent witness of what the bytes should be -- the thing the
real codec round-trip test will be checked against.

Scenario: example 15.1 two-step PWM, the smallest file that exercises every
once-each chunk and a real START action slice.

  1 AY chip (chip_type 0x0001), no frame-data stream
  1 timer  (CHIP_PERIOD, divider 16) so the timer-lane integers are literal
           periods (semantically honest, vs ABS_RATE_HZ which needs 16.16 Hz)
  1 value lane [15, 0] loop=0  (U8 -> VU08), bound to R8 (amplitude A, 0x08)
  1 timer lane [25, 75] loop=0 (ABSOLUTE periods in VU32)
  2 frames: frame0 = START, frame1 = STOP

build_model() is the canonical source; build() packs the same thing directly
with struct as an independent witness. They must agree byte-for-byte.

build_audio_demo() is a SEPARATE, audible fixture (sustained tone + R8
amplitude PWM, ~1.5s) for ear-testing the engine -- it is NOT byte-checked and
must not be confused with the witness. Render it:
  python3 -m taym sample --audio demo.taym
  python -m taym.engine demo.taym -o demo.wav

Run:
  python -m taym sample           # annotated dump + witness check (via CLI)
"""
from __future__ import annotations

import struct

from . import spec
from .model import Actn, Chip, Lane, Mods, Taym, Timr, Tlan, Trak
from .codec import write_taym


def build_model() -> Taym:
    """The canonical sample as a model object."""
    return Taym(
        trak=Trak(frame_rate_hz=50.0, frame_count=2, loop_frame=spec.NO_LOOP),
        chips=[Chip(clock_hz=1773400, chip_type_id=spec.CHIP_TYPE_AY, name="AY")],
        timers=[Timr(chip_index=0, clock_mode=spec.CLOCK_CHIP_PERIOD, clock_divider=16)],
        mods=[
            Mods(command=spec.CMD_START, base_timer_value=25,
                 timer_lane_ref=0, first_action=0, action_count=1),
            Mods(command=spec.CMD_STOP),
        ],
        actions=[Actn(target_id=0x08, source_mode=spec.SRC_BIND_LANE, operand=0)],
        lanes=[Lane(value_type=spec.VT_U8, value_offset=0, length=2, loop_index=0)],
        tlanes=[Tlan(timing_mode=spec.TM_ABSOLUTE, value_offset=0, length=2, loop_index=0)],
        vu08=[15, 0],
        vu32=[25, 75],
    )


AY_CLOCK = 1773400


def _psg_steady_tone(freq_hz: float, frames: int) -> bytes:
    """A minimal Bulba .psg: a steady AY channel-A tone held for `frames`.

    Sets tone-A period + mixer (tone A on, all else off) on frame 0, then
    repeats. R8 amplitude is LEFT to the timer (the PWM owns it), so the .psg
    writes R8=0 and the active timer overrides it. R13=0xFF = no-write."""
    tp = round(AY_CLOCK / (16 * freq_hz))
    regs = [tp & 0xFF, (tp >> 8) & 0x0F, 0, 0, 0, 0, 0,
            0b111110,                 # R7: tone A enabled (bit0=0), rest disabled
            0, 0, 0, 0, 0, 0xFF]      # R8 amp = 0 (timer drives it)
    out = bytearray(b"PSG\x1a" + bytes(12))
    out.append(0xFF)                  # frame 0: full reg set
    for r in range(14):
        out += bytes((r, regs[r] & 0xFF))
    if frames > 1:
        out += bytes((0xFE, frames - 1))
    out.append(0xFD)
    return bytes(out)


def build_audio_demo(frames: int = 75, pwm_hz: float = 300.0,
                     tone_hz: float = 220.0) -> Taym:
    """An AUDIBLE demo (not the byte-witness): a sustained `tone_hz` square on
    AY channel A with R8 amplitude PWM'd [15,0] at ~`pwm_hz`. The .psg holds the
    tone; one CHIP_PERIOD timer owns R8 and toggles its amplitude, so you hear a
    buzzy/gated note. 75 frames @ 50 Hz = 1.5 s. Render with taymengine.

    PWM rate -> period: rate = clock/(16*period) => period = clock/(16*rate).
    The lane is [15,0] looping, so one full on/off cycle spans two expiries;
    the perceived gating rate is pwm_hz/2."""
    period = max(1, round(AY_CLOCK / (16 * pwm_hz)))
    psg = _psg_steady_tone(tone_hz, frames)
    return Taym(
        trak=Trak(frame_rate_hz=50.0, frame_count=frames, loop_frame=spec.NO_LOOP),
        chips=[Chip(clock_hz=AY_CLOCK, chip_type_id=spec.CHIP_TYPE_AY,
                    name="AY", frame_data_tag="PSG0")],
        timers=[Timr(chip_index=0, clock_mode=spec.CLOCK_CHIP_PERIOD, clock_divider=16)],
        # frame 0 START owns R8; rest EMPTY (PWM keeps running); no STOP so it
        # rings out the whole note.
        mods=[Mods(command=spec.CMD_START, base_timer_value=period,
                   timer_lane_ref=0, first_action=0, action_count=1)]
        + [Mods(command=spec.CMD_EMPTY) for _ in range(frames - 1)],
        actions=[Actn(target_id=0x08, source_mode=spec.SRC_BIND_LANE, operand=0)],
        lanes=[Lane(value_type=spec.VT_U8, value_offset=0, length=2, loop_index=0)],
        tlanes=[Tlan(timing_mode=spec.TM_ABSOLUTE, value_offset=0, length=1, loop_index=0)],
        vu08=[15, 0],                 # amplitude PWM
        vu32=[period],                # single steady PWM period
        frame_data={"PSG0": psg},
    )


def build() -> bytes:
    # --- value pools -----------------------------------------------------
    vu08 = bytes([15, 0])                                  # value lane data
    vu16 = b""
    vu32 = struct.pack("<2I", 25, 75)                      # timer lane data

    # --- LANE[0]: [15,0] loop=0, U8 --------------------------------------
    lane0 = struct.pack(spec.LANE_FMT, 0, 2, 0, spec.VT_U8, b"\0\0\0")
    lane = lane0

    # --- TLAN[0]: [25,75] loop=0, ABSOLUTE -------------------------------
    tlan0 = struct.pack(spec.TLAN_FMT, 0, 2, 0, spec.TM_ABSOLUTE, b"\0\0\0")
    tlan = tlan0

    # --- ACTN[0]: R8 <- BIND_LANE lane 0 ---------------------------------
    actn0 = struct.pack(spec.ACTN_FMT, 0, 0x08, spec.SRC_BIND_LANE)
    actn = actn0

    # --- MODS: frame0 START, frame1 STOP (timer_count=1) -----------------
    # START: base nonzero period, timer_lane_ref=TLAN[0], first_action=0, count=1
    mods0 = struct.pack(spec.MODS_FMT, 25, 0, 0, 1, spec.CMD_START, 0)
    # STOP: other fields zeroed/ignored
    mods1 = struct.pack(spec.MODS_FMT, 0, 0, 0, 0, spec.CMD_STOP, 0)
    mods = mods0 + mods1

    # --- TIMR[0]: CHIP_PERIOD, divider 16, chip 0 ------------------------
    timr0 = struct.pack(spec.TIMR_FMT, 16, 0, spec.CLOCK_CHIP_PERIOD, 0)
    timr = timr0

    # --- CHIP[0]: AY, clock 1773400, no frame data, variant 0 (AY) -------
    chip0 = struct.pack(spec.CHIP_FMT, 1773400, spec.CHIP_TYPE_AY,
                        spec.AY_VARIANT_AY, 0, b"AY", b"\0\0\0\0",
                        spec.CHIP_CONFIG_DEFAULT)
    chip = chip0

    # --- TRAK: 50 Hz, 2 frames, no loop, 1 chip, 1 timer -----------------
    trak = struct.pack(spec.TRAK_FMT, spec.to_fix16(50.0), 2, spec.NO_LOOP, 1, 1, 0)

    def chunk(tag: str, payload: bytes) -> bytes:
        return struct.pack(spec.CHUNK_HEADER_FMT, tag.encode("ascii"), len(payload)) + payload

    chunks = b"".join([
        chunk("TRAK", trak),
        chunk("CHIP", chip),
        chunk("TIMR", timr),
        chunk("MODS", mods),
        chunk("ACTN", actn),
        chunk("LANE", lane),
        chunk("TLAN", tlan),
        chunk("VU08", vu08),
        chunk("VU16", vu16),
        chunk("VU32", vu32),
    ])

    header = struct.pack(spec.HEADER_FMT, spec.MAGIC, spec.VERSION,
                         spec.HEADER_SIZE, 0, len(chunks))
    return header + chunks


# --------------------------------------------------------------------------
# Annotated dump -- walk the bytes we just built and label each field.
# --------------------------------------------------------------------------
def _hex(b: bytes) -> str:
    return " ".join(f"{x:02X}" for x in b)


def _row(off: int, raw: bytes, label: str) -> str:
    return f"  {off:4d}  {_hex(raw):<24}  {label}"


def dump(data: bytes) -> str:
    out = ["TAYM annotated dump  (%d bytes)" % len(data), ""]
    p = 0

    def take(n):
        nonlocal p
        b = data[p:p + n]
        p_was = p
        p += n
        return p_was, b

    out.append("FILE HEADER (16):")
    o, b = take(4); out.append(_row(o, b, f"magic {b!r}"))
    o, b = take(2); out.append(_row(o, b, f"version {struct.unpack('<H', b)[0]}"))
    o, b = take(2); out.append(_row(o, b, f"header_size {struct.unpack('<H', b)[0]}"))
    o, b = take(4); out.append(_row(o, b, f"flags {struct.unpack('<I', b)[0]}"))
    o, b = take(4); out.append(_row(o, b, f"chunk_bytes {struct.unpack('<I', b)[0]}"))
    out.append("")

    record_strides = {
        "TRAK": spec.TRAK_SIZE, "CHIP": spec.CHIP_SIZE, "TIMR": spec.TIMR_SIZE,
        "MODS": spec.MODS_SIZE, "ACTN": spec.ACTN_SIZE, "LANE": spec.LANE_SIZE,
        "TLAN": spec.TLAN_SIZE,
    }
    while p < len(data):
        o, b = take(8)
        tag, size = struct.unpack(spec.CHUNK_HEADER_FMT, b)
        tag = tag.decode("ascii")
        out.append(f"CHUNK {tag}  size={size}")
        out.append(_row(o, b, f"chunk header tag={tag} size={size}"))
        stride = record_strides.get(tag)
        end = p + size
        if stride and size and size % stride == 0:
            idx = 0
            while p < end:
                o, rb = take(stride)
                out.append(_row(o, rb, f"{tag}[{idx}]"))
                idx += 1
        else:
            o, rb = take(size)
            if rb:
                out.append(_row(o, rb, "(pool/payload bytes)"))
        out.append("")

    out.append(f"END at {p} (header_size + chunk_bytes = "
               f"{spec.HEADER_SIZE + struct.unpack('<I', data[12:16])[0]})")
    return "\n".join(out)


# build() (hand-packed witness) and build_model()+write_taym must agree; that
# round-trip is asserted in the test suite. The annotated dump() is exposed via
# `python -m taym sample` (structural dump of the canonical sample).
