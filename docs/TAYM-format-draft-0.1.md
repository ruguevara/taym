# TAYM interchange format -- draft 0.1

Status: **discussion draft**

TAYM is a chip-oriented music interchange format. Trackers and synthesis
tools export TAYM; platform-specific converters turn it into their own runtime
formats. Direct multiplatform playback is not a design goal.

The format is designed to remain practical for a conventional C reader on a
16-bit system:

- little-endian, byte-packed records;
- fixed-size timeline records;
- flat typed value pools;
- indices rather than pointers;
- no embedded schema language;
- no object reconstruction after loading the chunks.

This draft consolidates the earlier model notes into a single normative
specification. Where it tightens or corrects an earlier note, this draft is
authoritative.

## 1. Scalar conventions

All multibyte integers are little-endian.

```text
u8   unsigned 8-bit integer
u16  unsigned 16-bit integer
u32  unsigned 32-bit integer
```

Unsigned 16.16 fixed point stores a value as:

```text
encoded = round(value * 65536)
value   = encoded / 65536
```

All records and chunks are byte-packed. Reserved fields must be zero.

### 1.1 Enumerations

Every enum below is stored in the byte width of its field and is closed: a
reader rejects any value not listed here (or, where a range is marked
*reserved*, treats it as invalid until a later version assigns it). Sentinel
constants (`0xFFFFFFFF`, `0xFFFFFFFE`) are operations, not enum values, and are
called out where used.

| Enum          | Field / width                | Values (see section)                                |
| ------------- | ---------------------------- | --------------------------------------------------- |
| `clock_mode`  | `TIMR.clock_mode` u8         | 0 ABS_RATE_HZ, 1 CHIP_PERIOD (S7)                   |
| `value_type`  | `LANE.value_type` u8         | 0 invalid, 1 U8, 2 U16, 3 U32, 4..255 reserved (S9) |
| `timing_mode` | `TLAN.timing_mode` u8        | 0 ABSOLUTE, 1 RELATIVE (S10)                        |
| `source_mode` | `ACTN.source_mode` u8        | 0 INLINE_VALUE, 1 BIND_LANE (S11)                   |
| `command`     | `MODS.command` u8            | 0 EMPTY, 1 START, 2 MODULATE, 3 STOP (S12)          |

`chip_type_id` (u8, section 6) and `target_id` (u8, section 11 hardware range)
are assigned by chip and target registries (appendix A defines AY). A reader for
a standardized chip rejects an unknown `target_id`; private contracts use
private `chip_type_id` values.

## 2. File header

The file begins with a fixed 16-byte header:

| Off | Size | Field       | Type / rule                                       |
| ---:| ---: | ----------- | ------------------------------------------------- |
|   0 |    4 | magic       | `TAYM` (`54 41 59 4D`)                            |
|   4 |    2 | version     | u16, `1`                                          |
|   6 |    2 | header_size | u16, `16`                                         |
|   8 |    4 | flags       | u32, zero in draft 0.1                            |
|  12 |    4 | chunk_bytes | u32, exact byte size of the complete chunk stream |

The chunk stream starts at `header_size`. The file ends at:

```text
header_size + chunk_bytes
```

`chunk_bytes` is mandatory and nonzero. Reading to EOF is not an alternative.
Trailing bytes are invalid. There is no file checksum.

`chunk_bytes` is u32, so the chunk stream is at most 4,294,967,295 bytes. The
file extent is `header_size + chunk_bytes`.

## 3. Chunk container

Every chunk has an 8-byte header:

| Off | Size | Field   | Type / rule                                      |
| ---:| ---: | ------- | ------------------------------------------------ |
|   0 |    4 | tag     | four ASCII uppercase letters or digits           |
|   4 |    4 | size    | u32 payload byte count, excluding this header    |
|   8 | size | payload | chunk-specific bytes                             |

There is no inter-chunk alignment or padding. The next chunk begins
immediately after the previous payload.

Every chunk tag is unique within a file. Readers accept chunks in any order
and skip unknown chunks using `size`.

Canonical writers use this order:

```text
TRAK
INFO        optional
CHIP
TIMR
MODS
ACTN
LANE
TLAN
VU08
VU16
VU32
referenced frame-data chunks
extension chunks
```

