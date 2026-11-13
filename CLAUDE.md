# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

This is the **standalone TAYM monorepo** (`github.com/ruguevara/taym`). It is
embedded as a submodule inside the AYMax project at `scripts/taym`, but it is
its own repo — edit it here, commit and push from here.

## What TAYM is

TAYM (Timer-tricks AY-3-8910 Music) is a chip-agnostic *interchange* format for
chip music with timer tricks (PWM, duty sweeps, envelope retriggers, samples).
A tracker/synth exports TAYM; a per-platform converter compiles it into a target
runtime format. It is **not** a playback format — "source you compile from," not
a runtime. Despite the name it targets any retro PSG (SID, SN76489, ...).

The repo holds the **spec** (normative) and **reference implementations** of it.

## Layout

```
docs/      normative spec (language-neutral) — the source of truth
python/    Python reference reader/writer/validator + AY reference renderer
c/         C reference model, structural reader/writer, and the timer engine
```

- `docs/TAYM-overview.md` — informal tour / mental model. **Read this first.**
- `docs/TAYM-format-draft-0.1.md` — the normative spec (draft 0.1). Cited by
  section: "S12", "section 14", "appendix A".

## Commands

The Python package lives in `python/` (the CI sets `working-directory: python`).

```bash
cd python
python -m pip install -e .          # editable install (pulls numpy/pyayay/matplotlib)
python -m pytest -q                 # all tests
python -m pytest tests/test_taym_validate.py -q          # one file
python -m pytest tests/test_taymengine_psg.py::test_reference_grade_buzz_continuity   # one test
```

Engine tests self-skip if `numpy`/`pyayay` aren't importable, so a deps-free
install still passes the codec/validator suite. CI runs Python 3.9, 3.11, 3.13.

CLI (installed as `taym` and `taym-render`, or `python -m taym`):

```bash
python -m taym validate song.taym          # section-14 checks; exit 1 on problems
python -m taym stats    song.taym          # counts, MODS histogram, per-timer
python -m taym dump     song.taym          # structural field-by-field
python -m taym dump --timeline song.taym   # decoded per-frame timer events
python -m taym sample   out.taym           # write the canonical sample fixture
python -m taym sample --engine out.taym    # the engine-rules fixture (cross-engine diffs)
python -m taym writes   song.taym          # register-write timeline as text
python -m taym.engine   song.taym -o out.wav   # render to WAV, stereo per CHIP.config (A.1)
python -m taym.engine   song.taym --mono       # 1-ch WAV instead
```

`writes` prints `frame cycle reg value` per line and needs no audio deps: it is
the oracle the C engine is diffed against (`cd c && make test`).

## Architecture (python/src/taym)

The whole on-disk format is described **once** in `spec.py` — every constant,
struct format string, enum, sentinel, and stride. `codec.py` and `validate.py`
import from `spec.py` and nowhere else hard-codes an offset or width. **If the
spec draft changes, edit `spec.py` first.**

- `model.py` — plain dataclasses, one per chunk record (`Taym`, `Trak`, `Chip`,
  `Timr`, `Mods`, `Actn`, `Lane`, `Tlan`). Hand-constructible and
  round-trippable; no codec logic. `Taym.mods` is the row-major
  `frame_count x timer_count` grid flattened, `index = frame*timer_count + timer`.
- `codec.py` — `read_taym(bytes) -> Taym` / `write_taym(Taym) -> bytes`. Output
  is canonical and byte-stable (fixed chunk order from `spec.CHUNK_ORDER`).
- `validate.py` — strict, one check per spec rule with messages naming the
  section. `validate(model)` + `validate_bytes(raw)`. Validation is a
  first-class feature, not an afterthought.
- `psg.py` — parse/count standard Bulba `.psg` frame streams (embedded per-chip
  as `PSG0`/`PSG1`/... chunks).
- `transform.py` — pure `Taym -> Taym` functions operating on the model only
  (e.g. `drop_empty_timers`). No format knowledge; derived counts re-derive on
  write, so transforms only touch arrays.
- `stats.py` / `dump.py` — inspection (counts/histograms; structural and
  decoded-timeline dumps).
