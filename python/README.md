# taym

Python reference implementation of **TAYM** (Timer-tricks AY-3-8910 Music) -- a
chip-agnostic *interchange* format for music with timer tricks (PWM, duty
sweeps, envelope retriggers). The name references the AY-3-8910 but the format
targets any retro music chip where these techniques apply. Reader/writer/validator
for the format, plus an offline **AY reference renderer** (the format's audio
oracle).

The normative spec lives in the [repository](https://github.com/ruguevara/taym)
under `docs/`; a copy ships inside this package at `taym/docs/`.

```bash
pip install taym            # core: codec, validator, stats, dump (no deps)
pip install taym[engine]    # + numpy & pyayay for the reference renderer
```

The core (read/write/validate/inspect) is dependency-free. The reference
renderer needs numpy + pyayay, pulled by the `[engine]` extra.

## Library

```python
import taym

t = taym.read_taym(open("song.taym", "rb").read())
problems = taym.validate(t)          # full spec section-14 checklist; [] = valid
data = taym.write_taym(t)            # canonical, byte-stable

from taym.engine import render, render_stereo   # numpy PCM (needs taym[engine])
pcm = render(t, sample_rate=44100)              # mono float32 (the oracle)
left, right = render_stereo(t)                  # stereo per CHIP.config layout (A.1)
```

The in-memory model is plain dataclasses (`Taym`, `Trak`, `Chip`, `Timr`,
`Mods`, `Actn`, `Lane`, `Tlan`) -- hand-constructible and round-trippable.
Validation is strict and a first-class feature: one check per spec rule, with
precise messages naming the section.

## Command line

```bash
taym validate song.taym         # section-14 validation; exit 1 on any problem
taym stats    song.taym         # counts, sizes, MODS command histogram, per-timer
taym dump     song.taym         # structural field-by-field dump
taym dump --timeline song.taym  # decoded per-frame timer events
taym sample   out.taym          # write the built-in canonical sample
taym sample --audio out.taym    # an audible tone+PWM demo instead

taym-render song.taym -o song.wav   # WAV, stereo per the chip's layout (A.1)
taym-render song.taym --mono        # 1-ch WAV instead
```

## The reference engine

`taym.engine` renders a TAYM file to PCM via
[pyayay](https://pypi.org/project/pyayay/) (Ayumi, a widely used AY-3-8910 /
YM2149 core). It interprets the PSG frame stream as background register state
and the timer/lane model as fast sub-frame modulation. It is the format's audio
oracle -- "what the bytes mean" -- not a target runtime.

It is **reference-grade**: for a plain tone and an R13 buzzer it is bit-exact
(`maxdiff < 1e-6`) to a fractional continuous Ayumi reference. See
`src/taym/engine/README.md` for the model and the three invariants that make it
so (continuous expiry clock, armed first write, delta register push).

Verify engine output by **eye** (sample-resolution waveform plots) and by
**bit-exact diff**, never by spectral/autocorrelation analysis, which misreads
buzzers.

## License

MIT -- see the repository `LICENSE`.