The following core chunks occur exactly once:

```text
TRAK CHIP TIMR MODS ACTN LANE TLAN VU08 VU16 VU32
```

`INFO` occurs at most once.

Empty array and value-pool chunks remain present with a zero-sized payload.
`TRAK` always contains one record. `CHIP`, `TIMR`, and `MODS` sizes follow the
counts in `TRAK`.

Tags named in section 16 are reserved but not defined in draft 0.1.

### 3.1 Direct array and memory-mapped access

After walking the chunk headers once, each core payload is a flat array with a
documented record stride. Records contain no pointers, relocations,
per-record lengths, or variable tails. Cross-references are integer indices
into other chunk arrays.

A reader may therefore:

- memory-map the file and keep chunk payloads in place;
- read a complete payload into an array of packed C structs; or
- retain byte pointers and decode fields only when used.

## 4. Track timeline -- `TRAK`

`TRAK` contains one 16-byte record:

| Off | Size | Field       | Type / rule                                  |
| ---:| ---: | ----------- | -------------------------------------------- |
|   0 |    4 | frame_rate  | u32 unsigned 16.16 Hz                        |
|   4 |    4 | frame_count | u32, nonzero                                 |
|   8 |    4 | loop_frame  | u32 frame index, or `0xFFFFFFFF` = no loop   |
|  12 |    1 | chip_count  | u8                                           |
|  13 |    1 | timer_count | u8                                           |
|  14 |    2 | reserved    | zero                                         |

`frame_rate` is nonzero. If present, `loop_frame` is less than
`frame_count`.

All chips, frame-data streams, and timer mods share this one frame timeline.
A referenced frame-data stream decodes to exactly `frame_count` frames.

When `loop_frame` is present:

- playback jumps from the end of the track to `loop_frame`;
- every timer's `MODS` record at `loop_frame` is `START` or `STOP`.

This re-establishes timer state at loop entry. Frame-data streams need not
keyframe `loop_frame`; consumers reconstruct background register state by
decoding the stream up to that frame.

## 5. Optional metadata -- `INFO`

`INFO` is semantically irrelevant UTF-8 metadata:

```text
key=value\0
key=value\0
...
\0
```

Keys are lowercase ASCII identifiers. Conventional initial keys are:

```text
title
author
system
tracker
comment
```

Readers may ignore or preserve unknown keys.

Values are arbitrary UTF-8, so `INFO` (`title`, `author`, ...) is the place for
non-ASCII naming. `CHIP.name` (section 6) is a printable-ASCII hardware label,
not a display title -- producers must not put UTF-8 there.

## 6. Chip instances -- `CHIP`

`CHIP` is an array of 32-byte records:

| Off | Size | Field          | Type / rule                                        |
| ---:| ---: | -------------- | -------------------------------------------------- |
|   0 |    4 | clock_hz       | u32 chip master clock in integer Hz                |
|   4 |    1 | chip_type_id   | u8 standardized or private chip type               |
|   5 |    1 | variant        | u8 behavioral pick within type (A.1); 0 = default  |
|   6 |    2 | reserved       | zero                                               |
|   8 |   16 | name           | printable ASCII, NUL-padded                        |
|  24 |    4 | frame_data_tag | chunk tag, or four zero bytes                      |
|  28 |    4 | config         | u32 chip-type-private config bitfield; 0 = default |

The number of records equals `TRAK.chip_count`. Chip index is the record
index.

`clock_hz` is an integer chip master clock. `name` is informational and may
occupy all 16 bytes without a terminator.

`variant` selects registry-defined behavior within one `chip_type_id`; `0` is
the family default. `config` is a registry-defined per-instance bitfield; bits
a chip type does not define are reserved zero.

Chip type ID ranges:

```text
0x00        invalid
0x01..0x7F  standardized TAYM chip types
0x80..0xFF  private/experimental chip types
```

The standardized range is registry-assigned, not open-ended. Draft 0.1 fixes
one standardized entry, the AY family, in appendix A.

Multiple chip instances may use the same type ID. Turbo Sound, for example,
uses two AY records. Timers refer to the chip-instance index.

