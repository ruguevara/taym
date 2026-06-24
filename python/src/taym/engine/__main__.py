"""Render a TAYM file to WAV via the reference engine.

  python -m taym.engine song.taym -o song.wav
  python -m taym.engine song.taym --sr 44100
"""
from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path

from .engine import DEFAULT_STEREO_WIDTH, EngineError, render, render_stereo


def build_parser():
    ap = argparse.ArgumentParser(
        prog="python -m taym.engine",
        description="TAYM -> WAV (reference engine).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="  python -m taym.engine song.taym -o song.wav\n"
               "  python -m taym.engine song.taym --sr 44100",
    )
    ap.add_argument("taym", nargs="?", help="TAYM file to render")
    ap.add_argument("-o", "--out", help="output WAV (default: <taym stem>.wav)")
    ap.add_argument("--sr", type=int, default=44100, help="sample rate (default 44100)")
    ap.add_argument("--chip", type=int, default=0, help="chip index to render (default 0)")
    ap.add_argument("--mono", action="store_true",
                    help="render a 1-channel WAV (default: 2-channel, per CHIP.config layout A.1)")
    ap.add_argument("--no-dc", action="store_true",
                    help="bypass Ayumi's DC-blocking filter (raw DAC output)")
    ap.add_argument("--width", type=float, default=DEFAULT_STEREO_WIDTH,
                    help="stereo separation of side channels from center "
                         "(0.0=mono, 0.5=hard pan; default %(default).2f, matches Bitphase)")
    return ap


def main():
    ap = build_parser()
    args = ap.parse_args()
    if args.taym is None:                      # bare invocation -> full help
        ap.print_help()
        return 0

    try:
        import numpy as np
        remove_dc = not args.no_dc
        if args.mono:
            sig = render(args.taym, sample_rate=args.sr, chip_index=args.chip, remove_dc=remove_dc)
        else:
            sigL, sigR = render_stereo(
                args.taym, sample_rate=args.sr, chip_index=args.chip, remove_dc=remove_dc,
                stereo_width=args.width
            )
    except EngineError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if args.mono:
        pcm = np.clip(sig, -1.0, 1.0)
        nch, nframes = 1, len(sig)
    else:
        # interleave L,R per frame for a 2-channel WAV
        pcm = np.clip(np.stack([sigL, sigR], axis=1), -1.0, 1.0)
        nch, nframes = 2, len(sigL)
    pcm16 = (pcm * 32767).astype("<i2")
    out = args.out or str(Path(args.taym).with_suffix(".wav"))
    with wave.open(out, "w") as w:
        w.setnchannels(nch)
        w.setsampwidth(2)
        w.setframerate(args.sr)
        w.writeframes(pcm16.tobytes())
    print(f"wrote {out}: {nframes} frames x {nch}ch, {nframes / args.sr:.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
