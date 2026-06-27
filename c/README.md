# taym -- C reference reader/writer

This directory contains the plain C reference implementation for TAYM draft
0.1. Its scope is intentionally narrow:

- an owned-memory model for the draft-0.1 records and payload arrays;
- structural byte reader/writer;
- canonical writer chunk order matching the Python codec;
- no section-14 semantic validator;
- no renderer or playback engine.

The normative format remains `../docs/TAYM-format-draft-0.1.md`. The Python
package in `../python` remains the behavioral oracle, and the C tests compare
against the Python canonical sample.

## Layout

```text
include/taym/taym.h   public model, constants, and API
src/taym.c            reader/writer implementation
tests/test_roundtrip.c
Makefile
```

## Build and Test

```bash
make
make test
```

`make test` builds the C test binary, asks the Python package to emit the
canonical sample fixture, then checks C read/write stability and manual C model
packing against those bytes.

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