### 6.1 Frame-data association

A zero `frame_data_tag` means that the chip has no frame-data stream.

A nonzero tag identifies either:

1. an embedded chunk with that exact tag; or
2. an external sidecar inferred from the TAYM filename.

Nonzero frame-data tags are unique across chip records and may not reuse core
or `INFO` tags. Chunk order has no association semantics.

### 6.2 AY-compatible frame data

For AY-compatible chips, draft 0.1 uses an unmodified standard Bulba `.psg`
file:

- the payload begins with its own 16-byte `PSG\x1a` header;
- the payload includes the standard `$FD` terminator;
- TAYM adds no wrapper inside the chunk;
- the decoded frame count equals `TRAK.frame_count`.

Conventional tags are `PSG0`, `PSG1`, and so on.

If the chunk is absent, the external filename is:

```text
<taym-stem>.<frame_data_tag>.psg
```

Example:

```text
song.taym + PSG0 -> song.PSG0.psg
```

The tag is copied into the filename verbatim, preserving its case. Tags are
uppercase letters/digits (section 3), so the sidecar component is uppercase;
matching is case-sensitive where the host filesystem is.

The Bulba stream preserves the register writes present in each frame,
including write-sensitive repeated writes represented by the source.

Embedding an unmodified `.psg` preserves write-sensitive repeated writes. The
stream has no keyframes, so consumers decode linearly to seek or reconstruct a
loop point.

Other chip-specific frame-data payloads are outside draft 0.1.

## 7. Timer definitions -- `TIMR`

Each timer belongs to exactly one chip instance. `TIMR` is an array of 6-byte
records:

| Off | Size | Field         | Type / rule                         |
| ---:| ---: | ------------- | ----------------------------------- |
|   0 |    2 | clock_divider | u16                                 |
|   2 |    1 | chip_index    | u8 index into `CHIP`                |
|   3 |    1 | clock_mode    | u8                                  |
|   4 |    2 | reserved      | zero                                |

The number of records equals `TRAK.timer_count`. Timer index is the record
index. Timers have no stored names.

`clock_mode`:

```text
0  ABS_RATE_HZ
1  CHIP_PERIOD
```

For `ABS_RATE_HZ`:

- base and absolute timer-lane values are unsigned 16.16 Hz;
- `clock_divider` is zero;
- values must fit unsigned 16.16, so the rate ceiling is just under 65536 Hz.

For `CHIP_PERIOD`:

- base and absolute timer-lane values are unsigned integer periods;
- `clock_divider` is nonzero;
- the referenced chip's `clock_hz` is nonzero;
- logical timer rate is:

```text
rate_hz = chip.clock_hz / (clock_divider * period)
```

Examples include divider 16 for AY tone-like timing and divider 8 for an
exported doubled tone rate.

Unsigned 16.16 resolves to ~1.5e-5 Hz and tops out just below 65536 Hz, which
covers frame rates and absolute timer rates for AY/SID-class chips. A target
needing timer rates above that ceiling uses `CHIP_PERIOD`, whose rate is not
bounded by 16.16.

Zero is not a valid active rate, period, or relative multiplier.

## 8. Shared scalar pools

The value chunks are flat logical arrays:

| Tag    | Element | Payload rule                      |
| ------ | ------- | --------------------------------- |
| `VU08` | u8      | raw bytes                         |
| `VU16` | u16     | little-endian; size multiple of 2 |
| `VU32` | u32     | little-endian; size multiple of 4 |

Descriptor offsets are element indices, not byte offsets.

Draft 0.1 supports unsigned 8-, 16-, and 32-bit scalar types. Other type codes
and matching pools are reserved.

## 9. Value lanes -- `LANE`

`LANE` is an array of anonymous immutable 16-byte descriptors:

| Off | Size | Field        | Type / rule                                  |
| ---:| ---: | ------------ | -------------------------------------------- |
|   0 |    4 | value_offset | u32 element index in the selected pool       |
|   4 |    4 | length       | u32, nonzero                                 |
|   8 |    4 | loop_index   | lane-relative, or `0xFFFFFFFF` = no loop     |
|  12 |    1 | value_type   | u8                                           |
|  13 |    3 | reserved     | zero                                         |

