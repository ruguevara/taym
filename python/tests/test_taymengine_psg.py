"""TAYM engine PSG-only render tests -- the foundation, no timer fx (pytest).

Builds frame-data-only TAYM files (one AY chip, a hand-made .psg, zero timers)
with a KNOWN answer and checks the rendered audio: silence renders silent, a
steady tone renders at the right frequency and a sane level. If these fail the
engine's PSG path is broken and nothing downstream can be trusted.

Skips cleanly if pyayay/numpy are unavailable.
"""
import pytest

AY_CLOCK = 1_773_400
FPS = 50.0
SR = 44100


def _available():
    try:
        import numpy  # noqa: F401
        import pyayay  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


SKIP = not _available()
pytestmark = pytest.mark.skipif(SKIP, reason="pyayay/numpy unavailable")


def _psg_steady(regs14, frames):
    """A minimal Bulba .psg: 16-byte header, frame 0 sets all regs, then
    `frames-1` repeat frames, $FD. R13=0xFF (no-write sentinel)."""
    out = bytearray(b"PSG\x1a" + bytes(12))
    out.append(0xFF)                       # frame 0
    for r in range(14):
        out += bytes((r, regs14[r] & 0xFF))
    if frames > 1:
        out += bytes((0xFE, frames - 1))   # repeat the rest
    out.append(0xFD)
    return bytes(out)


def _frame_data_taym(psg_bytes, frames):
    from taym import spec
    from taym.model import Chip, Taym, Trak
    chip = Chip(clock_hz=AY_CLOCK, chip_type_id=spec.CHIP_TYPE_AY,
                name="AY", frame_data_tag="PSG0")
    trak = Trak(frame_rate_hz=FPS, frame_count=frames, loop_frame=spec.NO_LOOP)
    return Taym(trak=trak, chips=[chip], timers=[], mods=[], actions=[],
                lanes=[], tlanes=[], vu08=[], vu16=[], vu32=[],
                frame_data={"PSG0": psg_bytes})


def _tone_regs(freq_hz, vol=15):
    """Channel A pure tone: tone period = clock/(16*f), mixer tone-A only."""
    tp = round(AY_CLOCK / (16 * freq_hz))
    regs = [tp & 0xFF, (tp >> 8) & 0x0F, 0, 0, 0, 0, 0,
            0b111110,            # R7: bit0=0 tone A on; everything else off
            vol, 0, 0, 0, 0, 0xFF]
    return regs, tp


def _measure_f0(sig):
    """Autocorrelation fundamental over the whole steady signal."""
    import numpy as np
    seg = sig.astype(float) - sig.mean()
    if seg.std() < 1e-4:
        return 0.0
    ac = np.correlate(seg, seg, "full")[len(seg) - 1:]
    lo = int(SR / 2000)
    hi = min(int(SR / 30), len(ac) - 1)
    if hi <= lo:
        return 0.0
    lag = lo + int(np.argmax(ac[lo:hi]))
    return SR / lag


def test_stereo_layout_pans_channel_a():
    """ABC layout pans channel A toward the left: a tone only on A is louder in
    L than R. The default stereo_width is partial (matches Bitphase), so R is
    attenuated but not silent; stereo_width=0.5 hard-pans A fully left. MONO keeps
    both channels equal."""
    if SKIP:
        print("  (skipped)"); return
    import numpy as np
    from taym import spec, write_taym
    from taym.engine import render, render_stereo

    def _rms(x):
        return float(np.sqrt(np.mean(x ** 2)))

    regs, _ = _tone_regs(220.0, vol=15)     # tone on channel A only
    taym = _frame_data_taym(_psg_steady(regs, 50), 50)

    # ABC, default partial width: A leans left -> L louder than R, R not silent.
    taym.chips[0].config = spec.AY_LAYOUT_ABC
    raw = write_taym(taym)
    L, R = render_stereo(raw, sample_rate=SR)
    assert _rms(L) > 0.05, f"ABC left channel too quiet: {_rms(L)}"
    assert _rms(R) < _rms(L), f"ABC: A should lean left (L>R): L={_rms(L)} R={_rms(R)}"

    # Hard pan (width 0.5): A fully left, R near-silent.
    Lh, Rh = render_stereo(raw, sample_rate=SR, stereo_width=0.5)
    assert _rms(Rh) < _rms(Lh) * 0.1, f"hard-pan right not attenuated: L={_rms(Lh)} R={_rms(Rh)}"

    # MONO: both channels equal, and equal to the mono render.
    taym.chips[0].config = spec.AY_LAYOUT_MONO
    raw = write_taym(taym)
    Lm, Rm = render_stereo(raw, sample_rate=SR)
    mono = render(raw, sample_rate=SR)
    assert np.allclose(Lm, Rm), "MONO layout should give equal L/R"
    assert np.allclose(Lm, mono), "MONO stereo channel should equal the mono render"


