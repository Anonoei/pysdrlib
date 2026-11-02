"""Raw device data <-> pysdrlib samples (complex64), for any ADC layout.

Devices deliver interleaved I/Q in whatever layout their hardware uses. A `SampleFormat`
describes that layout and converts it to complex64 scaled to [-1, 1]; `from_complex64` goes back.

    fmt = convert.INT12                  # 12-bit ADC values in 16-bit words
    samples = fmt.to_complex64(raw, valid_bytes)

Nothing in here is specific to a device. `valid` lengths are in bytes, as drivers report them.
"""
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

@dataclass(frozen=True)
class SampleFormat:
    """Layout of one component (I or Q) of the raw stream

    kind:      "int"   signed two's compliment
               "uint"  unsigned, offset binary (mid-scale is zero, e.g. RTL-SDR 8-bit)
               "float" IEEE float
    bits:      significant bits per component (12 for a 12-bit ADC)
    storage:   bits a component occupies in memory: 8, 16, 32 or 64. For `packed` formats, equals `bits`.
    packed:    components follow each other with no padding (only 12-bit signed is supported)
    byteorder: "<" little endian (default) or ">"
    """
    kind: str
    bits: int
    storage: int
    packed: bool = False
    byteorder: str = "<"

    def __post_init__(self):
        if self.kind not in ("int", "uint", "float"):
            raise ValueError(f"kind must be int, uint or float, not {self.kind!r}")
        if self.byteorder not in ("<", ">"):
            raise ValueError("byteorder must be '<' or '>'")
        if self.packed:
            if (self.kind, self.bits, self.storage) != ("int", 12, 12):
                raise ValueError("only signed 12-bit samples can be packed")
            return
        if self.storage not in (8, 16, 32, 64) or not 1 <= self.bits <= self.storage:
            raise ValueError(f"invalid bits/storage: {self.bits}/{self.storage}")
        if self.kind == "float" and (self.bits != self.storage or self.storage not in (32, 64)):
            raise ValueError("float samples are 32 or 64 bits")

    @property
    def dtype(self):
        """numpy dtype of one component, or None for packed formats (raw bytes)"""
        if self.packed:
            return None
        code = {"int": "i", "uint": "u", "float": "f"}[self.kind]
        return np.dtype(f"{self.byteorder}{code}{self.storage // 8}")

    @property
    def bytes_per_sample(self):
        """Bytes of one complex (I, Q) sample"""
        return 3 if self.packed else 2 * self.storage // 8

    @property
    def full_scale(self):
        """Component magnitude that maps to 1.0"""
        if self.kind == "int":
            return float(2 ** (self.bits - 1))
        if self.kind == "uint":
            return (2 ** self.bits - 1) / 2
        return 1.0

    def to_complex64(self, raw, valid=None):
        """Raw interleaved I/Q (ndarray or bytes-like) -> complex64 in [-1, 1].
        `valid` is the number of valid *bytes* in `raw` (default: all). Returns a new array."""
        return _converter(self)(raw, valid)

    def from_complex64(self, iq):
        """complex64 in [-1, 1] -> raw data in this layout (typed array, or uint8 bytes if packed)"""
        return _encoder(self)(np.asarray(iq))

    def __str__(self):
        return f"{self.kind}{self.bits}" + ("" if self.bits == self.storage else f" in {self.storage}") \
            + (" packed" if self.packed else "")

INT8 = SampleFormat("int", 8, 8)
UINT8 = SampleFormat("uint", 8, 8)
INT16 = SampleFormat("int", 16, 16)
INT12 = SampleFormat("int", 12, 16)                  # 12-bit values in 16-bit words
INT12_PACKED = SampleFormat("int", 12, 12, packed=True)  # I and Q in 3 bytes
FLOAT32 = SampleFormat("float", 32, 32)

def _bytes_of(raw):
    return raw if isinstance(raw, np.ndarray) else np.frombuffer(raw, dtype=np.uint8)

@lru_cache(maxsize=None)
def _converter(fmt: SampleFormat):
    if fmt.packed:
        return _packed12_converter(fmt)
    dtype, scale = fmt.dtype, np.float32(1.0 / fmt.full_scale)
    offset = np.float32(fmt.full_scale) if fmt.kind == "uint" else None
    scaled = fmt.kind != "float"
    def convert(raw, valid=None):
        if isinstance(raw, np.ndarray):
            a = raw if raw.dtype == dtype else raw.view(dtype)   # e.g. a uint8 byte view of int16 data
        else:
            a = np.frombuffer(raw, dtype=dtype)
        if valid is not None:
            a = a[:valid // dtype.itemsize]
        out = a[:len(a) & ~1].astype(np.float32)   # whole I/Q pairs only
        if offset is not None:
            out -= offset
        if scaled:
            out *= scale
        return out.view(np.complex64)
    return convert

def _packed12_converter(fmt):
    scale = np.float32(1.0 / fmt.full_scale)
    def convert(raw, valid=None):
        b = _bytes_of(raw)
        if valid is not None:
            b = b[:valid]
        b = b[:len(b) // 3 * 3].reshape(-1, 3).astype(np.int16)
        comps = np.empty((len(b), 2), dtype=np.int16)
        comps[:, 0] = b[:, 0] | ((b[:, 1] & 0x0F) << 8)           # I
        comps[:, 1] = (b[:, 1] >> 4) | (b[:, 2] << 4)             # Q
        comps = (comps ^ 0x800) - 0x800                           # sign extend 12 -> 16
        return (comps.astype(np.float32) * scale).reshape(-1).view(np.complex64)
    return convert

@lru_cache(maxsize=None)
def _encoder(fmt: SampleFormat):
    def components(iq):
        iq = np.ascontiguousarray(iq, dtype=np.complex64)
        return iq.view(np.float32)   # I, Q, I, Q ... as float32
    if fmt.kind == "float":
        return lambda iq: components(iq).astype(fmt.dtype)
    scale = fmt.full_scale
    if fmt.kind == "uint":
        lo, hi = 0, 2 ** fmt.bits - 1
        quant = lambda x: np.clip(np.rint(x * scale + scale), lo, hi)
    else:
        lo, hi = -(2 ** (fmt.bits - 1)), 2 ** (fmt.bits - 1) - 1
        quant = lambda x: np.clip(np.rint(x * scale), lo, hi)
    if not fmt.packed:
        return lambda iq: quant(components(iq)).astype(fmt.dtype)
    def encode(iq):
        c = quant(components(iq)).astype(np.int16).reshape(-1, 2) & 0x0FFF
        out = np.empty((len(c), 3), dtype=np.uint8)
        out[:, 0] = c[:, 0] & 0xFF
        out[:, 1] = (c[:, 0] >> 8) | ((c[:, 1] & 0x0F) << 4)
        out[:, 2] = c[:, 1] >> 4
        return out.reshape(-1)
    return encode
