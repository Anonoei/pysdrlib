import numpy as np

# Sample files are little-endian.
LE = "<"

def _native(buff, width):
    """True if `buff` is an unsigned/signed integer array whose element is exactly `width` bits"""
    return buff.dtype.kind in "ui" and buff.dtype.itemsize * 8 == width

def o2t(buff, width, otype):
    """One's compliment to two's compliment"""
    buff = np.asarray(buff)
    if _native(buff, width) and buff.dtype.kind == "u":
        # Fast path: reinterpret as signed (two's compliment), then add 1 to negatives
        # (sign bit): -x in one's compliment is one less than in two's.
        signed = buff.view(buff.dtype.str.replace("u", "i"))
        return (signed + (buff >> (width - 1)).astype(signed.dtype)).astype(otype, copy=False)
    b = buff.astype(np.int64)  # odd widths (e.g. 4-bit nibbles in uint8)
    b[b >= 2**(width-1)] -= 2**width - 1
    return b.astype(otype)

def t2o(buff, width, otype):
    """Two's compliment to one's compliment (stored as unsigned `otype`)"""
    buff = np.asarray(buff)
    if _native(buff, width) and buff.dtype.kind == "i":
        unsigned = buff.view(buff.dtype.str.replace("i", "u"))
        return (unsigned + (buff >> (width - 1)).view(unsigned.dtype)).astype(otype, copy=False)
    b = buff.astype(np.int64)
    b[b < 0] += 2**width - 1
    return b.astype(otype)

