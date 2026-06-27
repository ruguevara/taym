# TAYM interchange format

A chip-oriented *interchange* format for music with timer tricks. Trackers and
synthesis tools export TAYM; platform-specific converters turn it into their own
runtime formats for retrocomputer platforms. This format is not intended for
direct multiplatform playback.

The format is chip-agnostic: although the name references the AY-3-8910
(TAYM is acronym for "Timer-tricks AY Music", spelled as "time"), the format
targets any retro music chip (SID, OPL, SN76489, etc.) where timer-trick
techniques apply.

This repository holds the **format specification** and **reference
implementations** of it. Start with the
[overview](docs/TAYM-overview.md) for the mental model, then the
[normative spec (draft 0.1)](docs/TAYM-format-draft-0.1.md).

## Layout

```
docs/        the format specification (language-neutral, normative)
python/      Python reference reader/writer/validator + AY reference renderer
c/           C reference model + reader/writer
```

- **[`docs/TAYM-format-draft-0.1.md`](docs/TAYM-format-draft-0.1.md)** — the
  normative spec (draft 0.1). [`docs/TAYM-overview.md`](docs/TAYM-overview.md)
  is the informal tour; read it first.
- **`python/`** — the `taym` package: codec, strict spec-section-14 validator,
  stats, dumps, and an offline AY reference renderer (`taym.engine`, the audio
  oracle). `pip install taym`. See `python/README.md`.
- **`c/`** — a C reference model plus structural reader/writer, so a plain C
  consumer has a vetted implementation to start from. It intentionally omits
  section-14 semantic validation and rendering.

## On LLM-assisted tooling

The reference implementation here was largely written with the help of LLM
coding agents. That is intentional, not incidental: in practice, much of the
tooling around this format — readers, writers, and platform converters — will
likely be written with AI assistance anyway. So the project leans into it. The
spec is deliberately generic and self-contained, and the reference code is
plain and conventional, precisely so that a similar LLM can read the spec, study
the reference, and produce a correct implementation for a new platform with
minimal friction. Easy for a human to understand, easy for a model to
understand — same goal.

In the demoscene, hacker craftsmanship lives mostly in the tight, elegant code
written for the target platforms themselves — not so much in the interchange
format's tooling. So letting an LLM handle the readers, writers, and converters
frees that craft to go where it actually matters.

## License

MIT — see `LICENSE`.
