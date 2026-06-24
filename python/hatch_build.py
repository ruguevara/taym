"""Custom hatchling build hook: vendor the repo-root spec docs into the package.

The normative spec lives once at the repo root (`../docs`), shared by all
language implementations. We want a copy inside the wheel/sdist at
`taym/docs/` so the spec travels with `pip install taym`.

A plain `force-include = {"../docs/..." = ...}` breaks the standard
sdist -> wheel build flow: the sdist drops the docs at `src/taym/docs/` but the
unpacked sdist has no `../docs` for the wheel step to force-include from. So
instead we copy the docs into `src/taym/docs/` before the build. The copy is
idempotent and is picked up as ordinary package data on every build path
(direct wheel, sdist, and wheel-from-sdist, where the files already exist).
"""
from __future__ import annotations

import shutil
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

# Repo-root docs to vendor, relative to this file (python/). When building from
# an unpacked sdist there is no ../docs and the files already live in the dest;
# the hook then no-ops.
_DOCS = ["TAYM-format-draft-0.1.md", "TAYM-overview.md"]


class VendorDocsHook(BuildHookInterface):
    PLUGIN_NAME = "vendor-docs"

    def initialize(self, version, build_data):
        here = Path(self.root)
        src_dir = here.parent / "docs"
        dest_dir = here / "src" / "taym" / "docs"
        dest_dir.mkdir(parents=True, exist_ok=True)
        for name in _DOCS:
            src = src_dir / name
            if src.is_file():
                shutil.copy2(src, dest_dir / name)
