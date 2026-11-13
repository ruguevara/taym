"""Bulba .PSG decoder -- standalone, stdlib only.

TAYM embeds an unmodified Bulba .psg as a chip's frame-data chunk (spec S6.2).
The format body keeps PSG payloads opaque, but the reference engine and frame-
count validation need to decode them, so the decoder lives here.
"""
from __future__ import annotations

PSG_MAGIC = b"PSG\x1a"

# A frame's R13 when the stream does not write R13 in that frame.
R13_NO_WRITE = 0xFF


def parse_psg(data: bytes) -> list[list[int]]:
    """Bulba .PSG -> list of 14-byte register frames, one per frame.

    $FF = next frame, $FE n = n repeat frames, reg/val pairs = deltas,
    $FD = end. R0..R12 are states: the shadow carries across frames (delta
    semantics). R13 is an event, because every write of it restarts the
    envelope, the same shape too (S6.2): a frame's R13 is the value written in
    that frame, and R13_NO_WRITE in a frame that does not write it."""
    if data[:4] != PSG_MAGIC:
        raise ValueError("not a .PSG (bad magic)")
    body = data[16:]
    shadow = [0] * 14
    shadow[13] = R13_NO_WRITE
    frames: list[list[int]] = []

    def commit():
        frames.append(shadow[:])
        shadow[13] = R13_NO_WRITE

    started = False
    i = 0
    while i < len(body):
        b = body[i]
        if b == 0xFD:
            break
        if b == 0xFF:
            if started:
                commit()
            started = True
            i += 1
        elif b == 0xFE:
            n = body[i + 1]
            i += 2
            if not started:
                started = True
            for _ in range(n):
                commit()
        else:
            if b < 14:
                shadow[b] = body[i + 1]
            i += 2
    if started:
        commit()
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
