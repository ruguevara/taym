"""TAYM codec round-trip tests (pytest).

Checks the canonical sample packs to the hand-verified bytes, survives a
write->read->write round trip, and that read_taym rejects structurally broken
files. Semantic (section-14) validation lives in validate.py / its own test.
"""
import struct

from taym import read_taym, write_taym, CodecError, spec
from taym.sample import build as build_witness, build_model


def test_sample_packs_to_witness():
    assert write_taym(build_model()) == build_witness()


def test_sample_size():
    assert len(write_taym(build_model())) == 230


def test_round_trip_bytes_stable():
    data = write_taym(build_model())
    again = write_taym(read_taym(data))
    assert again == data


def test_round_trip_model_fields():
    m = build_model()
    r = read_taym(write_taym(m))
    assert r.trak.frame_rate_hz == 50.0
    assert r.trak.frame_count == 2
    assert r.trak.loop_frame == spec.NO_LOOP
    assert len(r.chips) == 1 and r.chips[0].chip_type_id == spec.CHIP_TYPE_AY
    assert r.chips[0].name == "AY" and r.chips[0].frame_data_tag == ""
    assert r.timers[0].clock_mode == spec.CLOCK_CHIP_PERIOD
    assert r.timers[0].clock_divider == 16
    assert [x.command for x in r.mods] == [spec.CMD_START, spec.CMD_STOP]
    assert r.mods[0].base_timer_value == 25 and r.mods[0].timer_lane_ref == 0
    assert r.actions[0].target_id == 0x08
    assert r.lanes[0].value_type == spec.VT_U8 and r.lanes[0].length == 2
    assert r.vu08 == [15, 0] and r.vu32 == [25, 75]


def test_stop_fields_canonicalized():
    # STOP record's ignored fields are zeroed on write regardless of model.
    data = write_taym(build_model())
    chunks, _, _ = __import__("taym.codec", fromlist=["_split_chunks"])._split_chunks(data)
    stop = chunks["MODS"][spec.MODS_SIZE:2 * spec.MODS_SIZE]
    base, tlref, fa, ac, cmd, _ = struct.unpack(spec.MODS_FMT, stop)
    assert (base, tlref, fa, ac, cmd) == (0, 0, 0, 0, spec.CMD_STOP)


def test_info_round_trip():
    m = build_model()
    m.info = {"title": "PWM", "author": "ru"}
    r = read_taym(write_taym(m))
    assert r.info == {"title": "PWM", "author": "ru"}


def _expect(exc, fn, *a):
    try:
        fn(*a)
    except exc:
        return
    raise AssertionError(f"expected {exc.__name__}")


def test_reject_bad_magic():
    data = bytearray(write_taym(build_model()))
    data[0] = ord("X")
    _expect(CodecError, read_taym, bytes(data))


def test_reject_wrong_chunk_bytes():
    data = bytearray(write_taym(build_model()))
    struct.pack_into("<I", data, 12, 9999)
    _expect(CodecError, read_taym, bytes(data))


def test_reject_trailing_bytes():
    data = write_taym(build_model()) + b"\x00"
    _expect(CodecError, read_taym, data)


def test_reject_missing_core_chunk():
    # Drop the trailing VU32 chunk and fix chunk_bytes so structure is consistent.
    data = write_taym(build_model())
    # VU32 is the last chunk: 8-byte header + 8 payload = 16 bytes.
    trimmed = bytearray(data[:-16])
    struct.pack_into("<I", trimmed, 12, len(trimmed) - spec.HEADER_SIZE)
    _expect(CodecError, read_taym, bytes(trimmed))


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\n{len(tests)} passed")