def test_st_mono_warns_and_falls_back_to_mono():
    """ST_MONO is a valid layout but pyayay has no is_st: render_stereo warns
    and produces plain MONO (L==R, equal to the mono render)."""
    if SKIP:
        print("  (skipped)"); return
    import warnings
    import numpy as np
    from taym import spec, validate, write_taym
    from taym.engine import render, render_stereo

    regs, _ = _tone_regs(220.0, vol=15)
    taym = _frame_data_taym(_psg_steady(regs, 50), 50)
    taym.chips[0].config = spec.AY_LAYOUT_ST_MONO
    assert validate(taym) == [], "ST_MONO must be a valid layout"

    raw = write_taym(taym)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        L, R = render_stereo(raw, sample_rate=SR)
    assert any("ST_MONO" in str(w.message) for w in caught), "expected ST_MONO warning"
    assert np.allclose(L, R), "ST_MONO fallback should be MONO (L==R)"
    assert np.allclose(L, render(raw, sample_rate=SR)), "fallback should equal mono render"


def test_silence_renders_silent():
    if SKIP:
        print("  (skipped)"); return
    import numpy as np
    from taym.engine import render
    from taym import write_taym
    # All mixer channels off (R7=0xFF), zero volume -> silence.
    regs = [0, 0, 0, 0, 0, 0, 0, 0xFF, 0, 0, 0, 0, 0, 0xFF]
    taym = _frame_data_taym(_psg_steady(regs, 25), 25)
    sig = render(write_taym(taym), sample_rate=SR)
    rms = float(np.sqrt(np.mean(sig ** 2)))
    assert rms < 1e-3, f"expected silence, got rms={rms}"


def test_steady_tone_frequency():
    if SKIP:
        print("  (skipped)"); return
    from taym.engine import render
    from taym import write_taym
    for target in (110.0, 220.0, 440.0):
        regs, tp = _tone_regs(target)
        taym = _frame_data_taym(_psg_steady(regs, 50), 50)  # 1s of steady tone
        sig = render(write_taym(taym), sample_rate=SR)
        f0 = _measure_f0(sig)
        # AY square fundamental = clock/(16*period); allow 3% (period rounding).
        want = AY_CLOCK / (16 * tp)
        err = abs(f0 - want) / want
        assert err < 0.03, f"tone {target}Hz: measured {f0:.1f}, want {want:.1f} (err {err:.1%})"


def test_steady_tone_level_sane():
    if SKIP:
        print("  (skipped)"); return
    import numpy as np
    from taym.engine import render
    from taym import write_taym
    regs, _ = _tone_regs(220.0, vol=15)
    taym = _frame_data_taym(_psg_steady(regs, 50), 50)
    sig = render(write_taym(taym), sample_rate=SR)
    rms = float(np.sqrt(np.mean(sig ** 2)))
    peak = float(np.max(np.abs(sig)))
    assert 0.05 < rms < 0.9, f"tone rms out of range: {rms}"
    assert peak <= 1.0, f"tone clips: peak {peak}"


