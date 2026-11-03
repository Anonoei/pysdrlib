"""Raw device data <-> pysdrlib samples (complex64), for any ADC layout.

Devices deliver interleaved I/Q in whatever layout their hardware uses. A `SampleFormat`
describes that layout and converts it to complex64 scaled to [-1, 1]; `from_complex64` goes back.

    fmt = convert.INT12                  # 12-bit ADC values in 16-bit words
    samples = fmt.to_complex64(raw, valid_bytes)

Nothing in here is specific to a device. `valid` lengths are in bytes, as drivers report them.

The conversion itself is C++ (`_native/include/hardware_convert.hpp`), the same code a device's streaming thread runs.
This module describes the layouts and checks them.
"""
from dataclasses import dataclass

import numpy as np

try:
    from .lib import samples as _samples
except ImportError as exc:
    raise ImportError(
        "pysdrlib's compiled sample converter is not available (e.g. a source checkout that was never built, "
        "or an unsupported platform). Install a wheel for your platform, or build it with `pip install -e .` "
        "(or `python setup.py build_ext --inplace`)."
    ) from exc

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
        if isinstance(raw, np.ndarray) and not raw.flags.c_contiguous:
            raw = np.ascontiguousarray(raw)   # the extension reads memory as it is laid out
        return _samples.decode(raw, self.kind, self.bits, self.storage, self.packed, self.byteorder == ">", valid)

    def from_complex64(self, iq):
        """complex64 in [-1, 1] -> raw data in this layout (typed array, or uint8 bytes if packed)"""
        raw = _samples.encode(np.asarray(iq), self.kind, self.bits, self.storage, self.packed, self.byteorder == ">")
        return raw if self.packed else raw.view(self.dtype)

    def __str__(self):
        return f"{self.kind}{self.bits}" + ("" if self.bits == self.storage else f" in {self.storage}") \
            + (" packed" if self.packed else "")

INT8 = SampleFormat("int", 8, 8)
UINT8 = SampleFormat("uint", 8, 8)
INT16 = SampleFormat("int", 16, 16)
INT12 = SampleFormat("int", 12, 16)                  # 12-bit values in 16-bit words
INT12_PACKED = SampleFormat("int", 12, 12, packed=True)  # I and Q in 3 bytes
FLOAT32 = SampleFormat("float", 32, 32)
