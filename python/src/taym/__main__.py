"""TAYM CLI:  python -m taym <command> [options] FILE

  validate FILE   run section-14 validation; exit 1 if any problem
  stats    FILE   counts, sizes, command histogram, per-timer activity
  dump     FILE   structural field-by-field dump (--timeline for decoded view;
                  --from/--to N|Ts limit the timeline frame range, e.g. 2.5s)
  sample   [OUT]  write the built-in canonical sample, or print it if no OUT
                  (--audio: an audible 1.5s tone+PWM demo instead)

  python -m taym sample song.taym && python -m taym dump --timeline song.taym

Render to WAV lives in its own entry point: python -m taym.engine -h
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import dump as _dump
from . import stats as _stats
from .codec import read_taym, write_taym
from .sample import build_model
from .validate import validate, validate_bytes


def _read(path):
    """Load FILE, returning (raw_bytes, model). Exits 2 on read/decode error."""
    try:
        data = Path(path).read_bytes()
    except OSError as e:
        _die(f"cannot read {path}: {e.strerror or e}")
    try:
        return data, read_taym(data)
    except Exception as e:  # malformed TAYM -> friendly message, not traceback
        _die(f"{path}: not a valid TAYM file: {e}")


def _die(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(2)


def cmd_validate(args):
    data, t = _read(args.file)
    problems = validate(t) + validate_bytes(data)
    if problems:
        print(f"INVALID: {len(problems)} problem(s)")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("valid")
    return 0


def cmd_stats(args):
    _, t = _read(args.file)
    print(_stats.format_stats(t))
    return 0


def cmd_dump(args):
    data, t = _read(args.file)
    if not args.timeline:
        if args.frm is not None or args.to is not None:
            _die("--from/--to require --timeline")
        print(_dump.structural(data))
        return 0
    fr = t.trak.frame_rate_hz
    try:
        first = None if args.frm is None else _dump.parse_frame_bound(args.frm, fr)
        last = None if args.to is None else _dump.parse_frame_bound(args.to, fr)
    except ValueError as e:
        _die(f"bad frame range: {e}")
    print(_dump.timeline(t, first, last, args.decode_tlan))
    return 0


def cmd_sample(args):
    if args.audio:
        from .sample import build_audio_demo
        data = write_taym(build_audio_demo())
        kind = "audio demo"
    else:
        data = write_taym(build_model())
        kind = "sample"
    if args.out:
        Path(args.out).write_bytes(data)
        print(f"wrote {args.out} ({len(data)} bytes, {kind})")
    else:
        print(_dump.structural(data))
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="python -m taym",
        description="TAYM inspection and authoring tools.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Render to WAV: python -m taym.engine -h",
    )
    sub = p.add_subparsers(dest="cmd", metavar="command")

    s = sub.add_parser("validate", help="run section-14 validation")
    s.add_argument("file", help="TAYM file to check")
    s.set_defaults(func=cmd_validate)

    s = sub.add_parser("stats", help="counts, sizes, histograms, per-timer activity")
    s.add_argument("file", help="TAYM file to summarize")
    s.set_defaults(func=cmd_stats)

    s = sub.add_parser("dump", help="structural field-by-field dump")
    s.add_argument("file", help="TAYM file to dump")
    s.add_argument("--timeline", action="store_true",
                   help="decoded timeline view instead of structural")
    s.add_argument("--from", dest="frm", metavar="N|Ts",
                   help="timeline: start at frame N or time Ts (e.g. 100, 2.5s)")
    s.add_argument("--to", dest="to", metavar="N|Ts",
                   help="timeline: end (inclusive) at frame N or time Ts")
    s.add_argument("--decode-tlan", action="store_true",
                   help="timeline: show tlan values decoded (Hz / x-multipliers) "
                        "instead of raw")
    s.set_defaults(func=cmd_dump)

    s = sub.add_parser("sample", help="write or print the built-in canonical sample")
    s.add_argument("out", nargs="?", help="output file (prints structural dump if omitted)")
    s.add_argument("--audio", action="store_true",
                   help="audible 1.5s tone+PWM demo instead of the canonical sample")
    s.set_defaults(func=cmd_sample)

    return p


def main(argv=None):
    p = build_parser()
    args = p.parse_args(sys.argv[1:] if argv is None else argv)
    if not getattr(args, "func", None):       # bare invocation -> list commands
        p.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
