# taym -- C reference reader/writer (planned)

A C reference implementation of the TAYM format reader and writer, to give a
plain C consumer on a 16-bit machine a vetted starting point. Not yet written.

The format is designed for exactly this target (spec `../docs`):

- little-endian, byte-packed, fixed-size records;
- flat typed value pools; indices instead of pointers;
- no embedded schema language, no object reconstruction after loading chunks;
- zero-copy `memcpy`-into-packed-structs access is valid on a little-endian host
  with explicitly packed structs and compile-time size assertions (spec S3.1).

Scope when built: parse + validate (spec section 14) + write the core chunks.
The Python package (`../python`) is the behavioural oracle to test against --
its canonical sample and golden bytes are the cross-language fixtures.
