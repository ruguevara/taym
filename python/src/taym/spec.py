"""TAYM draft 0.1 -- single source of truth for the on-disk format.

Mirrors docs/TAYM-format-draft-0.1.md. Every constant, struct format, enum
and sentinel a reader/writer needs lives here; codec.py and validate.py import
from this module and nowhere else hard-code an offset or width. If the spec
draft changes, this file is the one to edit.

All multibyte fields are little-endian (struct '<'). Sizes in bytes.
"""
from __future__ import annotations

import struct

# --------------------------------------------------------------------------
# File header (S2) and chunk container (S3)
# --------------------------------------------------------------------------
MAGIC = b"TAYM"                 # 54 41 59 4D
VERSION = 1
HEADER_SIZE = 16

# u16 version, u16 header_size, u32 flags, u32 chunk_bytes (magic read separately)
HEADER_FMT = "<4sHHII"
assert struct.calcsize(HEADER_FMT) == HEADER_SIZE

CHUNK_HEADER_FMT = "<4sI"       # tag, payload size (excludes this 8-byte header)
CHUNK_HEADER_SIZE = struct.calcsize(CHUNK_HEADER_FMT)
assert CHUNK_HEADER_SIZE == 8

# Canonical writer chunk order (S3). Frame-data + extension chunks trail these.
CHUNK_ORDER = (
    "TRAK", "INFO", "CHIP", "TIMR", "MODS", "ACTN", "LANE", "TLAN",
    "VU08", "VU16", "VU32",
)
# Occur exactly once (S3); INFO is at-most-once and handled separately.
CORE_ONCE = ("TRAK", "CHIP", "TIMR", "MODS", "ACTN", "LANE", "TLAN",
             "VU08", "VU16", "VU32")

# --------------------------------------------------------------------------
# Sentinels (S1.1, S12) -- operations, NOT enum values.
# --------------------------------------------------------------------------
NO_LOOP = 0xFFFFFFFF            # loop_frame / loop_index "no loop"
TLAN_NONE = 0xFFFFFFFF          # timer_lane_ref: no timer lane, use base
TLAN_UNCHANGED = 0xFFFFFFFE     # timer_lane_ref: keep current (MODULATE only)

# --------------------------------------------------------------------------
# Enumerations (S1.1). Each is closed; readers reject unlisted values.
# --------------------------------------------------------------------------
# clock_mode (TIMR.clock_mode u8, S7)
CLOCK_ABS_RATE_HZ = 0
CLOCK_CHIP_PERIOD = 1
CLOCK_MODES = (CLOCK_ABS_RATE_HZ, CLOCK_CHIP_PERIOD)

# value_type (LANE.value_type u8, S9) -- 0 invalid, 4..255 reserved
VT_INVALID = 0
VT_U8 = 1
VT_U16 = 2
VT_U32 = 3
VALUE_TYPES = (VT_U8, VT_U16, VT_U32)
# value_type -> (pool tag, element struct char, element byte width)
VALUE_TYPE_POOL = {VT_U8: ("VU08", "B", 1), VT_U16: ("VU16", "H", 2), VT_U32: ("VU32", "I", 4)}

# timing_mode (TLAN.timing_mode u8, S10)
TM_ABSOLUTE = 0
TM_RELATIVE = 1
TIMING_MODES = (TM_ABSOLUTE, TM_RELATIVE)

# source_mode (ACTN.source_mode u8, S11)
SRC_INLINE_VALUE = 0
SRC_BIND_LANE = 1
SOURCE_MODES = (SRC_INLINE_VALUE, SRC_BIND_LANE)

# command (MODS.command u8, S12)
CMD_EMPTY = 0
CMD_START = 1
CMD_MODULATE = 2
CMD_STOP = 3
COMMANDS = (CMD_EMPTY, CMD_START, CMD_MODULATE, CMD_STOP)