`value_type`:

```text
0  invalid
1  U8  -> VU08
2  U16 -> VU16
3  U32 -> VU32
4..255 reserved
```

If present, `loop_index` is in `0..length-1`. The selected value slice must be
within its pool.

A lane does not identify a target. Its type is checked when an action binds it
to a target defined by the owning chip type. One lane may therefore be shared
by multiple compatible targets. Every binding has an independent running
index.

### 9.1 Value-lane completion

A looping lane advances from its final element to `loop_index`.

A no-loop lane writes its final value once, then becomes dormant. It retains
its final logical value and ownership but performs no further writes.

This is observably different from looping on the final element, which rewrites
the value on every timer expiry and may retrigger a write-sensitive target.

A one-shot sample may end with an explicit zero and use no loop.

## 10. Timer lanes -- `TLAN`

Timer lanes are separate from value lanes (`LANE`) because they carry timing
semantics: timing mode, coupling to the owning timer's clock mode, a fixed
`VU32` pool, and no-loop quiescence (section 10.2). A `LANE` of type `U32` and
a `TLAN` descriptor may read from the same `VU32` pool; each descriptor carries
its own slice.

`TLAN` is an array of 16-byte descriptors over `VU32`:

| Off | Size | Field        | Type / rule                                  |
| ---:| ---: | ------------ | -------------------------------------------- |
|   0 |    4 | value_offset | u32 element index in `VU32`                  |
|   4 |    4 | length       | u32, nonzero                                 |
|   8 |    4 | loop_index   | lane-relative, or `0xFFFFFFFF` = no loop     |
|  12 |    1 | timing_mode  | u8                                           |
|  13 |    3 | reserved     | zero                                         |

`timing_mode`:

```text
0  ABSOLUTE
1  RELATIVE
```

An `ABSOLUTE` lane interprets its elements according to the owning timer's
`clock_mode`. It may be shared only by timers with the same clock mode.

A `RELATIVE` lane contains unsigned 16.16 multipliers:

```text
effective_rate = base_rate * multiplier
```

For a `CHIP_PERIOD` timer, the base rate is first derived from the stored
period and chip clock. The multiplier does not directly multiply the encoded
period.

For an `ABS_RATE_HZ` timer, `effective_rate` must fit unsigned 16.16 Hz. Higher
effective rates require `CHIP_PERIOD`.

The persistent base and timer lane compose as:

```text
no timer lane  -> effective rate comes from the base
ABSOLUTE       -> effective rate comes from the lane
RELATIVE       -> effective rate = base rate * lane multiplier
```

An absolute lane overrides but does not erase the persistent base.

### 10.1 Timer-step meaning

A timer-lane value describes the interval during which every target lane's
current step is active:

```text
START:
    apply target step 0
    select timer step 0
    wait for that interval

expiry:
    advance each active target lane independently
    advance the timer lane independently
    write the newly selected target values
    wait for the newly selected interval
```

The lanes may have different lengths and loop points. Every binding maintains
its own index.

The expiry is one atomic logical transition across target values and timing.

### 10.2 Timer-lane completion

A no-loop timer lane runs its final interval and then makes the timer
quiescent. The final boundary performs no lane advance and no target write.

The timer retains ownership and final target values but generates no more
expiries. Quiescent is not stopped: a `STOP` is still required to release
ownership. `MODULATE` is invalid while quiescent. Only `START` or `STOP` may
follow.

Looping on the final timer element explicitly means to continue indefinitely
using that interval.

## 11. Target actions -- `ACTN`

`ACTN` is an array of packed 6-byte records:

| Off | Size | Field       | Type / rule                          |
| ---:| ---: | ----------- | ------------------------------------ |
|   0 |    4 | operand     | u32 inline scalar or `LANE` index    |
|   4 |    1 | target_id   | u8 chip-local target ID              |
|   5 |    1 | source_mode | u8                                    |

`source_mode`:

```text
0  INLINE_VALUE
1  BIND_LANE
```

`INLINE_VALUE` is a persistent timer source. It is written on `START` and
every subsequent timer expiry. For U8 and U16 targets, unused high operand
bits are zero.

