from enum import Enum

from . import io as IO

class Formats(Enum):
    """IO Format wrapper"""
    # Integer
    ui8 = IO.ui8_t
    ui16 = IO.ui16_t
    ui32 = IO.ui32_t

    i4t = IO.i4_t
    i8t = IO.i8_t
    i16t = IO.i16_t
    i32t = IO.i32_t

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
        self.value.write(io, data)

    @property
    def bits(self):
        """Return size of format in bits"""
        return self.value.SIZE

    @property
    def bytes(self):
        """Return size of format in bytes"""
        return self.value.SIZE // 8
    @property
    def is_complex(self):
        if issubclass(self.value, IO.ComplexFormat):
            return True
        return False

    @classmethod
    def ls(cls):
        return [fmt.name for fmt in cls]

    @classmethod
    def get(cls, name: str):
        return cls[name]
