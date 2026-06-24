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
c/         planned C reader/writer (not written yet)
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
python -m taym.engine   song.taym -o out.wav   # render to WAV, stereo per CHIP.config (A.1)
python -m taym.engine   song.taym --mono       # 1-ch WAV instead
```

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

### Sentinels and indices (common gotchas)

- Cross-references are integer **indices**, never pointers. `index 0` is valid.
- The only "null" is `0xFFFFFFFF` (`NO_LOOP` / `TLAN_NONE`); `0xFFFFFFFE` is
  `TLAN_UNCHANGED` (MODULATE-only "keep current").
- `MODULATE` is not a restart: never retriggers, never writes a register itself;
  new output appears at the next expiry. Preserves lane phase.
- Everything multibyte is little-endian; reserved fields must be zero.

## The reference engine (engine/engine.py)

Renders a TAYM file to PCM via **pyayay** (Ayumi AY core). It is the format's
*audio oracle* — "what the bytes mean" — not a target runtime; deliberately
simple and readable against the spec, not optimized. Import of numpy/pyayay is
lazy so the core `taym` lib never depends on it.

It is reference-grade (bit-exact to a fractional continuous Ayumi reference,
`maxdiff < 1e-6`, for tone + R13 buzz) because of three non-obvious invariants,
each pinned by a regression test in `tests/test_taymengine_psg.py` — **do not
break these** (full rationale in `engine/README.md`):

1. **Continuous expiry clock** — `samples_to_expiry` is a float accumulator that
   carries across frame boundaries; never reset per frame, never round the
   interval to an integer rate.
2. **Armed first write** — the first expiry after START (or a lane install via
   MODULATE) writes lane element 0 *without* advancing; tracked by an `armed`
   flag on the timer state, not a per-frame local.
3. **Delta register push** — only write background registers whose value
   changed; re-latching unchanged R8/R11/R12 resets the AY envelope generator
   mid-buzz.

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