# --------------------------------------------------------------------------
# Chip / target registry (appendix A, AY = normative for draft 0.1)
# --------------------------------------------------------------------------
CHIP_TYPE_INVALID = 0x00
CHIP_TYPE_AY = 0x01
CHIP_TYPE_STD_RANGE = (0x01, 0x7F)      # standardized (u8)
CHIP_TYPE_PRIVATE_RANGE = (0x80, 0xFF)  # private/experimental (u8)

# Standardized chip_type_ids assigned so far (appendix A.1). Only AY (0x0001) is
# fully defined (registry A.2/A.3); these have a name but no register registry
# yet (status A.4). Sound-distinct revisions (SID 6581/8580, NES NTSC/PAL) share
# an id and use CHIP.variant, as AY/YM do. FM chips with an AY-style SSG block
# (YM2203 etc.) take one whole id covering FM+SSG; no separate 0x0001 record.
CHIP_TYPE_REGISTRY = {
    # PSG family
    0x0002: "SN76489 (TI DCSG; SMS / BBC / ColecoVision)",
    0x0003: "SAA1099 (Philips; Sam Coupe)",
    # console custom
    0x0004: "SID (MOS 6581/8580; revision = variant)",
    0x0005: "POKEY (Atari 8-bit / arcade)",
    0x0006: "NES APU (RP2A03/2A07; NTSC/PAL = variant)",
    0x0007: "Game Boy APU (DMG/CGB)",
    0x0008: "HuC6280 (PC Engine / TurboGrafx)",
    # Yamaha FM
    0x0009: "YM2612 (OPN2; Sega Genesis FM)",
    0x000A: "YM2151 (OPM; arcade / X68000)",
    0x000B: "YM2203 (OPN; FM + AY-style SSG)",
    0x000C: "YM2413 (OPLL)",
    0x000D: "YMF262 (OPL3; AdLib / Sound Blaster)",
}

# CHIP.variant (appendix A.1): one chip_type_id, per-instance behavioral pick.
# For AY (0x0001): selects the DAC amplitude table (AY symmetric vs YM
# asymmetric). 0 = the family default. Other types define their own meaning.
CHIP_VARIANT_DEFAULT = 0x00
AY_VARIANT_AY = 0x00            # AY-3-8910/8912 DAC curve (default)
AY_VARIANT_YM = 0x01           # YM2149 DAC curve

# CHIP.config (S6, u32 @ off 28): chip-type-private config bitfield, defined per
# chip_type_id in the registry. 0 = family default. Each type owns its own bits;
# unused/undefined bits are reserved zero. A consumer that does not recognize a
# field renders the family default.
CHIP_CONFIG_DEFAULT = 0x00000000

# AY (0x0001) config layout (appendix A.1):
#   bits 0..2  stereo layout (channels are tone A/B/C; names give left/right;
#              MONO sums all three to both); values 0..7 all defined.
#   bits 3..31 reserved zero.
AY_CFG_STEREO_MASK = 0x00000007
AY_LAYOUT_MONO = 0x00          # default: A+B+C to both (linear sum)
AY_LAYOUT_ABC = 0x01           # left=A, center=B, right=C
AY_LAYOUT_ACB = 0x02           # left=A, center=C, right=B
AY_LAYOUT_BAC = 0x03           # left=B, center=A, right=C
AY_LAYOUT_BCA = 0x04           # left=B, center=C, right=A
AY_LAYOUT_CAB = 0x05           # left=C, center=A, right=B
AY_LAYOUT_CBA = 0x06           # left=C, center=B, right=A
AY_LAYOUT_ST_MONO = 0x07       # Atari ST combined-DAC mono (non-linear 3-voice
                               # mix; not a pan -- a distinct mixing model)
AY_LAYOUTS = (AY_LAYOUT_MONO, AY_LAYOUT_ABC, AY_LAYOUT_ACB, AY_LAYOUT_BAC,
              AY_LAYOUT_BCA, AY_LAYOUT_CAB, AY_LAYOUT_CBA, AY_LAYOUT_ST_MONO)


def ay_stereo_layout(config: int) -> int:
    """Extract the AY stereo-layout field (bits 0..2) from CHIP.config."""
    return config & AY_CFG_STEREO_MASK