`BIND_LANE` selects a `LANE` descriptor using `operand`.

There are no one-shot timer writes. Ordinary once-per-frame writes belong in
the chip's frame-data stream.

Target IDs are chip-local. The 8-bit space is split:

```text
0x00..0x7F  hardware registers      real chip registers (e.g. AY R0..R13),
                                    standardized per chip type
0x80..0xBF  format-specified virtual engine-interpreted targets with a
                                    format-wide meaning, defined below:
              0x80  sample amplitude
              0x81  sample index
              0x82  sample rate
              0x83..0xBF  reserved for future format-specified virtual targets
0xC0..0xFF  engine-interpreted      assigned by the chip registry, or by a
                                    private chip type's producer/consumer
                                    contract
```

A virtual target is not a hardware register; it modulates an engine-level
parameter the frame-data stream cannot reach. This is what lets a sample's
amplitude, index, or rate be driven by a lane independently of its sample data.

The hardware range is standardized per chip type by the separate target
registry (the AY assignments are in appendix A). The format-specified virtual
range has a fixed, chip-independent meaning (`0x80..0x82` defined here;
`0x83..0xBF` reserved -- invalid until a later version assigns them). The
engine-interpreted range is valid only when assigned by the chip registry, or
when used with a private chip type.

Unknown target IDs are invalid for a standardized chip. There is no
producer-private target-ID range for standardized chip types.

Within every referenced action slice:

- records are strictly sorted by `target_id`;
- duplicate target IDs are invalid.

Identical lanes or action slices may be shared. Deduplication is optional.

`first_action == 0` is not a null sentinel: a `START` whose slice begins at
`ACTN[0]` is a normal case, disambiguated by `action_count` (a `START` always
has `action_count >= 1`). The only null index sentinel in draft 0.1 is
`loop_index == 0xFFFFFFFF`.

## 12. Timer-mod timeline -- `MODS`

`MODS` is a fixed frame-major record array:

```text
record_index = frame_index * timer_count + timer_index
record_count = frame_count * timer_count
```

Each (timer, frame) has exactly one record, so a timer carries exactly one
command per frame. There is no within-frame command ambiguity for a single
timer; the same-frame ordering in section 13.2 is only across timers.

The fixed array is the canonical form because it gives O(1) random access to
any (timer, frame) with no decompression.

Each record is 16 bytes:

| Off | Size | Field            | Type / rule              |
| ---:| ---: | ---------------- | ------------------------ |
|   0 |    4 | base_timer_value | u32                      |
|   4 |    4 | timer_lane_ref   | u32, `TLAN` index or op   |
|   8 |    4 | first_action     | u32 index into `ACTN`    |
|  12 |    1 | action_count     | u8                       |
|  13 |    1 | command          | u8                       |
|  14 |    2 | reserved         | zero                     |

`command`:

```text
0  EMPTY
1  START
2  MODULATE
3  STOP
```

`timer_lane_ref` encodes the timer-lane operation directly: ordinary indices
select a `TLAN` descriptor (`BIND`), and two high sentinels stand for the
no-index operations:

```text
0xFFFFFFFF  NONE       no timer lane; use the persistent base directly
0xFFFFFFFE  UNCHANGED  keep the current timer-lane state (MODULATE only)
otherwise   BIND       select this `TLAN` index
```

When `action_count` is zero, `first_action` is zero.

Index zero is valid. The null/operation sentinels are `0xFFFFFFFF` (also the
no-loop `loop_index`), and `0xFFFFFFFE` for `timer_lane_ref` only.

Which fields a record carries depends on `command`. `base_timer_value`,
`timer_lane_ref`, `first_action`, and `action_count` are interpreted only by
`START` and `MODULATE`. For `EMPTY` and `STOP` these fields are not indices and
are not range-checked: they are simply ignored and a canonical writer zeroes
them. A zeroed `EMPTY`/`STOP` record therefore has `timer_lane_ref == 0`, which
is *not* read as `BIND TLAN[0]` because the command consumes no timer-lane
operation. Only `reserved` is validated as a zero field for these commands.

All timers are stopped before frame 0 is processed.

### 12.1 `EMPTY`

