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


def _psg_from_deltas(frames: list) -> bytes:
    """A Bulba .psg from per-frame {register: value} deltas, one dict per frame."""
    out = bytearray(b"PSG\x1a" + bytes(12))
    for delta in frames:
        out.append(0xFF)
        for reg, value in sorted(delta.items()):
            out += bytes((reg, value & 0xFF))
    out.append(0xFD)
    return bytes(out)


def build_engine_fixture() -> Taym:
    """A valid file that walks the engine through its rules, for diffing one
    engine's write timeline against another's (`cd c && make test`). Not meant
    to sound like anything.

    Two timers on one chip over 14 frames, with a PSG background that keeps
    changing under them, and that repeats an envelope shape (R13) where no
    timer holds it -- a write the engine must keep (S6.2):

      T0 (CHIP_PERIOD)  PWM on R8; MODULATE of the base, lane -> inline,
                        inline -> lane (pending element 0), lane -> lane (phase
                        kept); STOP back to the background; an R13 buzzer; a
                        timer lane whose multiplier drops to zero (no rate).
      T1 (ABS_RATE_HZ)  a U16 0x80 sample amplitude paired with R9 on a
                        RELATIVE timer lane; timer-lane swap and removal; a
                        no-loop ABSOLUTE timer lane (quiescence, S10.2); taking
                        R13 from T0 in the same frame (S13.2); a one-shot value
                        lane (dormancy, S9.1).
    """
    hz = spec.to_fix16
    empty = Mods(command=spec.CMD_EMPTY)
    stop = Mods(command=spec.CMD_STOP)

    def start(base, tlan, first, count):
        return Mods(command=spec.CMD_START, base_timer_value=base,
                    timer_lane_ref=tlan, first_action=first, action_count=count)

    def modulate(base=0, tlan=spec.TLAN_UNCHANGED, first=0, count=0):
        return Mods(command=spec.CMD_MODULATE, base_timer_value=base,
                    timer_lane_ref=tlan, first_action=first, action_count=count)

    none = spec.TLAN_NONE
    # One (T0, T1) pair per frame.
    frames = [
        (start(222, none, 0, 1),        empty),                         # 0  PWM on R8
        (empty,                         start(hz(300.37), 0, 3, 2)),    # 1  sample, relative lane
        (modulate(base=180),            empty),                         # 2  new base
        (modulate(first=1, count=1),    modulate(tlan=3)),              # 3  lane -> inline; lane swap
        (modulate(first=2, count=1),    modulate(tlan=none)),           # 4  inline -> lane; lane removed
        (modulate(first=0, count=1),    modulate(base=hz(450.0))),      # 5  lane -> lane; new base
        (stop,                          start(hz(500.0), 1, 5, 1)),     # 6  release R8; quiescence
        (start(60, none, 6, 1),         empty),                         # 7  R13 buzzer
        (empty,                         stop),                          # 8
        (stop,                          start(hz(2000.0), none, 7, 2)), # 9  R13 changes hands
        (start(222, 2, 0, 1),           empty),                         # 10 rate drops to zero
        (empty,                         start(hz(1000.0), none, 5, 1)), # 11 one-shot value lane
        (stop,                          empty),                         # 12
        (empty,                         stop),                          # 13
    ]
    psg = _psg_from_deltas([
        {0: 0xFC, 1: 0x01, 2: 0x7E, 3: 0x00, 4: 0x40, 5: 0x00, 6: 0x05,
         7: 0b111000, 8: 10, 9: 11, 10: 12, 11: 0x20, 12: 0x00},
        {0: 0xF0},
        {8: 13},                    # under T0: held back until the STOP
        {11: 0x30, 13: 0x0A},       # a real envelope-shape write
        {2: 0x70},
        {13: 0x0A},                 # the same shape again: a retrigger, kept
        {8: 14},                    # the frame T0 releases R8
        {0: 0xE0},
        {9: 5},
        {},
        {10: 3},
        {},                         # T1 releases R13: the background's 0x0A back
        {1: 0x02, 13: 0x0A},        # and the stream repeats it a frame later
        {8: 0},
    ])
    return Taym(
        trak=Trak(frame_rate_hz=50.0, frame_count=len(frames), loop_frame=spec.NO_LOOP),
        chips=[Chip(clock_hz=AY_CLOCK, chip_type_id=spec.CHIP_TYPE_AY,
                    name="AY", frame_data_tag="PSG0")],
        timers=[Timr(chip_index=0, clock_mode=spec.CLOCK_CHIP_PERIOD, clock_divider=16),
                Timr(chip_index=0, clock_mode=spec.CLOCK_ABS_RATE_HZ)],
        mods=[m for pair in frames for m in pair],
        actions=[
            Actn(target_id=0x08, source_mode=spec.SRC_BIND_LANE, operand=0),       # 0
            Actn(target_id=0x08, source_mode=spec.SRC_INLINE_VALUE, operand=7),    # 1
            Actn(target_id=0x08, source_mode=spec.SRC_BIND_LANE, operand=1),       # 2
            Actn(target_id=0x09, source_mode=spec.SRC_INLINE_VALUE, operand=12),   # 3
            Actn(target_id=spec.TGT_SAMPLE_AMPLITUDE,
                 source_mode=spec.SRC_BIND_LANE, operand=2),                       # 4
            Actn(target_id=0x0A, source_mode=spec.SRC_BIND_LANE, operand=3),       # 5
            Actn(target_id=0x0D, source_mode=spec.SRC_INLINE_VALUE, operand=0x0C), # 6
            Actn(target_id=0x0B, source_mode=spec.SRC_INLINE_VALUE, operand=8),    # 7
            Actn(target_id=0x0D, source_mode=spec.SRC_INLINE_VALUE, operand=0x0E), # 8
        ],
        lanes=[
            Lane(value_type=spec.VT_U8, value_offset=0, length=2, loop_index=0),   # 0 [15, 9]
            Lane(value_type=spec.VT_U8, value_offset=2, length=2, loop_index=0),   # 1 [3, 0]
            Lane(value_type=spec.VT_U16, value_offset=0, length=6, loop_index=1),  # 2 amplitude
            Lane(value_type=spec.VT_U8, value_offset=4, length=4),                 # 3 one-shot
        ],
        tlanes=[
            Tlan(timing_mode=spec.TM_RELATIVE, value_offset=0, length=3, loop_index=0),  # 0
            Tlan(timing_mode=spec.TM_ABSOLUTE, value_offset=3, length=3),                # 1 no loop
            Tlan(timing_mode=spec.TM_RELATIVE, value_offset=6, length=2, loop_index=1),  # 2 -> x0
            Tlan(timing_mode=spec.TM_RELATIVE, value_offset=8, length=3, loop_index=0),  # 3
        ],
        vu08=[15, 9, 3, 0, 12, 8, 4, 0],
        vu16=[0, 16384, 32768, 49152, 65535, 32768],
        vu32=[hz(1.0), hz(2.0), hz(0.5),
              hz(400.0), hz(800.0), hz(600.0),
              hz(1.0), 0,
              hz(0.5), hz(1.0), hz(2.0)],
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
