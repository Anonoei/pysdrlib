"""How the sample file formats (`pysdrlib.base.formats.Formats`) are laid out, read and written. Little-endian."""
import numpy as np

class RangeError(ValueError):
    """A value cannot be represented in the format"""

def o2t(buff, width, otype):
    """One's complement (stored unsigned) to two's complement"""
    buff = np.asarray(buff)
    if buff.dtype.kind == "u" and buff.dtype.itemsize * 8 == width:
        # reinterpret as signed, then add 1 to negatives (sign bit): -x in one's complement is one less than in two's
        signed = buff.view(buff.dtype.str.replace("u", "i"))
        return (signed + (buff >> (width - 1)).astype(signed.dtype)).astype(otype, copy=False)
    b = buff.astype(np.int64)  # odd widths (4-bit nibbles in uint8)
    b[b >= 2**(width-1)] -= 2**width - 1
    return b.astype(otype)

def t2o(buff, width, otype):
    """Two's complement to one's complement (stored as unsigned `otype`)"""
    buff = np.asarray(buff)
    if buff.dtype.kind == "i" and buff.dtype.itemsize * 8 == width:
        unsigned = buff.view(buff.dtype.str.replace("i", "u"))
        return (unsigned + (buff >> (width - 1)).view(unsigned.dtype)).astype(otype, copy=False)
    b = buff.astype(np.int64)
    b[b < 0] += 2**width - 1
    return b.astype(otype)

class Layout:
    """One format: `kind` "i" (signed), "u" (unsigned) or "f" (float), `bits` per value (I or Q of a complex format),
    one's complement (`ones`, signed only), and `cplx`: interleaved I, Q. 4-bit values are packed two to a byte, the
    first in the high nibble, so they are read and written an even count at a time"""
    def __init__(self, kind, bits, ones=False, cplx=False):
        self.kind, self.bits, self.ones, self.cplx = kind, bits, ones, cplx
        self.dtype = None if bits == 4 else np.dtype(f"<{'u' if ones else kind}{bits // 8}")   # one value, on disk
        self.out = np.complex128 if cplx and kind == "f" and bits == 64 else np.complex64      # what complex reads give

    def __repr__(self):
        return f"Layout({self.kind!r}, {self.bits}, ones={self.ones}, cplx={self.cplx})"

    @property
    def bytes(self):
        """Bytes of one element (a complex sample: I and Q); at least 1, since a 4-bit byte holds two values"""
        return max(1, self.bits * (2 if self.cplx else 1) // 8)

    def integer(self):
        return self.kind != "f"

    def limits(self):
        """(lo, hi) a value (I or Q) can hold, or None if unbounded (float32/64)"""
        if self.kind == "f":
            return (-65504.0, 65504.0) if self.bits == 16 else None
        if self.kind == "u":
            return 0.0, 2.0 ** self.bits - 1
        top = 2.0 ** (self.bits - 1)
        return (1 - top, top - 1) if self.ones else (-top, top - 1)

    def full_scale(self):
        """The magnitude of a value that maps to 1.0: 2^(bits-1) signed, half the range unsigned, 1.0 float"""
        if self.kind == "f":
            return 1.0
        return 2.0 ** (self.bits - 1) if self.kind == "i" else (2 ** self.bits - 1) / 2

    def offset(self):
        """The stored value that is zero when scaling: mid-scale for unsigned integers, else 0"""
        return (2 ** self.bits - 1) / 2 if self.kind == "u" else 0.0

    def read(self, f, count):
        """`count` elements (-1: to the end): values of their numpy type, or complex samples"""
        n = count * 2 if self.cplx and count >= 0 else count   # values
        if self.bits == 4:
            if n % 2 and n > 0:
                raise ValueError("4-bit formats are read an even number of values at a time")
            packed = np.frombuffer(f.read(-1 if n < 0 else n // 2), dtype=np.uint8)
            nib = np.empty(packed.size * 2, dtype=np.uint8)
            nib[0::2], nib[1::2] = packed >> 4, packed & 0x0F
            vals = o2t(nib, 4, np.int8) if self.ones else ((nib ^ 0x08).astype(np.int8) - 8).astype(np.int8)
        else:
            vals = np.frombuffer(f.read(-1 if n < 0 else n * self.dtype.itemsize), dtype=self.dtype)
            if self.ones:
                vals = o2t(vals, self.bits, np.dtype(f"i{self.bits // 8}"))
        if not self.cplx:
            return vals
        return vals.astype(np.float64 if self.out == np.complex128 else np.float32).view(self.out)

    def write(self, f, data):
        """Any array-like (complex for a complex format). Raises RangeError rather than wrap or overflow"""
        if self.cplx:
            x = np.ascontiguousarray(np.asarray(data, dtype=self.out))
            data = x.view(x.real.dtype).ravel()
        arr = np.asarray(data)
        if self.integer() and arr.dtype.kind == "f":
            arr = np.rint(arr)
        self._check(arr)
        if self.bits == 4:
            if arr.size % 2:
                raise ValueError("4-bit formats are written an even number of values at a time")
            vals = arr.astype(np.int8)
            nib = t2o(vals, 4, np.uint8) if self.ones else vals.view(np.uint8) & 0x0F
            f.write(((nib[0::2] << 4) | nib[1::2]).tobytes())
        elif self.ones:   # in range, so the signed type of the width holds it: t2o's fast path
            f.write(t2o(arr.astype(f"<i{self.bits // 8}"), self.bits, self.dtype).tobytes())
        else:
            f.write(arr.astype(self.dtype, copy=False).tobytes())

    def _check(self, arr):
        """RangeError for a value the format can't hold (integers: NaN/infinity too), rather than let numpy wrap it"""
        limits = self.limits()
        if limits is None or not arr.size:
            return
        lo, hi = arr.min(), arr.max()
        if lo >= limits[0] and hi <= limits[1]:
            return
        if arr.dtype.kind == "f":
            if self.integer() and (np.isnan(lo) or np.isnan(hi)):
                raise RangeError("an integer format cannot store NaN")
            finite = arr[np.isfinite(arr)]   # NaN and infinity are fine in a float format
            if not self.integer() and (not finite.size or (finite.min() >= limits[0] and finite.max() <= limits[1])):
                return
        raise RangeError(f"values must be within [{limits[0]:g}, {limits[1]:g}]")
