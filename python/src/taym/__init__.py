"""TAYM -- chip-music interchange format, reference implementation.

Read/write/validate TAYM files (draft 0.1), inspect them (stats, structural and
timeline dumps), and render them to PCM via the bundled reference engine
(`taym.engine`, uses pyayay/numpy). Reference-quality: readable against the
format spec (repo `docs/TAYM-format-draft-0.1.md`), not optimized.

    import taym
    t = taym.read_taym(open("song.taym", "rb").read())
    problems = taym.validate(t)            # spec section-14 checklist
    from taym.engine import render         # -> numpy float32 PCM

CLI:  python -m taym validate|stats|dump|sample FILE
      python -m taym.engine FILE.taym -o out.wav
"""
from .codec import CodecError, read_taym, write_taym
from .model import Actn, Chip, Lane, Mods, Taym, Timr, Tlan, Trak
from .psg import parse_psg, psg_frame_count
from .transform import drop_empty_timers
from .validate import ValidationError, check, validate, validate_bytes
from . import spec

try:                                   # written at build time by hatch-vcs
    from ._version import __version__
except ImportError:                    # editable/source tree without a build
    try:
        from importlib.metadata import version as _v
        __version__ = _v("taym")
    except Exception:
        __version__ = "0+unknown"

__all__ = [
    "spec", "__version__",
    "CodecError", "read_taym", "write_taym",
    "Taym", "Trak", "Chip", "Timr", "Mods", "Actn", "Lane", "Tlan",
    "ValidationError", "check", "validate", "validate_bytes",
    "parse_psg", "psg_frame_count",
    "drop_empty_timers",
]
