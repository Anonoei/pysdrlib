"""Sample file formats: what `Formats` names mean and how they are laid out in memory (and in C)

`pysdrlib.File` and `pysdrlib.FileSink` read and write these. Pick one by name:

    File("capture.cfile", "cf64")        # a GNU Radio .cfile: float32 I, float32 Q, repeating
    FileSink("out.bin", "ci16t")         # int8 I, int8 Q, repeating (what hackrf_transfer records)

## Reading the names

    <kind><bits><variant>

| Part | Meaning |
| --- | --- |
| `ui` | **u**nsigned **i**nteger |
| `i` | signed integer |
| `f` | floating point |
| `c` before `i` or `f` | **c**omplex: samples are interleaved `I, Q, I, Q, ...` |
| bits | Size of **one value** for real formats, but of **a whole complex sample** (I and Q together) for complex ones |
| `t` / `o` | Signed integers only: **t**wo's complement or **o**ne's complement |

The bit count is the gotcha. `f32` is one float32, so a complex sample of two float32 is **`cf64`**, not `cf32`:
`cf32` is two *float16*. In the same way `ci16t` is two int8 and `ci32t` is two int16.

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
| `ci8t` | 1 | I, Q: 4-bit two's complement | one byte: I in the high nibble, Q in the low | -8 to 7 |
| `ci16t` | 2 | I, Q: `int8_t` | `struct { int8_t i, q; }` | -128 to 127 |
| `ci32t` | 4 | I, Q: `int16_t` | `struct { int16_t i, q; }` | -32768 to 32767 |
| `cf32` | 4 | I, Q: `_Float16` | `struct { _Float16 i, q; }` | +-65504 |
| `cf64` | 8 | I, Q: `float` | `float _Complex` (C++ `std::complex<float>`) | unbounded |
| `cf128` | 16 | I, Q: `double` | `double _Complex` (C++ `std::complex<double>`) | unbounded |

`Formats.<name>.bytes` is the size in bytes of one element (one complex sample for the complex formats) and `.bits` the
same in bits. The 4-bit formats have no byte-sized element, so they report 1 byte: for `i4t`/`i4o` that byte holds two
values, and `ci8t` is one I/Q pair per byte (I in the high nibble). Read and write 4-bit values an even count at a time.

### Two's complement and one's complement
Two's complement is what C integers use: `-x` is `~x + 1`, there is one zero, and the range has one more negative than
positive value (`int8_t`: -128 to 127). One's complement is `-x == ~x`: the range is symmetric (-127 to 127), and
all-ones (`0xFF`) is a "negative zero" that reads as 0. It is read into the matching signed numpy type (`int8`, `int16`)
and written back the same way. You'll only need `i4o`/`i8o`/`i16o` for hardware or files that use them.

## What reading and writing do
- **Nothing is scaled.** A `ci16t` file of full-scale samples reads back as values from -128 to 127, not -1 to 1. Divide
  by the full scale yourself (e.g. `/ 128`) if you want the `[-1, 1]` that devices return.
- **Real formats** read as their natural numpy type (`i16t` -> `int16`, `f32` -> `float32`).
  **Complex formats** read as `complex64`, except `cf128`, which reads as `complex128`.
- **Writing** accepts any array-like. Floats written to an integer format are rounded to the nearest integer. A value
  the format can't hold (out of range, or NaN/infinity in an integer format) raises `ValueError`; nothing wraps.
  `FileSink(..., clip=True)` saturates instead. Complex input is split into I and Q.
- **A sample is never split across reads.** For complex formats `count` is in samples, not values.
- There is no unsigned complex format. A file of unsigned 8-bit I/Q (RTL-SDR's `.cu8`) reads as `ui8` values you
  de-interleave yourself: `x = (raw[0::2] - 127.5) + 1j * (raw[1::2] - 127.5)`.

## The same formats elsewhere

How each `Formats` member is written in C, GNU Radio and numpy. For complex integer and half-float formats numpy has
no complex type to match, so the on-disk layout is `<dtype>` pairs (`np.fromfile(path, "<int8").reshape(-1, 2)`);
`File` returns them as `complex64`. A dash means there is no standard type to name.

| pysdrlib | C | GNU Radio | numpy |
| --- | --- | --- | --- |
| `ui8` | `uint8_t` | unsigned byte (`uchar`) | `uint8` |
| `ui16` | `uint16_t` | - | `uint16` |
| `ui32` | `uint32_t` | - | `uint32` |
| `i8t` | `int8_t` | byte (`int8`) | `int8` |
| `i16t` | `int16_t` | short (`int16`) | `int16` |
| `i32t` | `int32_t` | int (`int32`) | `int32` |
| `i4t` | two values per `uint8_t` | - | unpack into `int8` |
| `i8o` | `uint8_t` holding a one's complement value | - | `int8` after conversion |
| `i16o` | `uint16_t` holding a one's complement value | - | `int16` after conversion |
| `i4o` | two values per `uint8_t` | - | unpack into `int8` |
| `f16` | `_Float16` | - | `float16` |
| `f32` | `float` | float (`float32`) | `float32` |
| `f64` | `double` | - | `float64` |
| `ci8t` | one `uint8_t`: I and Q nibbles | - | unpack into `complex64` |
| `ci16t` | `struct { int8_t i, q; }` | complex byte (`sc8`) | `int8` pairs |
| `ci32t` | `struct { int16_t i, q; }` | complex short (`sc16`) | `int16` pairs |
| `cf32` | `struct { _Float16 i, q; }` | - | `float16` pairs |
| `cf64` | `float _Complex` (C++ `std::complex<float>`) | complex float (`gr_complex`, a `.cfile`) | `complex64` |
| `cf128` | `double _Complex` (C++ `std::complex<double>`) | - | `complex128` |

## Not the same as `pysdrlib.base.convert`
`Formats` describes *files*, with no scaling. `pysdrlib.base.convert.SampleFormat` describes the *raw stream a device
delivers* (for instance a 12-bit ADC in 16-bit words) and converts it to `complex64` scaled to `[-1, 1]`. The
`pysdrlib.remote` wire formats are `SampleFormat`s.
"""
from enum import Enum

