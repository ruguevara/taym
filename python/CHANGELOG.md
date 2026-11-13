# Changelog

All notable changes to the `taym` Python package are documented here. The
format follows [Keep a Changelog](https://keepachangelog.com/), and the project
adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- `taym.engine` renders through pyayay's absolute-timeline `Ayumi.render()`
  API and now requires `pyayay>=0.2.1`. The engine first builds a timeline of
  `(ay_cycle, reg, value)` writes, then renders it with one call per frame;
  about 3x faster on timer-heavy songs.
- Frames and timer expiries are timed in AY clock cycles instead of output
  samples. CHIP_PERIOD timers and frame rates that divide the sample rate render
  bit-identically to before. An ABS_RATE_HZ expiry is rounded up to a whole AY
  cycle, which can move it one sample later. A frame rate that does not divide
  the sample rate no longer accumulates per-frame rounding drift.
- `parse_psg` returns R13 per frame: the value the stream writes in that frame,
  and `0xFF` (no write) in a frame that does not write it. It used to carry the
  last R13 into every later frame. R0..R12 are still absolute states.

### Fixed

- A repeated envelope-shape write in PSG frame data is kept (spec S6.2). Every
  R13 write restarts the envelope, the same shape too, but the engine wrote R13
  only when its value changed, so an envelope tune writing R13 = 9 on frames 0,
  5 and 10 got one write. `python -m taym writes` and the C walker
  (`taym_player_run`, `taym_player_parse_psg`) changed together.

## [0.1.0] - 2026-06-27

Initial public release. Reference implementation of the TAYM (Timer-tricks
AY-3-8910 Music) interchange format, draft 0.1.

### Added

- `read_taym` / `write_taym` canonical, byte-stable codec.
- Strict, spec-section-14 `validate` / `validate_bytes` checker.
- Plain-dataclass in-memory model (`Taym`, `Trak`, `Chip`, `Timr`, `Mods`,
  `Actn`, `Lane`, `Tlan`); hand-constructible and round-trippable.
- `parse_psg` / `psg_frame_count` for embedded Bulba `.psg` streams.
- `drop_empty_timers` model transform.
- `taym` CLI: `validate`, `stats`, `dump` (with `--timeline`), `sample`.
- `taym.engine` offline AY reference renderer (`render`, `render_stereo`) and
  the `taym-render` CLI; reference-grade against a continuous Ayumi reference.
- Spec docs shipped inside the package at `taym/docs/`.

### Notes

- The core (codec/validator/stats/dump) installs dependency-free. The reference
  renderer needs numpy + pyayay via the optional extra: `pip install taym[engine]`.
