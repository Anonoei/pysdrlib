import struct

from io import BytesIO
from io import BufferedReader, BufferedWriter

import numpy as np

def o2t(buff, width, otype):
    """One's compliment to two's compliment"""
    out = np.copy(buff).astype(otype)
    m1 = buff >= (2**(width-1))
    out[m1] = buff[m1] - (2**width-1)
    return out

def m2t(buff, width, otype):
    """Sign Magnitude to two's compliment"""
    out = np.copy(buff).astype(otype)
    m1 = buff >= (2**(width-1))
    out[m1] = (2**(width-1)) - buff[m1]
    return out

def b2t(buff, width, otype):
    """Offset binary to two's compliment"""
    out = np.empty_like(buff, dtype=otype)
    out = buff - (2**(width-1))
    return out

class Format:
    SIZE = 0
    @classmethod
    def read(cls, io, count):
        raise NotImplementedError()
    @classmethod
    def write(cls, io, data):
        io.write(data.tobytes())

    @classmethod
    def bits(cls):
        """Return size of format in bits"""
        return cls.SIZE

    @classmethod
    def bytes(cls):
        """Return size of format in bytes"""
        return cls.SIZE // 8

    @classmethod
    def _read(cls, io, count, dtype):
        return np.frombuffer(io.read(int(cls.bytes()*count)), dtype=dtype)

# ===============================================
#     Integers
# ===============================================
# --- Two's compliment --- #
## Signed
class i4_t(Format):
    """Two's compliment int4"""
    SIZE = 4
    @classmethod
    def read(cls, io, count): # TODO: test signed bit logic
        _bytes = cls._read(io, count//2, dtype=np.uint8)
        buff = np.empty(count, dtype=np.int8)
        buff[::2] = (_bytes & 0b10000000) | ((_bytes & 0b01110000) >> 4)
        buff[1::2] = ((_bytes & 0b00001000) << 3) (_bytes & 0b00000111)
        return buff

class i8_t(Format):
    """Two's compliment int8, Eq. to C int8_t"""
    SIZE = 8
    @classmethod
    def read(cls, io, count):
        return cls._read(io, count, dtype=np.int8)

class i16_t(Format):
    """Two's compliment int16, Eq. to C int16_t"""
    SIZE = 16
    @classmethod
    def read(cls, io, count):
        return cls._read(io, count, dtype=np.int16)

class i32_t(Format):
    """Two's compliment int32, Eq. to C int32_t"""
    SIZE = 32
    @classmethod
    def read(cls, io, count):
        return cls._read(io, count, dtype=np.int32)

## Unsigned
class ui8_t(Format):
    """Two's compliment uint8, Eq. to C uint8_t"""
    SIZE = 8
    @classmethod
    def read(cls, io, count):
        return cls._read(io, count, dtype=np.uint8)

class ui16_t(Format):
    """Two's compliment uint16, Eq. to C uint16_t"""
    SIZE = 8
    @classmethod
    def read(cls, io, count):
        return cls._read(io, count, dtype=np.uint16)

class ui32_t(Format):
    """Two's compliment uint32, Eq. to C int32_t"""
    SIZE = 8
    @classmethod
    def read(cls, io, count):
        return cls._read(io, count, dtype=np.uint32)

# --- One's compliment --- #
class i4_o(Format):
    """One's compliment int4"""
    SIZE = 4
    @classmethod
    def read(cls, io, count):
        _bytes = cls._read(io, count//2, dtype=np.uint8)
        buff = np.empty(count, dtype=np.int8)
        buff[::2] = (_bytes & 0b10000000) | ((_bytes & 0b01110000) >> 4)
        buff[1::2] = ((_bytes & 0b00001000) << 3) (_bytes & 0b00000111)

        out = (buff + (buff >> 7))
        return out.astype(np.int8)
        # return o2t(buff, cls.SIZE, np.int8)

class i8_o(Format):
    """One's compliment int8"""
    SIZE = 8
    @classmethod
    def read(cls, io, count):
        buff = cls._read(io, count, dtype=np.uint8)
        out = (buff + (buff >> 7))
        return out.astype(np.int8)
        # return o2t(buff, cls.SIZE, np.int8)

class i16_o(Format):
    """One's compliment int16"""
    SIZE = 16
    @classmethod
    def read(cls, io, count):
        buff = cls._read(io, count, dtype=np.uint16)
        out = (buff + (buff >> 15))
        return out.astype(np.int16)
        # return o2t(buff, cls.SIZE, np.int16)

# ===============================================
#     Floats
# ===============================================
class f16(Format):
    """Eq. to C float16_t"""
    SIZE = 16
    @classmethod
    def read(cls, io, count):
        return cls._read(io, count, dtype=np.float16)

class f32(Format):
    """Eq. to C float32_t"""
    SIZE = 32
    @classmethod
    def read(cls, io, count):
        return cls._read(io, count, dtype=np.float32)

class f64(Format):
    """Eq. to C float64_t"""
    SIZE = 32
    @classmethod
    def read(cls, io, count):
        return cls._read(io, count, dtype=np.float64)

# ===============================================
#     Complex
# ===============================================
class ComplexFormat(Format):
    """Format subclass for complex numbers"""
    BASE: type = None # type: ignore
    @classmethod
    def read(cls, io, count):
        raise NotImplementedError()

# --- Integer --- #
class ci8_t(ComplexFormat):
    """Complex int8 (i4, i4)"""
    SIZE = 8
    BASE = i4_t
    @classmethod
    def read(cls, io, count):
        return cls.BASE.read(io, count*2).astype(np.float32).view(dtype=np.complex64)

class ci16_t(ComplexFormat):
    """Complex int16 (i8, i8)"""
    SIZE = 16
    BASE = i8_t
    @classmethod
    def read(cls, io, count):
        return cls.BASE.read(io, count*2).astype(np.float32).view(dtype=np.complex64)
class ci32_t(ComplexFormat):
    """Complex int32 (i16, i16)"""
    SIZE = 32
    BASE = i16_t
    @classmethod
    def read(cls, io, count):
        return cls.BASE.read(io, count*2).astype(np.float32).view(dtype=np.complex64)

# --- Float --- #
class cf32(ComplexFormat):
    """Complex float32 (f16, f16)"""
    SIZE = 32
    BASE = f16
    @classmethod
    def read(cls, io, count):
        return cls.BASE.read(io, count*2).astype(np.float32).view(dtype=np.complex64)

class cf64(ComplexFormat):
    """Complex float64 (f32, f32)"""
    SIZE = 64
    BASE = f32
    @classmethod
    def read(cls, io, count):
        return cls.BASE.read(io, count*2).view(dtype=np.complex64)

class cf128(ComplexFormat):
    """Complex float128 (f64, f64)"""
    SIZE = 128
    BASE = f64
    @classmethod
    def read(cls, io, count):
        return cls.BASE.read(io, count*2).view(dtype=np.complex128)
