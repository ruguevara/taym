# taym -- C reference reader/writer and timer engine

This directory contains the plain C reference implementation for TAYM draft
0.1. Its scope:

- an owned-memory model for the draft-0.1 records and payload arrays;
- structural byte reader/writer;
- canonical writer chunk order matching the Python codec;
- the **timer engine** (S9..S13) as a reusable, allocation-free, chip-agnostic
  core;
- the AY's target rules, and a frame walker producing an AY chip's
  register-write timeline;
- no section-14 semantic validator;
- no audio rendering (the engine emits register writes; an emulator plays them).

The normative format remains `../docs/TAYM-format-draft-0.1.md`. The Python
package in `../python` remains the behavioral oracle: `make test` diffs the C
write timeline against it write for write.

## Layout

```text
include/taym/taym.h          public model, constants, and API
include/taym/taym_timers.h   timer engine, any chip: commands in, target writes out
include/taym/taym_ay.h       AY/YM: target writes -> register writes
include/taym/taym_player.h   frame walker: an AY chip of a file -> write timeline
src/taym.c                   reader/writer implementation
src/taym_timers.c            the engine (read next to python engine/engine.py)
src/taym_ay.c                R13, the 0x80 amplitude combine, the DAC curves
src/taym_player.c            frame loop, PSG background, background push
tests/test_roundtrip.c       structural round trip
tests/test_timers.c          engine and AY rules the fixtures don't reach
tests/taym_timeline.c        CLI: print the write timeline (the diff's C side)
Makefile
```

## Build and Test

```bash
make
make test
```

`make test` does three things: the structural round trip against the Python
canonical sample, the timer engine's own unit checks, and the **cross-language
diff** — for each fixture it runs the C `taym_timeline` and Python
`python -m taym writes`, and requires the two outputs to be identical.

The fixtures are the canonical sample, the audio demo, and
`python -m taym sample --engine`: a valid file built to walk an engine through
its rules (two timers, both clock modes, every MODULATE case, quiescence, the
0x80 pairing, a same-frame handoff, a rate that drops to zero). A new rule in
the engine gets a few frames in that fixture. The diff also runs on every tune
in `../examples`.

That diff is the point. The engine is the part of the format that is easy to
get subtly wrong (lane phase, the armed first write, register ownership), so
the C implementation is held to the oracle's exact output rather than to a
hand-written expectation.

The library has no runtime dependencies beyond the C standard library. It
decodes and encodes little-endian fields explicitly, so it does not require the
host to be little-endian and does not rely on packed structs for I/O.

## API Shape

```c
#include <taym/taym.h>

Taym taym;
TaymResult r = taym_read_file("song.taym", &taym);
if (r != TAYM_OK) {
    /* handle error */
}

uint8_t *bytes = NULL;
size_t size = 0;
r = taym_write_bytes(&taym, &bytes, &size);

taym_free_bytes(bytes);
taym_free(&taym);
```

`taym_read_*` performs structural parsing only. It does not check reserved
fields, enum validity, cross-reference ranges, target ownership, PSG frame
counts, or any other semantic rule from spec section 14. Use the Python
validator for those checks.

## The timer engine

`taym_timers.h` is the format's *behavior* with the file reading taken out:
feed it `TaymMods` commands and it tells you which targets get which values at
which absolute chip cycle. It is the piece a consumer actually needs.

It is chip-agnostic, as the format is. A target is an 8-bit id with a value up
to 32 bits wide, and the engine reports it raw, virtual targets included. What
a target means is the chip's half: `taym_ay.h` has the AY's, turning an
expiry's target writes into register writes (bytes, R13's four bits, the 0x80
amplitude combined into the volume register through the DAC curve). Another
chip gets a file like `taym_ay.c`; the engine does not change. The frame walker
is AY-only, since frame data is a per-chip format (a Bulba `.psg` here).

The engine is reusable in two very different hosts:

- **offline** — `taym_player_run()` walks a parsed `Taym` frame by frame,
  decodes the PSG background, and reports each frame's writes. This is what the
  oracle diff exercises.
- **real time** — a host (a DAW plugin, an Arduino) holds a `TaymTimerBank`,
  applies commands from its own source (UI, MIDI, a converted clip), and pulls
  the writes due in each audio block with `taym_timers_collect()`.

Properties that make the second case work:

- **No allocation and no I/O.** All state lives in `TaymTimerBank`, which the
  caller owns; lanes and actions are borrowed. Safe on an audio thread.
- **Absolute cycle timeline.** Expiries carry across frame *and* block
  boundaries, so how the caller cuts time never changes the writes. This is
  spec invariant 1, and the reason a 50 Hz frame that is not a whole number of
  cycles causes no phase glitch.
- **The caller owns the background.** The engine holds no register values; it
  reports which targets a timer owns (`taym_timers_owns_target()`) so the
  caller suppresses its own writes to them, per S13.1.

```c
TaymTimerBank bank;
size_t count;
taym_timers_init_from_taym(&bank, &taym, /*chip*/ 0, timer_map, &count);

taym_timers_command(&bank, timer, &mods, cycle_now);   /* START/MODULATE/STOP */

TaymTimerWrite writes[64];
size_t n;
taym_timers_collect(&bank, cycle_at_block_end, writes, 64, &n);   /* raw targets */
n = taym_ay_resolve(writes, n, taym.chips[0].variant);            /* AY registers */
for (size_t i = 0; i < n; i++)
    chip_write(writes[i].cycle, writes[i].target, writes[i].value);
```

The engine has no frames of its own. The one thing it needs to know about them
is `bank.no_rate_interval`, the length of a frame in cycles: a timer whose rate
is zero fires once per frame. The walker sets it for each frame; a live host
sets it once.

Each write says whether its expiry begins a repeat of the timer's output
(`loop_start`: every lane the timer runs is back on the element it loops to).
That is the period of what the timer plays, and what an oscilloscope should
trigger on; the engine is the only place that knows it per expiry.

An expiry is atomic and advances its lanes as it fires, so `collect` never
splits one: give it at least `TAYM_MAX_TIMER_TARGETS` slots, and if it returns
`TAYM_ERROR_RANGE` call again with the same end cycle for the rest.

Expiry positions are accumulated in `double` and rounded up to a whole cycle,
matching the Python oracle's arithmetic exactly so the two agree write for
write. `CHIP_PERIOD` intervals are whole cycles anyway; `ABS_RATE_HZ` ones are
`clock / rate` and genuinely fractional, which is the one place the rounding
has to be shared. A fixed-point rewrite is worth doing only together with the
oracle's.
