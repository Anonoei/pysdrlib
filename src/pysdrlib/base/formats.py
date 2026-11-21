"""Sample file formats: what `Formats` names mean and how they are laid out in memory (and in C)

`pysdrlib.File` and `pysdrlib.FileSink` read and write these. Pick one by name:

    File("capture.cfile", "cf32")        # a GNU Radio .cfile: float32 I, float32 Q, repeating
    FileSink("out.bin", "ci8")           # int8 I, int8 Q, repeating

## Reading the names

    <c><kind><bits><variant>

| Part | Meaning |
| --- | --- |
| `c` | **c**omplex: samples are interleaved `I, Q, I, Q, ...` |
| `ui` | **u**nsigned **i**nteger |
| `i` | signed integer |
| `f` | floating point |
| bits | Size of **one value**: for a complex format, of I (or Q) alone, as in SigMF |
| `t` / `o` | Signed integers: **t**wo's complement (the default: `ci8` is `ci8t`) or **o**ne's complement |

So `cf32` is two float32 (numpy `complex64`, a GNU Radio `.cfile`), `ci8` two int8 and `cui8` two uint8 (an RTL-SDR
`.cu8`). Before pysdrlib 0.5 the bits counted the whole complex sample: what was `cf64` is now `cf32`, `ci16t` is `ci8t`.

## The formats

"C type" is what one value (real formats) or one I/Q pair (complex) looks like in memory. Everything is
little-endian.

| Name | Bytes | Per value | C type | Range (per value) |
| --- | --- | --- | --- | --- |
| `ui8` `ui16` `ui32` | 1, 2, 4 | unsigned int | `uint8_t` `uint16_t` `uint32_t` | 0 to 2^bits - 1 |
| `i8t` `i16t` `i32t` | 1, 2, 4 | two's complement | `int8_t` `int16_t` `int32_t` | -2^(bits-1) to 2^(bits-1) - 1 |
| `i4t` | 1 per 2 values | 4-bit two's complement | none: two values packed in a byte | -8 to 7 |
| `i8o` `i16o` | 1, 2 | one's complement | none: stored as `uint8_t` / `uint16_t` | -127 to 127, -32767 to 32767 |
| `i4o` | 1 per 2 values | 4-bit one's complement | none: two values packed in a byte | -7 to 7 |
| `f16` | 2 | IEEE half | `_Float16` (C23; GCC/Clang) | +-65504 |
| `f32` | 4 | IEEE single | `float` | unbounded |
| `f64` | 8 | IEEE double | `double` | unbounded |
| `ci4t` | 1 | I, Q: 4-bit two's complement | one byte: I in the high nibble, Q in the low | -8 to 7 |
| `ci8t` | 2 | I, Q: `int8_t` | `struct { int8_t i, q; }` | -128 to 127 |
| `ci16t` | 4 | I, Q: `int16_t` | `struct { int16_t i, q; }` | -32768 to 32767 |
| `ci32t` | 8 | I, Q: `int32_t` | `struct { int32_t i, q; }` | -2^31 to 2^31 - 1 (read as float32) |
| `cui8` | 2 | I, Q: `uint8_t` | `struct { uint8_t i, q; }` | 0 to 255 |
| `cui16` | 4 | I, Q: `uint16_t` | `struct { uint16_t i, q; }` | 0 to 65535 |
| `cf16` | 4 | I, Q: `_Float16` | `struct { _Float16 i, q; }` | +-65504 |
| `cf32` | 8 | I, Q: `float` | `float _Complex` (C++ `std::complex<float>`) | unbounded |
| `cf64` | 16 | I, Q: `double` | `double _Complex` (C++ `std::complex<double>`) | unbounded |

Each two's complement name also works without its `t` (`i8`, `ci8`, ...): `Formats["ci8"] is Formats.ci8t`.

`Formats.<name>.bytes` is the size in bytes of one element (one complex sample for the complex formats) and `.bits` the
size of one value in bits. The 4-bit formats have no byte-sized value, so they report 1 byte: for `i4t`/`i4o` that byte
holds two values, and `ci4t` is one I/Q pair per byte (I in the high nibble). Read and write 4-bit values an even count
at a time.

### Two's complement and one's complement
Two's complement is what C integers use: `-x` is `~x + 1`, there is one zero, and the range has one more negative than
positive value (`int8_t`: -128 to 127). One's complement is `-x == ~x`: the range is symmetric (-127 to 127), and
all-ones (`0xFF`) is a "negative zero" that reads as 0. It is read into the matching signed numpy type (`int8`, `int16`)
and written back the same way. You'll only need `i4o`/`i8o`/`i16o` for hardware or files that use them.

## What reading and writing do
- **Nothing is scaled** unless you ask. A `ci8t` file of full-scale samples reads back as values from -128 to 127, not
  -1 to 1. `File(path, fmt, scale=True)` (or `Formats.<name>.scale(samples)`) gives the `[-1, 1]` that devices return
  (an unsigned format is centred first: `cui8` mid-scale 127.5 is 0); `Formats.<name>.full_scale` is the value that
  maps to 1.0.
- **Real formats** read as their natural numpy type (`i16t` -> `int16`, `f32` -> `float32`).
  **Complex formats** read as `complex64`, except `cf64`, which reads as `complex128`.
- **Writing** accepts any array-like. Floats written to an integer format are rounded to the nearest integer. A value
  the format can't hold (out of range, or NaN/infinity in an integer format) raises `ValueError`; nothing wraps.
  `FileSink(..., clip=True)` saturates instead. Complex input is split into I and Q.
- **A sample is never split across reads.** For complex formats `count` is in samples, not values.

## The same formats elsewhere

How each `Formats` member is written in SigMF, C, GNU Radio and numpy. For complex integer and half-float formats numpy
has no complex type to match, so the on-disk layout is `<dtype>` pairs (`np.fromfile(path, "<int8").reshape(-1, 2)`);
`File` returns them as `complex64`. A dash means there is no standard type to name.

| pysdrlib | SigMF | C | GNU Radio | numpy |
| --- | --- | --- | --- | --- |
| `ui8` | `ru8` | `uint8_t` | unsigned byte (`uchar`) | `uint8` |
| `ui16` | `ru16_le` | `uint16_t` | - | `uint16` |
| `ui32` | `ru32_le` | `uint32_t` | - | `uint32` |
| `i8t` | `ri8` | `int8_t` | byte (`int8`) | `int8` |
| `i16t` | `ri16_le` | `int16_t` | short (`int16`) | `int16` |
| `i32t` | `ri32_le` | `int32_t` | int (`int32`) | `int32` |
| `i4t` `i4o` `i8o` `i16o` | - | packed or one's complement in `uint8_t` / `uint16_t` | - | `int8` / `int16` after conversion |
| `f16` | - | `_Float16` | - | `float16` |
| `f32` | `rf32_le` | `float` | float (`float32`) | `float32` |
| `f64` | `rf64_le` | `double` | - | `float64` |
| `ci4t` | - | one `uint8_t`: I and Q nibbles | - | unpack into `complex64` |
| `ci8t` | `ci8` | `struct { int8_t i, q; }` | complex byte (`sc8`) | `int8` pairs |
| `ci16t` | `ci16_le` | `struct { int16_t i, q; }` | complex short (`sc16`) | `int16` pairs |
| `ci32t` | `ci32_le` | `struct { int32_t i, q; }` | - | `int32` pairs |
| `cui8` | `cu8` | `struct { uint8_t i, q; }` | - | `uint8` pairs |
| `cui16` | `cu16_le` | `struct { uint16_t i, q; }` | - | `uint16` pairs |
| `cf16` | - | `struct { _Float16 i, q; }` | - | `float16` pairs |
| `cf32` | `cf32_le` | `float _Complex` (C++ `std::complex<float>`) | complex float (`gr_complex`, a `.cfile`) | `complex64` |
| `cf64` | `cf64_le` | `double _Complex` (C++ `std::complex<double>`) | - | `complex128` |

## Not the same as `pysdrlib.base.convert`
`Formats` describes *files*, with no scaling. `pysdrlib.base.convert.SampleFormat` describes the *raw stream a device
delivers* (for instance a 12-bit ADC in 16-bit words) and converts it to `complex64` scaled to `[-1, 1]`. The
`pysdrlib.remote` wire formats are `SampleFormat`s.
"""
from enum import Enum

