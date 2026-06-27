# taymengine -- TAYM offline reference renderer

Renders a TAYM file to PCM via **pyayay** (Ayumi, the AY core Bitphase uses), so
a TAYM export can be checked against real audio. It is the format's *audio
oracle* -- "what the bytes mean" -- not a target runtime. Deliberately simple
and readable against the spec (repo `docs/TAYM-format-draft-0.1.md`), not
optimized.

```
PYTHONPATH=scripts ../pyayay/.venv/bin/python -m taymengine song.taym -o song.wav
```

Needs pyayay + numpy; import is lazy so the `taym` lib never depends on it.

## What it models

One AY chip. Two layers run on the shared frame timeline (TAYM-overview "frame
stream + timers"):

1. **PSG frame data** (the `PSG0` chunk) is decoded to absolute 14-register
   state per frame -- the *background* (S13.1). Slow movement: notes, mixer,
   envelope setup.
2. **Timers** own a set of target registers while active and override the
   background for them, firing *between* frames (much faster than 50 Hz) to do
   PWM, duty sweeps, envelope retriggers. Fast movement.

Out of scope: the format-virtual range (`0x80..0xBF`, reserved in draft 0.1),
multi-chip mixing beyond "last writer wins" (the validator forbids the
conflicting case anyway).

## The render loop

For each frame (`render` in `engine.py`):

1. Resolve every timer's `MODS` command (`_apply_command`): `START` installs a
   target set + timing and *arms* the timer; `MODULATE` patches running state
   without retriggering; `STOP`/`EMPTY` as named.
2. Collect `owned` targets (a register an active timer drives).
3. Push **only changed** background registers (`_push_regs_delta`), skipping
   owned ones.
4. Walk the frame as sub-blocks bounded by **timer expiries**. At each expiry
   (`_expiry`): advance the value + timer lanes one step, then write the owned
   targets; reload the next interval from the (post-advance) timer-lane value.
   Render audio between expiries with Ayumi.

A timer expiry is the atomic unit (S10.1): advance lanes, write targets, load
the next interval -- all together.

## Three invariants that make it reference-grade

These are non-obvious and were each a real bug before being fixed; the
regression tests in `tests/test_taymengine_psg.py` pin them.

- **Continuous expiry clock.** `_TimerState.samples_to_expiry` is a *float*
  accumulator that carries across frame boundaries. It must NOT reset per frame.
  Resetting it injected a phase discontinuity at every 50 Hz boundary, smearing
  spurious harmonics into every sustained buzzer. The interval is the true
  fractional sample spacing (e.g. `44100 / 300.37 = 146.8`), never rounded to an
  integer rate.

- **Armed first write (S12.2/12.3).** Right after `START` (and a lane install
  via `MODULATE`) the first expiry writes lane element 0 *without* advancing.
  This is an `armed` flag on the timer state, not a per-frame local -- otherwise
  the no-advance fires again at every frame boundary and doubles a write.

- **Delta register push (S13.1).** Re-latching an *unchanged* register each
  frame is not a no-op on real hardware: re-writing R8/R11/R12 (env-mode
  amplitude + envelope period) **resets the AY envelope generator** mid-buzz.
  `_push_regs_delta` writes only registers whose value changed since the last
  latch. An owned register's shadow is set stale (`None`) so the background
  re-asserts cleanly when the timer releases it.

## Mono mixdown and stereo

Ayumi renders stereo with per-channel pan. `render()` (the oracle) center-pans
all three channels (`set_pan(ch, 0.5, False)`), renders into separate L/R
buffers, and outputs `(L + R) * 0.5`. Reusing one buffer or skipping the sum
gives R-only at half level -- quiet and pan-skewed.

`render_stereo()` returns `(L, R)` and pans tone channels A/B/C per the chip's
stereo layout (`CHIP.config` bits 0..2, appendix A.1): e.g. ABC = A-left,
B-center, C-right. MONO is all-center, so its `(L+R)/2` equals the mono oracle
output -- which is why the bit-exact regression path (`render()`, MONO) is
untouched. The CLI renders stereo by default (`--mono` forces 1-channel).

`ST_MONO` (layout 7) is the Atari ST combined-DAC mono mix -- a non-linear
function of all three channel volumes, not a pan. Ayumi's C core supports it
(`is_st`), but the installed pyayay wheel does not expose that flag, so the
engine warns and falls back to plain MONO. Implementing it here means porting
Ayumi's `generate_dac`/`ST_dac_table` and driving it from per-sample channel
volumes; left for when pyayay exposes ST or that data.

The chip's DAC curve follows `CHIP.variant` (A.1): variant 1 selects the YM2149
curve (`ChipType.YM`), otherwise the AY-3-8910 curve.

## Verifying the engine -- by eye and bit-exactly, NOT by spectrum

Spectral / autocorrelation analysis *lies* on these signals: autocorrelation
latches onto a buzzer's subharmonics and reports the wrong fundamental, and
spectral correlation can look fine while a phase glitch is audible. Use:

- **`scripts/taym_scope.py`** -- renders short known-answer fixtures
  (`tone`, `pwm`, `tremolo`, `buzz`, `duty`) to waveform PNGs at sample
  resolution, marking 50 Hz frame boundaries (where glitches hide). The eye is
  the right instrument.
- **Bit-exact diff** against a *fractional* continuous Ayumi reference that
  retriggers on the same accumulator schedule with no frame concept. For a plain
  tone and an R13 buzz the engine matches it to `maxdiff < 1e-6`
  (`test_reference_grade_buzz_continuity`).

### Sync-buzzer note for fixtures

An R13 buzzer needs a **short** envelope period (e.g. `env_period = 8`) so each
retrigger restarts a full saw; a long period (e.g. 200) never rises off zero and
renders silent. The retrigger rate sets pitch; `env_period` sets timbre. Also,
`[15, 0]` amplitude PWM looks visually ugly (AY amplitude 0 is the DAC floor, a
DC level, not silence) even when rendered correctly -- use `[15, 11]` for a
clean-looking tremolo fixture.