def _unpack_nibbles(io, count):
    """Read `count` 4-bit values (-1 = all). High nibble of each byte comes first.

    4-bit samples are I/Q components: each byte is one (I, Q) pair, so `count` must be even.
    """
    if count % 2 and count > 0:
        raise ValueError("4-bit formats store I/Q pairs; count must be even")
    data = np.frombuffer(io.read(-1 if count < 0 else count // 2), dtype=np.uint8)
    out = np.empty(data.size * 2, dtype=np.uint8)
    out[0::2] = data >> 4
    out[1::2] = data & 0x0F
    return out

def _pack_nibbles(nibbles):
    """Pack 4-bit values (0-15) two per byte, high nibble first (one I/Q pair per byte)"""
    n = np.asarray(nibbles, dtype=np.uint8)
    if n.size % 2:
        raise ValueError("4-bit formats store I/Q pairs; need an even number of values")
    return ((n[0::2] << 4) | n[1::2]).astype(np.uint8).tobytes()

class RangeError(ValueError):
    """A value cannot be represented in the format"""

def _as_int(data, lo, hi, name):
    arr = np.asarray(data)
    if arr.dtype.kind == "f":
        if not np.isfinite(arr).all():
            raise RangeError(f"{name} cannot store NaN or infinity")
        arr = np.rint(arr)
    arr = arr.astype(np.int64)
    if arr.size and (arr.min() < lo or arr.max() > hi):
        raise RangeError(f"{name} values must be within [{lo}, {hi}]")
    return arr

class Format:
    SIZE = 0
    DTYPE = None  # numpy dtype of one element on disk (None for packed/complex formats)

    @classmethod
    def read(cls, io, count):
        """Read `count` samples (-1 = until the end)"""
        if cls.DTYPE is None:
            raise NotImplementedError()
        return cls._read(io, count, dtype=cls.DTYPE)

    @classmethod
    def write(cls, io, data):
        """Write samples (any array-like) in this format"""
        if cls.DTYPE is None:
            raise NotImplementedError()
        arr = np.asarray(data)
        if np.dtype(cls.DTYPE).kind in "iu" and arr.dtype.kind == "f":
            arr = np.rint(arr)
        cls._check_range(arr)
        io.write(arr.astype(cls.DTYPE).tobytes())

    @classmethod
    def limits(cls):
        """(lo, hi) of the values `write` accepts per component, or None if unbounded (float32/64)"""
        if cls.DTYPE is None:
            return None
        dt = np.dtype(cls.DTYPE)
        if dt.kind in "iu":
            info = np.iinfo(dt)
        elif dt.kind == "f" and dt.itemsize == 2:
            info = np.finfo(dt)
        else:
            return None
        return float(info.min), float(info.max)

    @classmethod
    def integer(cls):
        """True if components are stored as integers (so NaN/inf have no representation)"""
        return cls.DTYPE is None or np.dtype(cls.DTYPE).kind in "iu"

    @classmethod
    def _check_range(cls, arr):
        """Raise ValueError rather than let numpy wrap (integers) or overflow to inf (float16).
        NaN and infinity are only valid in float formats."""
        limits = cls.limits()
        if limits is None or arr.size == 0:
            return
        lo, hi = limits
        mn, mx = arr.min(), arr.max()
        if lo <= mn and mx <= hi:
            return
        if not cls.integer() and np.isnan(arr).any() | np.isinf(arr).any():
            finite = arr[np.isfinite(arr)]
            if finite.size == 0 or (lo <= finite.min() and finite.max() <= hi):
                return  # NaN/inf are representable in a float format
        raise RangeError(f"values must be within [{lo:g}, {hi:g}]")

    @classmethod
    def full_scale(cls):
        """The magnitude of a component that maps to 1.0 when scaled to +-1: 2^(bits-1) for signed integers, half the
        range for unsigned ones (mid-scale is zero, see `offset`), 1.0 for floats"""
        if not cls.integer():
            return 1.0
        return float(2 ** (cls.SIZE - 1)) if cls.limits()[0] < 0 else (2 ** cls.SIZE - 1) / 2

    @classmethod
    def offset(cls):
        """The stored value that is zero when scaling: mid-scale for unsigned integers, else 0"""
        return (2 ** cls.SIZE - 1) / 2 if cls.integer() and cls.limits()[0] == 0 else 0.0

    @classmethod
    def bits(cls):
        """Return size of format in bits"""
        return cls.SIZE

    @classmethod
    def bytes(cls):
        """Return size of format in bytes. Sub-byte formats (4-bit) are I/Q components, so the
        smallest unit is 1 byte: one I/Q pair."""
        return max(1, cls.SIZE // 8)

    @classmethod
    def _read(cls, io, count, dtype):
        return np.frombuffer(io.read(int(cls.bytes()*count)), dtype=dtype)

# ===============================================
#     Integers
# ===============================================
# --- Two's compliment --- #
## Signed
class i4_t(Format):
    """Two's complement int4. Meant for I/Q: one byte is one (I, Q) pair, I in the high nibble.
    `count` and written data must therefore be even (see ci8_t)."""
    SIZE = 4
    @classmethod
    def read(cls, io, count):
        nib = _unpack_nibbles(io, count)
        return ((nib ^ 0x08).astype(np.int8) - 8).astype(np.int8)
    @classmethod
    def write(cls, io, data):
        io.write(_pack_nibbles(_as_int(data, -8, 7, "int4") & 0x0F))
    @classmethod
    def limits(cls):
        return -8.0, 7.0

class i8_t(Format):
    """Two's complement int8: C `int8_t`"""
    SIZE = 8
    DTYPE = np.dtype(LE + "i1")

class i16_t(Format):
    """Two's complement int16: C `int16_t`"""
    SIZE = 16
    DTYPE = np.dtype(LE + "i2")

class i32_t(Format):
    """Two's complement int32: C `int32_t`"""
    SIZE = 32
    DTYPE = np.dtype(LE + "i4")

## Unsigned
class ui8_t(Format):
    """Unsigned int8: C `uint8_t`"""
    SIZE = 8
    DTYPE = np.dtype(LE + "u1")

class ui16_t(Format):
    """Unsigned int16: C `uint16_t`"""
    SIZE = 16
    DTYPE = np.dtype(LE + "u2")

class ui32_t(Format):
    """Unsigned int32: C `uint32_t`"""
    SIZE = 32
    DTYPE = np.dtype(LE + "u4")

# --- One's compliment --- #
class i4_o(Format):
    """One's complement int4. Meant for I/Q: one byte is one (I, Q) pair, I in the high nibble.
    `count` and written data must therefore be even."""
    SIZE = 4
    @classmethod
    def read(cls, io, count):
        return o2t(_unpack_nibbles(io, count), 4, np.int8)
    @classmethod
    def write(cls, io, data):
        io.write(_pack_nibbles(t2o(_as_int(data, -7, 7, "one's compliment int4"), 4, np.uint8)))
    @classmethod
    def limits(cls):
        return -7.0, 7.0

class _OnesComplement(Format):
    """A negative number is the bitwise NOT of its magnitude: symmetric range, and all-ones is a -0 that reads as 0"""
    STORED: np.dtype = None # type: ignore   # unsigned, as stored on disk
    SIGNED: type = None # type: ignore       # what it reads as
    @classmethod
    def read(cls, io, count):
        return o2t(cls._read(io, count, dtype=cls.STORED), cls.SIZE, cls.SIGNED)
    @classmethod
    def write(cls, io, data):
        top = 2 ** (cls.SIZE - 1) - 1
        io.write(t2o(_as_int(data, -top, top, f"one's compliment int{cls.SIZE}"), cls.SIZE, cls.STORED).tobytes())
    @classmethod
    def limits(cls):
        top = float(2 ** (cls.SIZE - 1) - 1)
        return -top, top

class i8_o(_OnesComplement):
    """One's complement int8 (range -127..127; 0xFF is -0, read as 0)"""
    SIZE = 8
    STORED, SIGNED = np.dtype("u1"), np.int8

class i16_o(_OnesComplement):
    """One's complement int16 (range -32767..32767)"""
    SIZE = 16
    STORED, SIGNED = np.dtype(LE + "u2"), np.int16

# ===============================================
#     Floats
# ===============================================
class f16(Format):
    """IEEE half precision: C `_Float16` (C23, GCC/Clang), numpy `float16`"""
    SIZE = 16
    DTYPE = np.dtype(LE + "f2")

class f32(Format):
    """IEEE single precision: C `float`"""
    SIZE = 32
    DTYPE = np.dtype(LE + "f4")

class f64(Format):
    """IEEE double precision: C `double`"""
    SIZE = 64
    DTYPE = np.dtype(LE + "f8")

# ===============================================
#     Complex
# ===============================================
class ComplexFormat(Format):
    """Format subclass for complex numbers, stored as interleaved (I, Q) of BASE"""
    BASE: type = None # type: ignore
    OUT = np.complex64
    @classmethod
    def limits(cls):
        return cls.BASE.limits()
    @classmethod
    def integer(cls):
        return cls.BASE.integer()
    @classmethod
    def full_scale(cls):
        return cls.BASE.full_scale()
    @classmethod
    def offset(cls):
        return cls.BASE.offset()
    @classmethod
    def read(cls, io, count):
        parts = cls.BASE.read(io, count * 2 if count >= 0 else -1)
        return parts.astype(np.float32 if cls.OUT == np.complex64 else np.float64).view(cls.OUT)
    @classmethod
    def write(cls, io, data):
        # I and Q are the interleaved floats of the complex array itself (complex64 unless the format is cf128)
        x = np.ascontiguousarray(np.asarray(data, dtype=cls.OUT))
        cls.BASE.write(io, x.view(x.real.dtype).ravel())

# --- Integer --- #
class ci8_t(ComplexFormat):
    """Complex int8: I and Q are each a 4-bit two's complement value, one byte per sample (I in the high nibble)"""
    SIZE = 8
    BASE = i4_t

class ci16_t(ComplexFormat):
    """Complex int16: I and Q are each an `int8_t`, two bytes per sample (`struct { int8_t i, q; }`)"""
    SIZE = 16
    BASE = i8_t

class ci32_t(ComplexFormat):
    """Complex int32: I and Q are each an `int16_t`, four bytes per sample (`struct { int16_t i, q; }`)"""
    SIZE = 32
    BASE = i16_t

# --- Float --- #
class cf32(ComplexFormat):
    """Complex float32: I and Q are each a half-precision `_Float16`, four bytes per sample.
    Not a GNU Radio `.cfile`: that is `cf64`"""
    SIZE = 32
    BASE = f16

class cf64(ComplexFormat):
    """Complex float64: I and Q are each a `float`, eight bytes per sample (C `float _Complex`, C++ `std::complex<float>`,
    numpy `complex64`). The GNU Radio `.cfile` layout"""
    SIZE = 64
    BASE = f32

class cf128(ComplexFormat):
    """Complex float128: I and Q are each a `double`, sixteen bytes per sample (C `double _Complex`, numpy `complex128`).
    Read as `complex128`, the only format that is"""
    SIZE = 128
    BASE = f64
    OUT = np.complex128