`EMPTY` leaves all timer state unchanged. A canonical writer zeroes every other
field; a reader ignores them (only `reserved` is validated as zero).

### 12.2 `START`

`START` completely replaces the timer state:

- release all previous target ownership and lane bindings;
- install the complete new target-source set;
- reset all bound target lanes to index 0;
- write every initial inline value or lane element 0 immediately;
- install either constant base timing or timer-lane element 0;
- begin the first interval.

Rules:

- `base_timer_value` is nonzero;
- `timer_lane_ref` is `NONE` or a valid `TLAN` index (`UNCHANGED` is invalid on
  `START`);
- `action_count` is at least one;
- the action slice is the complete owned target set.

`START` while active is an explicit complete replacement and retrigger.

### 12.3 `MODULATE`

`MODULATE` patches running state without restarting it:

- zero `base_timer_value` means unchanged;
- a nonzero base replaces the persistent base;
- `timer_lane_ref == UNCHANGED` preserves timer-lane state;
- `timer_lane_ref == NONE` removes the timer lane;
- an ordinary `timer_lane_ref` index replaces or installs a timer lane;
- named actions replace sources only for existing owned targets.

`MODULATE` cannot add or remove targets. Changing the owned target set
requires `START` or `STOP`.

Source changes are immediate state changes, but `MODULATE` performs no target
writes. New output becomes observable at the next timer expiry.

When a lane replaces an inline source:

- the new lane starts at index 0;
- element 0 is pending;
- the next expiry writes element 0 without first advancing to element 1.

When a lane replaces an active lane:

- the current index is preserved;
- old and new descriptors have identical `length` and `loop_index`;
- no immediate target write occurs;
- the next expiry advances normally and writes from the replacement lane.

The same shape and phase-preservation rule applies to active timer-lane
replacement.

When a timer lane is installed where none was active, element 0 defines the
current interval immediately. Replacing an active timer lane makes the new
lane's value at the preserved index the requested current interval.

Handling of an interval already in progress is target-dependent. `MODULATE`
preserves logical lane phase and must not retrigger; only the realized hardware
interval boundary is target-defined.

A `MODULATE` that changes nothing is non-canonical and writers should emit
`EMPTY` instead. This is a writer recommendation, not a reader requirement: a
reader tolerates a no-op `MODULATE` and the validator does not reject it.

`MODULATE` is invalid on an inactive or quiescent timer.

### 12.4 `STOP`

`STOP` stops the timer, clears its state, and releases all targets. A canonical
writer zeroes every other field; a reader ignores them (only `reserved` is
validated as zero).

`STOP` is idempotent. It may be used on an already stopped timer, allowing
explicit inactive-state reconstruction at `loop_frame`.

## 13. Ownership and frame processing

Within one timer, each target has exactly one persistent source. Across all
timers belonging to one chip, a target may be owned by at most one active
timer.

Every normal timer expiry is logically atomic:

1. activate a newly bound pending lane at element 0, or advance each other
   non-dormant value lane;
2. select each target's inline or lane value;
3. advance/select timer timing;
4. write the target values and establish the next interval together.

The final boundary of a no-loop timer lane is the exception: it only makes the
timer quiescent.

### 13.1 Frame-data background state

Frame data and timers interact in this order:

1. Decode this frame's chip writes into background state.
2. Resolve timer commands transactionally.
3. Suppress ordinary hardware writes for targets owned after the transaction.
4. Write initial values for new `START`s.
5. For released targets with no new owner, expose the current background
   value.

Background state continues to update while a timer owns a target. `STOP`
therefore restores the current background value rather than a stale value.

If a chip has no frame-data stream, releasing a target performs no replacement
background write.

### 13.2 Same-frame ownership handoff

Timer commands in one frame are resolved without depending on timer index:

1. release targets from `STOP` and replaced `START` states;
2. validate and install all `START` acquisitions;
3. apply `MODULATE`s;
4. write initial values for new starts.

This permits timer A to release a target while timer B acquires it in the same
frame. Two starts claiming the same target are invalid.

## 14. Validation requirements

A draft-0.1 validator rejects at least:

