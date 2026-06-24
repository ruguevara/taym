"""TAYM offline reference engine -- renders a TAYM file to PCM via pyayay
(Ayumi). The format's audio oracle. See README.md in this directory for the
model and the reference-grade invariants.

Needs pyayay + numpy. CLI: `python -m taym.engine FILE.taym -o out.wav`.
"""
from .engine import EngineError, render, render_stereo  # noqa: F401

__all__ = ["render", "render_stereo", "EngineError"]