import numpy as np

from . import io as IO

class Formats(Enum):
    """The sample formats `File` and `FileSink` understand, by name (`Formats["cf32"]`, `Formats.ls()`).

    Each member can `read` and `write` a file object directly too. See the module documentation
    (`pysdrlib.base.formats`) for what every name means and how it is laid out in memory.
    """
    # Integer
    ui8 = IO.Layout("u", 8)
    ui16 = IO.Layout("u", 16)
    ui32 = IO.Layout("u", 32)

    i4t = IO.Layout("i", 4)
    i8t = IO.Layout("i", 8)
    i16t = IO.Layout("i", 16)
    i32t = IO.Layout("i", 32)

    i4o = IO.Layout("i", 4, ones=True)
    i8o = IO.Layout("i", 8, ones=True)
    i16o = IO.Layout("i", 16, ones=True)

    # Float
    f16 = IO.Layout("f", 16)
    f32 = IO.Layout("f", 32)
    f64 = IO.Layout("f", 64)

    # Complex
    ci4t = IO.Layout("i", 4, cplx=True)
    ci8t = IO.Layout("i", 8, cplx=True)
    ci16t = IO.Layout("i", 16, cplx=True)
    ci32t = IO.Layout("i", 32, cplx=True)
    cui8 = IO.Layout("u", 8, cplx=True)
    cui16 = IO.Layout("u", 16, cplx=True)

    cf16 = IO.Layout("f", 16, cplx=True)
    cf32 = IO.Layout("f", 32, cplx=True)
    cf64 = IO.Layout("f", 64, cplx=True)

    # Two's complement is the default: the same members without the `t` (aliases, not listed by `ls`)
    i4, i8, i16, i32 = i4t, i8t, i16t, i32t
    ci4, ci8, ci16, ci32 = ci4t, ci8t, ci16t, ci32t

    def read(self, io, count):
        """`count` elements (-1: to the end) from a file object"""
        return self.value.read(io, count)

    def write(self, io, data):
        """Write samples (any array-like) in this format"""
        try:
            self.value.write(io, data)
        except IO.RangeError as e:
            raise IO.RangeError(f"{self.name}: {e}") from e

    def limits(self):
        """(lo, hi) of the values `write` accepts per component (I or Q), or None if unbounded"""
        return self.value.limits()

    def integer(self):
        """True if components are stored as integers"""
        return self.value.integer()

    @property
    def full_scale(self):
        """The magnitude of a component (I or Q) that `scale` maps to 1.0: 2^(bits-1) for signed integers, half the range
        for unsigned ones, 1.0 for floats"""
        return self.value.full_scale()

    def scale(self, samples):
        """Samples as `read` gives them, scaled the way a device's are: float32 (complex64 for complex formats) with
        full scale at +-1. An unsigned integer format is centred first (mid-scale is zero). A float format keeps its
        values; `cf64` and `f64` stay double precision"""
        x = np.asarray(samples)
        if x.dtype.kind != "c" and x.dtype != np.float64:
            x = x.astype(np.float32)
        offset = self.value.offset()
        if offset:
            x = x - (x.dtype.type(offset * (1 + 1j)) if x.dtype.kind == "c" else x.dtype.type(offset))
        return x / x.real.dtype.type(self.full_scale)

    @property
    def bits(self):
        """Size of one value (I or Q of a complex format) in bits"""
        return self.value.bits

    @property
    def bytes(self):
        """Size of one element in bytes (a complex sample: I and Q). At least 1: 4-bit values come two to a byte"""
        return self.value.bytes

    @property
    def is_complex(self):
        return self.value.cplx

    @classmethod
    def ls(cls):
        """Names of every format (without the aliases)"""
        return [fmt.name for fmt in cls]

    @classmethod
    def get(cls, name: str):
        """The format called `name`, e.g. `Formats.get("cf32")`; the same as `Formats[name]`"""
        return cls[name]
