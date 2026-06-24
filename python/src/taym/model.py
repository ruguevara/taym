"""TAYM in-memory model -- plain dataclasses, one per chunk record.

Hand-constructible (converters build these directly) and round-trippable. No
codec logic here; codec.py packs/unpacks these against spec.py. Field names and
order mirror the spec record tables. Sentinels (NO_LOOP, TLAN_NONE,
TLAN_UNCHANGED) are stored as their raw u32 values; helpers in spec interpret.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import spec


@dataclass
class Trak:
    frame_rate_hz: float            # decoded from 16.16
    frame_count: int
    loop_frame: int = spec.NO_LOOP  # frame index or NO_LOOP
    # chip_count / timer_count are derived from the arrays on write.


@dataclass
class Chip:
    clock_hz: int
    chip_type_id: int = spec.CHIP_TYPE_AY
    name: str = ""
    frame_data_tag: str = ""        # "" = no frame data; else 4-char chunk tag
    variant: int = spec.CHIP_VARIANT_DEFAULT  # behavioral pick within type (A.1); AY: 0=AY,1=YM
    config: int = spec.CHIP_CONFIG_DEFAULT    # chip-type-private u32 bitfield (S6); AY: bits0..2 stereo


@dataclass
class Timr:
    chip_index: int
    clock_mode: int = spec.CLOCK_ABS_RATE_HZ
    clock_divider: int = 0


@dataclass
class Actn:
    target_id: int
    source_mode: int                # SRC_INLINE_VALUE | SRC_BIND_LANE
    operand: int                    # inline scalar or LANE index


@dataclass
class Lane:
    value_type: int                 # VT_U8 | VT_U16 | VT_U32
    value_offset: int               # element index into the matching pool
    length: int
    loop_index: int = spec.NO_LOOP  # lane-relative or NO_LOOP


@dataclass
class Tlan:
    timing_mode: int                # TM_ABSOLUTE | TM_RELATIVE
    value_offset: int               # element index into VU32
    length: int
    loop_index: int = spec.NO_LOOP


@dataclass
class Mods:
    command: int                    # CMD_EMPTY | START | MODULATE | STOP
    base_timer_value: int = 0
    timer_lane_ref: int = spec.TLAN_NONE
    first_action: int = 0
    action_count: int = 0


@dataclass
class Taym:
    """Whole file. MODS is row-major (frame_count x timer_count) flattened,
    index = frame*timer_count + timer (S12). Pools are plain int lists."""
    trak: Trak
    chips: list[Chip] = field(default_factory=list)
    timers: list[Timr] = field(default_factory=list)
    mods: list[Mods] = field(default_factory=list)
    actions: list[Actn] = field(default_factory=list)
    lanes: list[Lane] = field(default_factory=list)
    tlanes: list[Tlan] = field(default_factory=list)
    vu08: list[int] = field(default_factory=list)
    vu16: list[int] = field(default_factory=list)
    vu32: list[int] = field(default_factory=list)
    info: dict[str, str] = field(default_factory=dict)
    frame_data: dict[str, bytes] = field(default_factory=dict)  # tag -> .psg bytes
    flags: int = 0

    def pool_for(self, value_type: int) -> list[int]:
        return {spec.VT_U8: self.vu08, spec.VT_U16: self.vu16,
                spec.VT_U32: self.vu32}[value_type]
