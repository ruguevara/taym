"""Bulba .PSG decoder -- standalone, stdlib only.

TAYM embeds an unmodified Bulba .psg as a chip's frame-data chunk (spec S6.2).
The format body keeps PSG payloads opaque, but the reference engine and frame-
count validation need to decode them, so the decoder lives here.
"""
from __future__ import annotations

PSG_MAGIC = b"PSG\x1a"


def parse_psg(data: bytes) -> list[list[int]]:
    """Bulba .PSG -> list of absolute 14-byte register states, one per frame.

    $FF = next frame, $FE n = n repeat frames, reg/val pairs = deltas,
    $FD = end. Shadow carried across frames (delta semantics). R13 starts at
    0xFF = the don't-write sentinel."""
    if data[:4] != PSG_MAGIC:
        raise ValueError("not a .PSG (bad magic)")
    body = data[16:]
    shadow = [0] * 14
    shadow[13] = 0xFF
    frames: list[list[int]] = []
    started = False
    i = 0
    while i < len(body):
        b = body[i]
        if b == 0xFD:
            break
        if b == 0xFF:
            if started:
                frames.append(shadow[:])
            started = True
            i += 1
        elif b == 0xFE:
            n = body[i + 1]
            i += 2
            if not started:
                started = True
            for _ in range(n):
                frames.append(shadow[:])
        else:
            if b < 14:
                shadow[b] = body[i + 1]
            i += 2
    if started:
        frames.append(shadow[:])
    return frames


def psg_frame_count(data: bytes) -> int:
    """Count frames without materializing register state (S6.2 validation)."""
    body, i, n = data[16:], 0, 0
    while i < len(body):
        b = body[i]
        if b == 0xFD:
            break
        if b == 0xFF:
            n += 1
            i += 1
        elif b == 0xFE:
            n += body[i + 1]
            i += 2
        else:
            i += 2
    return n
