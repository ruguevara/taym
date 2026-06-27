"""TAYM 0x80 sample-amplitude DAC combine (spec S11.1, appendix A.3).

Pure-math unit tests for the engine's amplitude x volume -> nearest-code combine.
These do NOT need numpy/pyayay (the engine's audio deps are imported lazily
inside _render), so they run in the deps-free CI lane too.
"""
from taym import spec
from taym.engine.engine import (
    _AY_DAC, _YM_DAC, _amp_full_scale, _combine_amp_code, _dac_table,
)


def test_dac_table_selects_by_variant():
    assert _dac_table(spec.AY_VARIANT_AY) is _AY_DAC
    assert _dac_table(spec.AY_VARIANT_YM) is _YM_DAC
    assert len(_AY_DAC) == 16 and len(_YM_DAC) == 16


def test_full_amplitude_is_identity():
    # amplitude == full scale -> output is just the volume code, requantized.
    for code in range(16):
        assert _combine_amp_code(_AY_DAC, code, 0xFF, 0xFF) == code or \
            _AY_DAC[_combine_amp_code(_AY_DAC, code, 0xFF, 0xFF)] == _AY_DAC[code]
    # the common case: full volume x full amplitude -> top code.
    assert _combine_amp_code(_AY_DAC, 15, 0xFF, 0xFF) == 15
    assert _combine_amp_code(_YM_DAC, 15, 0xFF, 0xFF) == 15


def test_zero_amplitude_is_silent():
    assert _combine_amp_code(_AY_DAC, 15, 0, 0xFF) == 0
    assert _combine_amp_code(_YM_DAC, 15, 0, 0xFF) == 0


def test_attenuation_lowers_code():
    # half amplitude at full volume must drop below full code (single requant).
    half = _combine_amp_code(_AY_DAC, 15, 0x80, 0xFF)
    assert 0 < half < 15


def test_ay_and_ym_curves_differ():
    # same volume + amplitude, different chip -> different requantized code,
    # because the 16-step volume curves are not identical.
    assert _combine_amp_code(_AY_DAC, 15, 0x80, 0xFF) != \
        _combine_amp_code(_YM_DAC, 15, 0x80, 0xFF)


def test_u16_full_scale():
    assert _amp_full_scale(spec.VT_U16) == 0xFFFF
    assert _amp_full_scale(spec.VT_U8) == 0xFF
    # U16 amplitude at full scale is still identity at full volume.
    assert _combine_amp_code(_AY_DAC, 15, 0xFFFF, 0xFFFF) == 15
    # a U16 amplitude equal to U8 full-scale (255/65535) is near-silent, not unity.
    assert _combine_amp_code(_AY_DAC, 15, 0xFF, 0xFFFF) <= 1
