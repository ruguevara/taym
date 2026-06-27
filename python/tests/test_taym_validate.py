"""TAYM section-14 validation tests (pytest).

Each case takes the canonical valid sample, breaks exactly one rule, and
asserts validate() reports it (matching a section tag substring). The clean
sample must report nothing.
"""
import copy
import struct

from taym import spec, validate, validate_bytes, write_taym
from taym.model import Actn, Chip, Lane, Mods, Timr, Tlan
from taym.sample import build_model


def fresh():
    return copy.deepcopy(build_model())


def has(problems, frag):
    assert any(frag in s for s in problems), f"expected {frag!r} in {problems}"


def test_clean_sample_valid():
    assert validate(fresh()) == []
    assert validate_bytes(write_taym(fresh())) == []


def test_zero_frame_count():
    m = fresh(); m.trak.frame_count = 0; m.mods = []
    has(validate(m), "frame_count is zero")


def test_zero_frame_rate():
    m = fresh(); m.trak.frame_rate_hz = 0.0
    has(validate(m), "frame_rate")


def test_loop_frame_out_of_range():
    m = fresh(); m.trak.loop_frame = 5
    has(validate(m), "loop_frame")


def test_mods_count_mismatch():
    m = fresh(); m.mods = m.mods[:1]
    has(validate(m), "MODS has")


def test_chip_type_invalid():
    m = fresh(); m.chips[0].chip_type_id = 0x00
    has(validate(m), "0x00 is invalid")


def test_chip_type_undefined_standardized():
    m = fresh(); m.chips[0].chip_type_id = 0x02
    has(validate(m), "undefined in draft 0.1")


def test_ay_stereo_layout_valid_roundtrips():
    m = fresh(); m.chips[0].config = spec.AY_LAYOUT_ACB
    assert validate(m) == []
    from taym import read_taym
    assert read_taym(write_taym(m)).chips[0].config == spec.AY_LAYOUT_ACB


def test_ay_stereo_layout_st_mono_valid():
    m = fresh(); m.chips[0].config = spec.AY_LAYOUT_ST_MONO  # 7 = ST_MONO
    assert validate(m) == []


def test_ay_config_reserved_bits():
    m = fresh(); m.chips[0].config = 0x100  # a bit above the stereo field
    has(validate(m), "reserved bits")


def test_repeated_frame_data_tag():
    m = fresh()
    m.chips = [Chip(clock_hz=1, frame_data_tag="PSG0"),
               Chip(clock_hz=1, frame_data_tag="PSG0")]
    m.frame_data = {"PSG0": b"PSG\x1a" + b"\0" * 12}
    m.timers[0].chip_index = 0
    has(validate(m), "repeated frame_data_tag")


def test_abs_rate_with_divider():
    m = fresh()
    m.timers[0] = Timr(chip_index=0, clock_mode=spec.CLOCK_ABS_RATE_HZ, clock_divider=4)
    has(validate(m), "ABS_RATE_HZ requires clock_divider==0")


def test_chip_period_zero_divider():
    m = fresh(); m.timers[0].clock_divider = 0
    has(validate(m), "CHIP_PERIOD requires nonzero")


def test_chip_period_zero_clock():
    m = fresh(); m.chips[0].clock_hz = 0
    has(validate(m), "zero clock_hz")


def test_timer_chip_index_oob():
    m = fresh(); m.timers[0].chip_index = 9
    has(validate(m), "chip_index 9 out of range")


def test_lane_zero_length():
    m = fresh(); m.lanes[0].length = 0
    has(validate(m), "LANE[0] zero length")


def test_lane_slice_oob():
    m = fresh(); m.lanes[0].length = 99
    has(validate(m), "outside VU08")


def test_lane_loop_oob():
    m = fresh(); m.lanes[0].loop_index = 9
    has(validate(m), "LANE[0] loop_index")


def test_tlan_slice_oob():
    m = fresh(); m.tlanes[0].length = 99
    has(validate(m), "outside VU32")


def test_action_target_reserved():
    m = fresh(); m.actions[0].target_id = 0x90  # 0x81..0xBF reserved
    has(validate(m), "reserved/invalid")


def test_action_target_rate_dropped():
    # 0x82 (former sample rate) is now reserved -> invalid (generic path).
    m = fresh(); m.actions[0].target_id = 0x82
    has(validate(m), "reserved/invalid")


def test_ay_target_rate_dropped():
    # ...and also invalid on the AY-specific path.
    m = fresh(); m.actions[0].target_id = 0x82
    has(validate(m), "invalid for AY chip")


def _amp_slice(m, targets):
    # Replace the frame-0 START slice with inline-value actions (sorted).
    m.actions = [Actn(target_id=t, source_mode=spec.SRC_INLINE_VALUE, operand=0)
                 for t in sorted(targets)]
    m.mods[0].first_action = 0
    m.mods[0].action_count = len(targets)
    return m


def test_sample_amplitude_paired_ok():
    # 0x80 amplitude + exactly one AY amp reg (R8 as volume + output).
    m = _amp_slice(fresh(), [0x08, spec.TGT_SAMPLE_AMPLITUDE])
    assert validate(m) == []


def test_sample_amplitude_no_amp_reg():
    # 0x80 with no R8/R9/R10 to combine into -> invalid.
    m = _amp_slice(fresh(), [0x00, spec.TGT_SAMPLE_AMPLITUDE])
    has(validate(m), "paired AY amp reg")


