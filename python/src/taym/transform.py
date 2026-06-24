"""TAYM model transforms -- pure functions Taym -> Taym.

Operate on the parsed model only (no codec/format knowledge). Each returns a
new Taym; the input is left untouched. Derived counts (timer_count) re-derive
from the array lengths on write, so transforms only touch the arrays.
"""
from __future__ import annotations

from dataclasses import replace

from . import spec
from .model import Taym


def drop_empty_timers(t: Taym) -> Taym:
    """Drop timers whose MODS column is EMPTY for every frame, re-striding the
    row-major (frame x timer) MODS grid to the surviving columns.

    Shared pools (lanes/tlanes/actions, vu*) are referenced by the kept MODS
    and so are left as-is. Returns a new Taym; if nothing is empty, an
    equivalent copy. A MODS-count mismatch (not frame_count*timer_count) is
    left untouched -- nothing to re-stride safely.
    """
    nt = len(t.timers)
    fc = t.trak.frame_count
    if not nt or len(t.mods) != fc * nt:
        return replace(t)
    keep = [ti for ti in range(nt)
            if any(t.mods[f * nt + ti].command != spec.CMD_EMPTY for f in range(fc))]
    if len(keep) == nt:
        return replace(t)
    timers = [t.timers[ti] for ti in keep]
    mods = [t.mods[f * nt + ti] for f in range(fc) for ti in keep]
    return replace(t, timers=timers, mods=mods)
