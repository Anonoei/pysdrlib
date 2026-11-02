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

def m2t(buff, width, otype):
    """Sign Magnitude to two's compliment"""
    b = np.asarray(buff).astype(np.int64)
    neg = b >= 2**(width-1)
    b[neg] = -(b[neg] - 2**(width-1))
    return b.astype(otype)

def b2t(buff, width, otype):
    """Offset binary to two's compliment"""
    return (np.asarray(buff).astype(np.int64) - 2**(width-1)).astype(otype)

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

def _as_int(data, lo, hi, name):
    arr = np.rint(np.asarray(data)) if np.asarray(data).dtype.kind == "f" else np.asarray(data)
    arr = arr.astype(np.int64)
    if arr.size and (arr.min() < lo or arr.max() > hi):
        raise ValueError(f"{name} values must be within [{lo}, {hi}]")
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
        io.write(arr.astype(cls.DTYPE).tobytes())

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
    """Two's compliment int4. Meant for I/Q: one byte is one (I, Q) pair, I in the high nibble.
    `count` and written data must therefore be even (see ci8_t)."""
    SIZE = 4
    @classmethod
    def read(cls, io, count):
        nib = _unpack_nibbles(io, count)
        return ((nib ^ 0x08).astype(np.int8) - 8).astype(np.int8)
    @classmethod
    def write(cls, io, data):
        io.write(_pack_nibbles(_as_int(data, -8, 7, "int4") & 0x0F))

class i8_t(Format):
    """Two's compliment int8, Eq. to C int8_t"""
    SIZE = 8
    DTYPE = np.dtype(LE + "i1")

class i16_t(Format):
    """Two's compliment int16, Eq. to C int16_t"""
    SIZE = 16
    DTYPE = np.dtype(LE + "i2")

class i32_t(Format):
    """Two's compliment int32, Eq. to C int32_t"""
    SIZE = 32
    DTYPE = np.dtype(LE + "i4")

## Unsigned
class ui8_t(Format):
    """Unsigned int8, Eq. to C uint8_t"""
    SIZE = 8
    DTYPE = np.dtype(LE + "u1")

class ui16_t(Format):
    """Unsigned int16, Eq. to C uint16_t"""
    SIZE = 16
    DTYPE = np.dtype(LE + "u2")

class ui32_t(Format):
    """Unsigned int32, Eq. to C uint32_t"""
    SIZE = 32
    DTYPE = np.dtype(LE + "u4")

# --- One's compliment --- #
class i4_o(Format):
    """One's compliment int4. Meant for I/Q: one byte is one (I, Q) pair, I in the high nibble.
    `count` and written data must therefore be even."""
    SIZE = 4
    @classmethod
    def read(cls, io, count):
        return o2t(_unpack_nibbles(io, count), 4, np.int8)
    @classmethod
    def write(cls, io, data):
        io.write(_pack_nibbles(t2o(_as_int(data, -7, 7, "one's compliment int4"), 4, np.uint8)))

class i8_o(Format):
    """One's compliment int8"""
    SIZE = 8
    @classmethod
    def read(cls, io, count):
        return o2t(cls._read(io, count, dtype=np.uint8), cls.SIZE, np.int8)
    @classmethod
    def write(cls, io, data):
        io.write(t2o(_as_int(data, -127, 127, "one's compliment int8"), cls.SIZE, np.uint8).tobytes())

class i16_o(Format):
    """One's compliment int16"""
    SIZE = 16
    @classmethod
    def read(cls, io, count):
        return o2t(cls._read(io, count, dtype=np.dtype(LE + "u2")), cls.SIZE, np.int16)
    @classmethod
    def write(cls, io, data):
        out = t2o(_as_int(data, -32767, 32767, "one's compliment int16"), cls.SIZE, np.dtype(LE + "u2"))
        io.write(out.tobytes())

# ===============================================
#     Floats
# ===============================================
class f16(Format):
    """Eq. to C float16_t"""
    SIZE = 16
    DTYPE = np.dtype(LE + "f2")

class f32(Format):
    """Eq. to C float32_t"""
    SIZE = 32
    DTYPE = np.dtype(LE + "f4")

class f64(Format):
    """Eq. to C float64_t"""
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
    def read(cls, io, count):
        parts = cls.BASE.read(io, count * 2 if count >= 0 else -1)
        return parts.astype(np.float32 if cls.OUT == np.complex64 else np.float64).view(cls.OUT)
    @classmethod
    def write(cls, io, data):
        x = np.asarray(data, dtype=np.complex128)
        parts = np.empty(x.size * 2, dtype=np.float64)
        parts[0::2] = x.real.ravel()
        parts[1::2] = x.imag.ravel()
        cls.BASE.write(io, parts)

# --- Integer --- #
class ci8_t(ComplexFormat):
    """Complex int8 (i4, i4)"""
    SIZE = 8
    BASE = i4_t

class ci16_t(ComplexFormat):
    """Complex int16 (i8, i8)"""
    SIZE = 16
    BASE = i8_t

class ci32_t(ComplexFormat):
    """Complex int32 (i16, i16)"""
    SIZE = 32
    BASE = i16_t

# --- Float --- #
class cf32(ComplexFormat):
    """Complex float32 (f16, f16)"""
    SIZE = 32
    BASE = f16

class cf64(ComplexFormat):
    """Complex float64 (f32, f32)"""
    SIZE = 64
    BASE = f32

class cf128(ComplexFormat):
    """Complex float128 (f64, f64)"""
    SIZE = 128
    BASE = f64
    OUT = np.complex128