- a bad magic, version, or header size;
- a header or chunk extending outside the declared file;
- trailing bytes after `chunk_bytes`;
- duplicate chunk tags;
- a missing core chunk;
- a core record chunk whose size is not a multiple of its stride;
- `TRAK` counts inconsistent with `CHIP`, `TIMR`, or `MODS`;
- zero frame rate/count or an invalid loop frame;
- a nonzero reserved field;
- an unsupported enum or scalar type;
- an undefined standardized `chip_type_id`;
- an out-of-range chip, action, target, lane, timer-lane, or pool reference (a
  `timer_lane_ref` of `0xFFFFFFFE`/`0xFFFFFFFF` is an operation, not an index);
- a zero-length lane;
- an out-of-range loop index or value-pool slice;
- an invalid clock-mode/divider combination;
- a `CHIP_PERIOD` timer whose referenced chip has a zero `clock_hz`;
- an `ABS_RATE_HZ` base or absolute lane value that does not fit unsigned 16.16;
- an `ABS_RATE_HZ` relative timer lane whose effective rate does not fit
  unsigned 16.16;
- a zero active base, rate, period, or relative multiplier;
- an absolute timer lane shared across different clock modes;
- a `START` with no target actions, or a `START` whose `timer_lane_ref` is
  `UNCHANGED`;
- an unsorted or duplicate target in an action slice;
- a target/lane scalar-type mismatch;
- concurrent ownership of one target by multiple timers;
- a `MODULATE` naming a target not established by the active `START`;
- phase-preserving replacement with a different length or loop point;
- `MODULATE` on an inactive or quiescent timer;
- malformed timer reconstruction at `loop_frame`;
- a referenced frame-data stream with the wrong frame count;
- repeated nonzero frame-data tags.

## 15. Examples

### 15.1 Two-step PWM

```text
value lane: [15, 0], loop=0
timer lane: [25, 75], loop=0
```

```text
START    write 15, wait 25
expiry   write 0,  wait 75
expiry   write 15, wait 25
...
```

Replacing the timer lane with `[30, 60]` through `MODULATE` preserves its
logical index. A DDS converter may retime immediately; a basic hardware timer
may finish the old interval.

### 15.2 Turbo Sound

```text
CHIP[0] = { chip_type_id=0x01, name="AY-A", frame_data_tag="PSG0" }
CHIP[1] = { chip_type_id=0x01, name="AY-B", frame_data_tag="PSG1" }
```

Timers refer to chip index 0 or 1. The `PSG0` and `PSG1` chunks may occur
anywhere in the file, or resolve to `song.PSG0.psg` and `song.PSG1.psg`.

## Appendix A. AY-3-8910 / YM2149 registry (normative for draft 0.1)

The chip and target registries are external to the format body (sections 6 and
11), but draft 0.1 fixes one concrete chip so that producers and consumers can
interoperate without a second document. This appendix is that registry entry.

### A.1 Chip type

Standardized `chip_type_id` values (u8). `0x01` is fully defined by this
appendix (A.2/A.3); the rest are assigned reserved names whose target registries
arrive in later drafts. Experiments use the private range (`0x80..0xFF`).

```text
PSG family
  0x01  AY        AY-3-8910 / YM2149 family   (defined, A.2/A.3)
  0x02  SN76489   TI DCSG; SMS / BBC / ColecoVision
  0x03  SAA1099   Philips; Sam Coupe
Console custom
  0x04  SID       MOS 6581/8580
  0x05  POKEY     Atari 8-bit / arcade
  0x06  NES APU   RP2A03/2A07
  0x07  GB APU    Game Boy DMG/CGB
  0x08  HuC6280   PC Engine / TurboGrafx
Yamaha FM
  0x09  YM2612    OPN2; Sega Genesis FM
  0x0A  YM2151    OPM; arcade / X68000
  0x0B  YM2203    OPN; FM + AY-style SSG
  0x0C  YM2413    OPLL
  0x0D  YMF262    OPL3; AdLib / Sound Blaster
```

Two assignment rules apply to this table:

1. **Sound-distinct revisions share an ID and use `CHIP.variant`** (below), not
   separate IDs. SID `6581`/`8580` and NES APU `NTSC`/`PAL` are revisions.