def test_audio_demo_tone_and_pwm():
    """The audible demo (sample.build_audio_demo): a 220Hz tone with R8 PWM.
    The tone must show in the spectrum and the amplitude must be modulated."""
    if SKIP:
        print("  (skipped)"); return
    import numpy as np
    from taym.engine import render
    from taym import write_taym
    from taym.sample import build_audio_demo
    taym = build_audio_demo(frames=75, pwm_hz=300.0, tone_hz=220.0)
    sig = render(write_taym(taym), sample_rate=SR)
    assert len(sig) == 75 * round(SR / FPS)
    rms = float(np.sqrt(np.mean(sig ** 2)))
    assert 0.05 < rms < 0.9, f"demo rms {rms}"
    # tone present: FFT peak near 220 Hz in a steady slice
    seg = sig[SR // 4:SR // 4 + 8192] * np.hanning(8192)
    mag = np.abs(np.fft.rfft(seg))
    freqs = np.fft.rfftfreq(8192, 1 / SR)
    near = (freqs > 200) & (freqs < 240)
    assert mag[near].max() > 0.2 * mag.max(), "220Hz tone not prominent"
    # PWM present: per-window RMS envelope varies (gated, not steady)
    win = 64
    env = np.array([np.sqrt(np.mean(sig[i:i + win] ** 2))
                    for i in range(0, len(sig) - win, win)])
    assert env.std() / (env.mean() + 1e-9) > 0.1, "amplitude not PWM-modulated"


def test_reference_grade_buzz_continuity():
    """Reference-grade: the engine's R13 buzz must be BIT-EXACT to a fractional
    continuous reference (same retrigger schedule, no frame concept). This is
    the test that proves no per-frame phase glitch -- it failed before the
    continuous-expiry-clock + delta-register-push fixes."""
    if SKIP:
        print("  (skipped)"); return
    import numpy as np
    from pyayay import Ayumi, ChipType
    from taym.engine import render
    from taym import spec
    from taym.model import Actn, Chip, Mods, Taym, Timr, Tlan, Trak

    retrig_hz, tone_hz, frames = 500.0, 110.0, 20
    period = round(AY_CLOCK / (16 * retrig_hz))
    regs = [round(AY_CLOCK / (16 * tone_hz)) & 0xFF,
            (round(AY_CLOCK / (16 * tone_hz)) >> 8) & 0x0F, 0, 0, 0, 0, 0,
            0b111110, 0x10, 0, 0, 8, 0, 0xFF]   # env-mode A, short env period
    psg = _psg_steady(regs, frames)
    taym = Taym(
        trak=Trak(frame_rate_hz=FPS, frame_count=frames, loop_frame=spec.NO_LOOP),
        chips=[Chip(clock_hz=AY_CLOCK, chip_type_id=spec.CHIP_TYPE_AY,
                    name="AY", frame_data_tag="PSG0")],
        timers=[Timr(chip_index=0, clock_mode=spec.CLOCK_CHIP_PERIOD, clock_divider=16)],
        mods=[Mods(command=spec.CMD_START, base_timer_value=period, timer_lane_ref=0,
                   first_action=0, action_count=1)]
        + [Mods(command=spec.CMD_EMPTY) for _ in range(frames - 1)],
        actions=[Actn(target_id=spec.AY_R13_SHAPE, source_mode=spec.SRC_INLINE_VALUE,
                      operand=0x0E)],
        lanes=[], tlanes=[Tlan(timing_mode=spec.TM_ABSOLUTE, value_offset=0,
                               length=1, loop_index=0)],
        vu08=[], vu16=[], vu32=[period], frame_data={"PSG0": psg})

    eng = render(taym, sample_rate=SR)
    N = len(eng)
    # fractional continuous reference: same regs once, R13 retriggered on the
    # exact accumulator schedule the engine uses.
    spe = SR / (AY_CLOCK / (16 * period))
    ay = Ayumi(sample_rate=SR, clock=AY_CLOCK, type=ChipType.AY)
    for ch in range(3):
        ay.set_pan(ch, 0.5, True)
    ay.set_registers(list(range(13)), regs[:13])
    out, pos, acc = [], 0, 0.0
    while pos < N:
        if acc <= 0:
            ay.set_envelope_shape(0x0E)
            acc += spe
        nxt = int(min(max(1.0, acc), N - pos))
        L = np.zeros(nxt, dtype=np.float32)
        R = np.zeros(nxt, dtype=np.float32)
        ay.process_block(L, R, nxt)
        out.append((L + R) * 0.5)
        acc -= nxt
        pos += nxt
    ref = np.concatenate(out)[:N]
    n = min(len(eng), len(ref))
    maxdiff = float(np.abs(eng[:n] - ref[:n]).max())
    assert maxdiff < 1e-6, f"engine not bit-exact to continuous reference (maxdiff {maxdiff})"


def test_frame_count_matches_duration():
    if SKIP:
        print("  (skipped)"); return
    from taym.engine import render
    from taym import write_taym
    regs, _ = _tone_regs(220.0)
    taym = _frame_data_taym(_psg_steady(regs, 25), 25)
    sig = render(write_taym(taym), sample_rate=SR)
    spf = round(SR / FPS)
    assert abs(len(sig) - 25 * spf) <= spf, f"len {len(sig)} != ~{25*spf}"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\n{len(tests)} passed")