from . import io as IO

class Formats(Enum):
    """The sample formats `File` and `FileSink` understand, by name (`Formats["cf64"]`, `Formats.ls()`).

    Each member can `read` and `write` a file object directly too. See the module documentation
    (`pysdrlib.base.formats`) for what every name means and how it is laid out in memory.
    """
    # Integer
    ui8 = IO.ui8_t
    ui16 = IO.ui16_t
    ui32 = IO.ui32_t

    i4t = IO.i4_t
    i8t = IO.i8_t
    i16t = IO.i16_t
    i32t = IO.i32_t

    i4o = IO.i4_o
    i8o = IO.i8_o
    i16o = IO.i16_o

    # Float
    f16 = IO.f16
    f32 = IO.f32
    f64 = IO.f64
    # Complex
    ci8t = IO.ci8_t
    ci16t = IO.ci16_t
    ci32t = IO.ci32_t

    cf32 = IO.cf32
    cf64 = IO.cf64
    cf128 = IO.cf128

    def read(self, io, count):
        """Input as format"""
        return self.value.read(io, count)

    def write(self, io, data):
        """Output as format"""
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
    def bits(self):
        """Return size of format in bits"""
        return self.value.SIZE

    @property
    def bytes(self):
        """Return size of format in bytes (at least 1: 4-bit formats are I/Q, one pair per byte)"""
        return self.value.bytes()
    @property
    def is_complex(self):
        if issubclass(self.value, IO.ComplexFormat):
            return True
        return False

    @classmethod
    def ls(cls):
        """Names of every format"""
        return [fmt.name for fmt in cls]

    @classmethod
    def get(cls, name: str):
        """The format called `name`, e.g. `Formats.get("cf64")`; the same as `Formats[name]`"""
        return cls[name]