2. **A chip is one ID even when it embeds a sub-core.** FM chips with an
   AY-style SSG block (YM2203, YM2608) take one ID covering FM+SSG.
   A multi-chip *board* is different -- the Sega Genesis (YM2612 plus
   a separate SN76489) or Turbo Sound (two AYs) are genuinely two `CHIP`
   records (section 6).

The AY entry has two variants, not distinct at the register level and sharing
the ID; they differ only in the DAC amplitude curve. This is a render-time pick,
carried by `CHIP.variant`:

```text
variant = 0   AY   AY-3-8910 / AY-3-8912 DAC curve (family default)
variant = 1   YM   YM2149 DAC curve
```

A consumer with no YM curve renders variant 1 as AY. Turbo Sound is two `CHIP`
records with this same ID (section 6, example 15.2).

For `chip_type_id` == 0x01 the `CHIP.config` u32 (section 6) is laid out as:

```text
bits 0..2   stereo layout (left / right routing of tone channels A, B, C)
              0  MONO     A+B+C linearly summed to both outputs (default)
              1  ABC      left=A, center=B, right=C
              2  ACB      left=A, center=C, right=B
              3  BAC      left=B, center=A, right=C
              4  BCA      left=B, center=C, right=A
              5  CAB      left=C, center=A, right=B
              6  CBA      left=C, center=B, right=A
              7  ST_MONO  Atari ST combined-DAC mono (see below)
bits 3..31  reserved (zero)
```

A "center" channel is mixed equally to both outputs. A consumer with no stereo
output renders any layout as MONO. Layouts 1..6 describe output routing only;
they do not change which registers a timer may target.

`ST_MONO` (7) is not a pan layout but a distinct mixing model: the Atari ST
sums the three channels through a single shared, *non-linear* DAC (the combined
output is a function of all three channel volumes at once, not the sum of three
independent per-channel DAC outputs), giving a characteristically different mono
balance. A consumer that cannot reproduce the combined-DAC curve renders plain
MONO. Output is mono (both channels equal).

### A.2 Hardware targets (`target_id` 0x00..0x7F)

For `chip_type_id` == 0x01 (AY/YM): each maps directly to an AY register.
All are write targets.

```text
0x00  R0    tone A period fine       u8
0x01  R1    tone A period coarse     u8 (low 4 bits)
0x02  R2    tone B period fine       u8
0x03  R3    tone B period coarse     u8 (low 4 bits)
0x04  R4    tone C period fine       u8
0x05  R5    tone C period coarse     u8 (low 4 bits)
0x06  R6    noise period             u8 (low 5 bits)
0x07  R7    mixer / I/O enable       u8
0x08  R8    amplitude A              u8 (low 5 bits; bit4 = envelope)
0x09  R9    amplitude B              u8 (low 5 bits; bit4 = envelope)
0x0A  R10   amplitude C              u8 (low 5 bits; bit4 = envelope)
0x0B  R11   envelope period fine     u8
0x0C  R12   envelope period coarse   u8
0x0D  R13   envelope shape           u8 (low 4 bits; write retriggers)
0x0E..0x7F  unassigned -> invalid
```

The hardware range is not producer-extensible: `0x0E..0x7F` are invalid for a
standardized AY chip, not a private scratch area (section 11).

`target_id == 0x0D` (R13) is write-sensitive: every write retriggers the
envelope. This is the write-sensitive concern behind no-loop lane dormancy
(section 9.1).

R14/R15 (the AY I/O port data registers) are not sound registers and have no
target ID; they are not addressable by timers.

### A.3 Virtual targets (`target_id` 0x80..0xFF)

For `chip_type_id == 0x01`, draft 0.1 assigns only `0x80..0x82` from section
11. `0x83..0xFF` are invalid unless assigned by a later AY registry version.
Private AY-like engines use a private `chip_type_id`.

### A.4 Registry status

No other standardized chip type is defined by draft 0.1. Experiments use the
private range (`0x80..0xFF`).

## 16. Future work (not part of draft 0.1)

These reserved tags are not defined or required by draft 0.1:

```text
REG0, REG1...  chip-independent register-delta frame data candidates
DMP0, DMP1...  alternate register-delta frame data candidates
```
