# Changelog

All notable changes to the `taym` Python package are documented here. The
format follows [Keep a Changelog](https://keepachangelog.com/), and the project
adheres to [Semantic Versioning](https://semver.org/).

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
