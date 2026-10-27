# taymengine -- TAYM offline reference renderer

Renders a TAYM file to PCM via **pyayay** (Ayumi, the AY core Bitphase uses), so
a TAYM export can be checked against real audio. It is the format's *audio
oracle* -- "what the bytes mean" -- not a target runtime. Deliberately simple
and readable against the spec (repo `docs/TAYM-format-draft-0.1.md`), not
optimized.

```
PYTHONPATH=scripts ../pyayay/.venv/bin/python -m taymengine song.taym -o song.wav
```

Needs pyayay >= 0.2 + numpy; import is lazy so the `taym` lib never depends on
it.

## What it models

One AY chip. Two layers run on the shared frame timeline (TAYM-overview "frame
stream + timers"):

1. **PSG frame data** (the `PSG0` chunk) is decoded to absolute 14-register
   state per frame -- the *background* (S13.1). Slow movement: notes, mixer,
   envelope setup.
2. **Timers** own a set of target registers while active and override the
   background for them, firing *between* frames (much faster than 50 Hz) to do
   PWM, duty sweeps, envelope retriggers. Fast movement.

The `0x80` sample-amplitude virtual target is modeled via the AY/YM DAC combine
(spec S11.1): its paired amp reg R8/9/10 supplies the volume, and the engine
quantizes `delog(volume) * amplitude` once through the chip's 16-step DAC curve.

Out of scope: multi-chip mixing beyond "last writer wins" (the validator
forbids the conflicting case anyway).

## The render loop

Time is counted in **AY clock cycles**, the chip's own time base. Frame `f`
starts at cycle `ceil(f * clock / frame_rate)`; a CHIP_PERIOD timer expires
every `divider * period` cycles (an exact integer), an ABS_RATE_HZ timer every
`clock / rate` cycles (fractional).

`_frame_writes` turns the file into a register-write timeline: per frame, a list
of `(ay_cycle, reg, value)` writes in time order. For each frame:

1. Resolve every timer's `MODS` command (`_apply_command`): `START` installs a
   target set + timing and *arms* the timer, with its first expiry at the frame
   start; `MODULATE` patches running state without retriggering;
   `STOP`/`EMPTY` as named.
2. Collect `owned` targets (a register an active timer drives).
3. Push **only changed** background registers (`_push_regs_delta`) as writes at
   the frame start, skipping owned ones.
4. Fire every **timer expiry** that falls before the next frame start, in time
   order. At each expiry (`_expiry`): advance the value + timer lanes one step,
   then write the owned targets at `ceil(next_expiry)`; reload the next interval
   from the (post-advance) timer-lane value.

A timer expiry is the atomic unit (S10.1): advance lanes, write targets, load
the next interval -- all together.

`_render` then renders the timeline with pyayay's `Ayumi.render(out, writes)`,
one call per frame, each covering the samples up to the next frame start. On
pyayay's absolute timeline a write at cycle `c` is applied right before sample
`ceil(c * sample_rate / clock)`, with exact integer math, so how the render is
cut into blocks does not change the output. The write timeline itself does not
depend on the sample rate at all.

## Three invariants that make it reference-grade

These are non-obvious and were each a real bug before being fixed; the
regression tests in `tests/test_taymengine_psg.py` pin them.

- **Continuous expiry clock.** `_TimerState.next_expiry` is the absolute AY
  cycle of the timer's next expiry and carries across frame boundaries. It must
  NOT reset per frame. Resetting it injected a phase discontinuity at every
  50 Hz boundary, smearing spurious harmonics into every sustained buzzer. The
  interval is the true spacing in cycles (e.g. `1773400 / 300.37 = 5904.05`
  for ABS_RATE_HZ), never rounded to an integer rate; only the write position
  is rounded up to a whole cycle.

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
all three channels (`set_pan(ch, 0.5, True)`) and renders into a 1-D buffer, for
which pyayay outputs `(L + R) / 2`.

`render_stereo()` renders into an `(n, 2)` buffer, returns `(L, R)`, and pans
tone channels A/B/C per the chip's stereo layout (`CHIP.config` bits 0..2,
appendix A.1): e.g. ABC = A-left, B-center, C-right. MONO is all-center, so
its `(L+R)/2` equals the mono oracle output -- which is why the bit-exact
regression path (`render()`, MONO) is untouched. The CLI renders stereo by default (`--mono` forces 1-channel).

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
- **Bit-exact diff** against a continuous Ayumi reference: one `render()`
  call with the R13 retriggers at exact multiples of the timer period and no
  frame concept. For a plain tone and an R13 buzz the engine matches it to
  `maxdiff < 1e-6` (`test_reference_grade_buzz_continuity`).
- **The write timeline** (`_frame_writes`) can be checked directly, without
  audio: `tests/test_taymengine_timeline.py` pins exact expiry cycles across
  frame boundaries and the armed first write.

### Sync-buzzer note for fixtures

An R13 buzzer needs a **short** envelope period (e.g. `env_period = 8`) so each
retrigger restarts a full saw; a long period (e.g. 200) never rises off zero and
renders silent. The retrigger rate sets pitch; `env_period` sets timbre. Also,
`[15, 0]` amplitude PWM looks visually ugly (AY amplitude 0 is the DAC floor, a
DC level, not silence) even when rendered correctly -- use `[15, 11]` for a
clean-looking tremolo fixture.