- `sample.py` — the canonical sample model + an audible tone+PWM demo; these are
  the golden fixtures (also intended as the C impl's cross-language oracle).
- `engine/` — the offline **AY reference renderer** (the audio oracle).

## Architecture (c)

`c/include/taym/taym.h` + `c/src/taym.c` are the owned-memory model and
structural read/write: little-endian decoded explicitly, canonical chunk order,
but **no** section-14 semantic validation (use the Python validator).

`c/include/taym/taym_timers.h` + `c/src/taym_timers.c` are the **timer engine**
(S9..S13): commands in, `(cycle, target, value)` writes out. No allocation, no
I/O, all state in a caller-owned `TaymTimerBank` — so the same code serves the
offline walker and a real-time host (audio plugin, embedded firmware). Read it
next to `python/src/taym/engine/engine.py`; they are deliberately the same
algorithm and the Python one is the oracle.

**The engine is chip-agnostic; keep it that way.** It reports raw target
values (any id, up to 32 bits, virtual targets included) and knows no register,
width or DAC. Chip rules live in a per-chip file: `c/src/taym_ay.c`
(`taym_ay_resolve`) turns an expiry's target writes into AY register writes —
byte registers, R13's four bits, the 0x80 amplitude combine. The Python engine
is AY-only and mixes the two; the C split is the model for another chip.

`c/include/taym/taym_player.h` + `c/src/taym_player.c` add the frame loop, the
PSG background and the S13.1 background push for an AY chip, mirroring
`engine._frame_writes`.

`make test` runs the structural round trip, the engine's unit checks
(`tests/test_timers.c`, covering the rules the fixtures don't reach), and the
**cross-language diff**: the C timeline (`tests/taym_timeline.c`) against
`python -m taym writes` must be byte-identical. Keep those two output formats
in sync — the diff is how the C engine is held to the oracle. The three
engine invariants in the section below apply verbatim to the C engine, and
`tests/test_timers.c` pins each one.

### Sentinels and indices (common gotchas)

- Cross-references are integer **indices**, never pointers. `index 0` is valid.
- The only "null" is `0xFFFFFFFF` (`NO_LOOP` / `TLAN_NONE`); `0xFFFFFFFE` is
  `TLAN_UNCHANGED` (MODULATE-only "keep current").
- `MODULATE` is not a restart: never retriggers, never writes a register itself;
  new output appears at the next expiry. Preserves lane phase.
- Everything multibyte is little-endian; reserved fields must be zero.

## The reference engine (engine/engine.py)

Renders a TAYM file to PCM via **pyayay** >= 0.2.1 (Ayumi AY core). It is the format's
*audio oracle* — "what the bytes mean" — not a target runtime; deliberately
simple and readable against the spec, not optimized. Import of numpy/pyayay is
lazy so the core `taym` lib never depends on it.

The engine works in two steps. `_frame_writes` interprets the format into a
per-frame list of `(ay_cycle, reg, value)` writes (pure Python, no deps), and
`_render` hands each frame's writes to pyayay's `Ayumi.render()`, which applies
a write at absolute AY cycle `c` right before sample `ceil(c * sr / clock)`.

It is reference-grade (bit-exact to a continuous Ayumi reference,
`maxdiff < 1e-6`, for tone + R13 buzz) because of four non-obvious invariants,
each pinned by a regression test in `tests/test_taymengine_psg.py` /
`tests/test_taymengine_timeline.py` — **do not break these** (full rationale in
`engine/README.md`):

1. **Continuous expiry clock** — `next_expiry` is the absolute AY cycle of a
   timer's next expiry; it carries across frame boundaries, never resets per
   frame, and is never rounded to an integer rate (CHIP_PERIOD intervals are
   exact `divider * period` cycles; ABS_RATE_HZ ones are fractional).
2. **Armed first write** — the first expiry after START (or a lane install via
   MODULATE) writes lane element 0 *without* advancing; tracked by an `armed`
   flag on the timer state, not a per-frame local.
3. **Background push: delta for R0..R12, every write for R13** — R0..R12 are
   states: write one only when its value changed (a repeat of the value it
   holds changes nothing audible). R13 is an event, because every write of it
   restarts the envelope, the same shape too: `parse_psg` gives a frame its
   R13 only when the stream writes it there (`0xFF` otherwise), and the engine
   writes it then, whatever the value. So a repeated shape in the stream is
   kept (S6.2), and a frame that does not write R13 never re-latches it, which
   would reset the envelope mid-buzz. A timer that releases R13 hands it back
   with the background's last shape.
4. **Timer-lane completion (S10.2)** — a *no-loop* timer lane runs its final
   interval and then makes the timer **quiescent**: no further expiries, and no
   advance or write at that final boundary, though it keeps its targets until a
   STOP. A *looping* final element means "continue indefinitely" instead.
   Treating the no-loop case as a dormant value (keep firing at that interval)
   runs the timer forever. Found by diffing the C engine against this one.

**Verify engine output by eye (sample-resolution waveform plots, see
`scripts/taym_scope.py`) and by bit-exact diff — NEVER by spectral or
autocorrelation analysis**, which misreads buzzers (latches onto subharmonics,
reports wrong fundamentals, masks audible phase glitches).

## Editing the spec / format

The reference code is intentionally plain and conventional, and the spec is
self-contained, so an LLM can read the spec + reference and produce a correct
implementation for a new platform. Keep that property: when changing the format,
update `docs/` and `spec.py` together, keep the code readable-against-the-spec
rather than clever, and add a validator check + a test for any new rule.
