# Repository Guidelines

## Project Structure & Module Organization

This is the standalone TAYM monorepo, embedded under AYMax at `scripts/taym`.
The language-neutral format source of truth is in `docs/`, especially
`docs/TAYM-format-draft-0.1.md`. The Python reference implementation lives in
`python/`: package code is under `python/src/taym/`, CLI entry points are
`taym` and `taym-render`, and tests are in `python/tests/`. The `c/` directory
contains the C reference model plus structural reader/writer and should treat
the Python package as the behavioral oracle.

## Build, Test, and Development Commands

Run Python commands from `python/`, matching CI’s working directory.

```bash
cd python
python -m pip install -e .    # editable install with runtime dependencies
python -m pytest -q           # run the full test suite
python -m pytest tests/test_taym_validate.py -q
python -m taym sample out.taym
python -m taym validate out.taym
python -m taym.engine song.taym -o out.wav
```

Engine tests skip when optional renderer dependencies are unavailable, but
codec and validator tests should still pass.

## Coding Style & Naming Conventions

Use plain Python 3.9-compatible code, four-space indentation, dataclasses for
model records, and descriptive snake_case names. Keep format constants,
sentinels, struct formats, sizes, and chunk ordering centralized in
`python/src/taym/spec.py`; do not hard-code offsets in codecs, validators, or
transforms. Keep transforms pure `Taym -> Taym` operations and avoid embedding
format packing details outside `codec.py`.

## Testing Guidelines

Tests use `pytest` and follow `test_*.py` / `test_*` naming. Add focused tests
next to the behavior changed: codec round trips in `test_taym_codec.py`,
semantic rules in `test_taym_validate.py`, stats/dump behavior in
`test_taym_stats_dump.py`, and renderer invariants in `test_taymengine_psg.py`.
For format changes, update `docs/`, `spec.py`, validator checks, and golden or
round-trip tests together.

## Commit & Pull Request Guidelines

History uses concise imperative messages, sometimes with conventional prefixes
such as `feat:`, `refactor:`, or `ci:`. Keep commits scoped and mention the
format area affected when useful. Pull requests should summarize behavior
changes, list test commands run, link issues when applicable, and call out
spec, fixture, or renderer-output changes explicitly.

## Agent-Specific Instructions

Read `CLAUDE.md` before substantial edits. Preserve the repo’s goal of simple,
spec-readable reference code: correctness and clarity matter more than clever
abstractions.
