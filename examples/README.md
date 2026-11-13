# Examples

Real tunes exported to TAYM. They are here to listen to, to read with the
tools, and to test against: every file in this directory must pass the
validator, and `cd c && make test` requires the C engine's register-write
timeline for it to match the Python engine's.

```bash
cd python
python -m taym stats ../examples/atarized.taym
python -m taym dump --timeline --to 2s ../examples/atarized.taym
python -m taym.engine ../examples/atarized.taym -o atarized.wav
```

## atarized.taym

**Atarized** by Otomata Labs, 2nd place in the chip music competition at
Xenium 2026: <https://demozoo.org/music/396811/>

Music by **Pator**, **nIkO** and **Alex Winston**.

One AY chip at 1773400 Hz, 3 minutes 10 seconds at 50 Hz, exported from
Bitphase. Three timers: about 400 STARTs, 5400 MODULATEs and 1145 timer lanes,
driving the volume registers and the envelope shape (R8, R9, R10, R13).