def test_sample_amplitude_two_amp_regs():
    # 0x80 with two amp regs -> ambiguous which carries volume/output.
    m = _amp_slice(fresh(), [0x08, 0x09, spec.TGT_SAMPLE_AMPLITUDE])
    has(validate(m), "paired AY amp reg")


def test_action_bind_lane_oob():
    m = fresh(); m.actions[0].operand = 7
    has(validate(m), "out of LANE range")


def test_start_zero_base():
    m = fresh(); m.mods[0].base_timer_value = 0
    has(validate(m), "base_timer_value is zero")


def test_start_unchanged_tlan():
    m = fresh(); m.mods[0].timer_lane_ref = spec.TLAN_UNCHANGED
    has(validate(m), "UNCHANGED invalid")


def test_start_no_actions():
    m = fresh(); m.mods[0].action_count = 0
    has(validate(m), "START with no actions")


def test_modulate_on_inactive():
    # Frame 0 START active, frame 1 STOP -> add a 3rd frame MODULATE on stopped.
    m = fresh(); m.trak.frame_count = 3
    m.mods.append(Mods(command=spec.CMD_MODULATE, base_timer_value=25,
                       timer_lane_ref=spec.TLAN_UNCHANGED))
    has(validate(m), "MODULATE on")


def test_modulate_owned_target_ok():
    # START owns 0x08 (sample slice); a MODULATE re-pointing 0x08 is legal (S12.3).
    m = fresh(); m.trak.frame_count = 3
    m.mods[1] = Mods(command=spec.CMD_MODULATE, base_timer_value=0,
                     timer_lane_ref=spec.TLAN_UNCHANGED,
                     first_action=len(m.actions), action_count=1)
    m.mods.append(Mods(command=spec.CMD_STOP))
    m.actions.append(Actn(target_id=0x08, source_mode=spec.SRC_INLINE_VALUE, operand=5))
    assert validate(m) == []


def test_modulate_unowned_target_rejected():
    # START owns only 0x08; a MODULATE naming 0x09 tries to add a target (S12.3).
    m = fresh(); m.trak.frame_count = 3
    m.mods[1] = Mods(command=spec.CMD_MODULATE, base_timer_value=0,
                     timer_lane_ref=spec.TLAN_UNCHANGED,
                     first_action=len(m.actions), action_count=1)
    m.mods.append(Mods(command=spec.CMD_STOP))
    m.actions.append(Actn(target_id=0x09, source_mode=spec.SRC_INLINE_VALUE, operand=5))
    has(validate(m), "unowned target")


def test_action_slice_unsorted():
    m = fresh()
    m.actions = [Actn(target_id=0x09, source_mode=spec.SRC_BIND_LANE, operand=0),
                 Actn(target_id=0x08, source_mode=spec.SRC_BIND_LANE, operand=0)]
    m.mods[0].first_action = 0
    m.mods[0].action_count = 2
    has(validate(m), "not strictly")


def test_ay_reg_non_u8_lane():
    m = fresh()
    m.lanes.append(Lane(value_type=spec.VT_U16, value_offset=0, length=1, loop_index=spec.NO_LOOP))
    m.vu16 = [100]
    m.actions[0].operand = 1  # bind R8 to the U16 lane
    has(validate(m), "non-U8 lane")


def test_ay_target_out_of_range():
    m = fresh(); m.actions[0].target_id = 0x40  # 0x0E..0x7F invalid for AY
    has(validate(m), "invalid for AY chip")


def test_ay_engine_target_unassigned():
    m = fresh(); m.actions[0].target_id = 0xC0
    has(validate(m), "invalid for AY chip")


def test_abs_relative_rate_over_ceiling():
    m = fresh()
    m.timers[0] = Timr(chip_index=0, clock_mode=spec.CLOCK_ABS_RATE_HZ,
                       clock_divider=0)
    m.tlanes[0] = Tlan(timing_mode=spec.TM_RELATIVE, value_offset=0,
                       length=1, loop_index=0)
    m.vu32 = [spec.to_fix16(2.0)]
    m.mods[0].base_timer_value = spec.to_fix16(40000.0)
    has(validate(m), "effective rate exceeds")


def test_double_start_same_target():
    # Two timers both START claiming AY R8 in frame 0.
    m = fresh()
    m.timers = [Timr(chip_index=0, clock_mode=spec.CLOCK_CHIP_PERIOD, clock_divider=16),
                Timr(chip_index=0, clock_mode=spec.CLOCK_CHIP_PERIOD, clock_divider=16)]
    start = Mods(command=spec.CMD_START, base_timer_value=25, timer_lane_ref=0,
                 first_action=0, action_count=1)
    stop = Mods(command=spec.CMD_STOP)
    m.mods = [start, copy.deepcopy(start), stop, copy.deepcopy(stop)]  # 2 frames x 2 timers
    m.trak.frame_count = 2
    has(validate(m), "two STARTs claim")


def test_loop_frame_requires_start_or_stop():
    m = fresh(); m.trak.frame_count = 3
    m.mods.append(Mods(command=spec.CMD_EMPTY))  # frame 2, timer 0
    m.trak.loop_frame = 2
    has(validate(m), "neither START nor STOP")


def test_nonzero_reserved_byte():
    data = bytearray(write_taym(fresh()))
    # TRAK reserved is the last 2 bytes of TRAK payload: header(16)+chunkhdr(8)+14.
    struct.pack_into("<H", data, 16 + 8 + 14, 0x1234)
    has(validate_bytes(bytes(data)), "nonzero reserved")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\n{len(tests)} passed")