# target_id ranges (S11)
TGT_HW_RANGE = (0x00, 0x7F)         # real chip registers
TGT_FMT_VIRTUAL_RANGE = (0x80, 0xBF)
TGT_ENGINE_RANGE = (0xC0, 0xFF)     # registry-assigned, or private chip type
# format-specified virtual target (chip-independent). 0x80 carries the
# *unquantized, linear* sample amplitude on a lane (U8 or U16 -- unit is
# producer/platform chosen; the format defines only the semantics). Volume is
# the paired AY amplitude register; the engine combines amplitude x volume and
# requantizes to the chip DAC code once, at the DAC boundary (S11.1).
TGT_SAMPLE_AMPLITUDE = 0x80
TGT_FMT_VIRTUAL_DEFINED = (0x80,)    # 0x81..0xBF reserved -> invalid

# AY hardware targets R0..R13 = 0x00..0x0D; 0x0E..0x7F unassigned/invalid (A.2)
AY_TARGET_MAX = 0x0D
AY_R13_SHAPE = 0x0D                  # write-sensitive: every write retriggers
AY_AMP_REGS = (0x08, 0x09, 0x0A)     # R8/R9/R10: 0x80's volume + output reg (S11.1)

# --------------------------------------------------------------------------
# Record struct formats + strides. Each '<...' packs one record exactly.
# Trailing reserved bytes are explicit in the format so size == spec stride.
# --------------------------------------------------------------------------
TRAK_FMT = "<IIIBBH"            # frame_rate, frame_count, loop_frame, chip_count, timer_count, resv
TRAK_SIZE = struct.calcsize(TRAK_FMT)

CHIP_FMT = "<IBBH16s4sI"       # clock_hz, chip_type_id(u8), variant, resv(u16), name, frame_data_tag, config(u32)
CHIP_SIZE = struct.calcsize(CHIP_FMT)

TIMR_FMT = "<HBBH"             # clock_divider, chip_index, clock_mode, resv
TIMR_SIZE = struct.calcsize(TIMR_FMT)

MODS_FMT = "<IIIBBH"          # base_timer_value, timer_lane_ref, first_action, action_count, command, resv
MODS_SIZE = struct.calcsize(MODS_FMT)

ACTN_FMT = "<IBB"             # operand, target_id, source_mode
ACTN_SIZE = struct.calcsize(ACTN_FMT)

LANE_FMT = "<IIIB3s"         # value_offset, length, loop_index, value_type, resv(3)
LANE_SIZE = struct.calcsize(LANE_FMT)

TLAN_FMT = "<IIIB3s"         # value_offset, length, loop_index, timing_mode, resv(3)
TLAN_SIZE = struct.calcsize(TLAN_FMT)

# Spec-stated strides (S sections) -- assert our formats match.
assert (TRAK_SIZE, CHIP_SIZE, TIMR_SIZE, MODS_SIZE, ACTN_SIZE, LANE_SIZE, TLAN_SIZE) \
    == (16, 32, 6, 16, 6, 16, 16), (TRAK_SIZE, CHIP_SIZE, TIMR_SIZE, MODS_SIZE, ACTN_SIZE, LANE_SIZE, TLAN_SIZE)

# 16.16 fixed point (S1)
FIX16_ONE = 65536
FIX16_MAX = 0xFFFFFFFF          # just under 65536 Hz


def to_fix16(value: float) -> int:
    """value -> unsigned 16.16 encoded u32 (round). Caller checks range."""
    return round(value * FIX16_ONE)


def from_fix16(encoded: int) -> float:
    return encoded / FIX16_ONE


def fits_fix16(value: float) -> bool:
    enc = to_fix16(value)
    return 0 <= enc <= FIX16_MAX


def fix16_product_fits(lhs_encoded: int, rhs_encoded: int) -> bool:
    """Whether two unsigned 16.16 values multiply to a representable 16.16."""
    return 0 <= lhs_encoded and 0 <= rhs_encoded \
        and lhs_encoded * rhs_encoded <= FIX16_MAX * FIX16_ONE
