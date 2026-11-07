"""Write samples to a file in any of the Formats"""

from pathlib import Path

import numpy as np

from .. import logger
from .. import err as pysdr_err
from ..base import Formats

class FileSink:
    """Counterpart of File: `FileSink(path, fmt)`, then `write(samples)`.

    Truncates an existing file unless `append=True` (the format of an existing file is not checked).
    `open=True` opens it on construction.
    `clip=True` saturates values outside the format's range instead of raising, and logs one warning
    ("N samples clipped") when the sink closes. `clipped` holds the running count. Usable as a context manager.
    """
    def __init__(self, path, fmt: str, append: bool = False, open: bool = False, clip: bool = False):
        self.log = logger.new("device.FileSink")
        self.path = Path(path)
        self.fmt: Formats = Formats[fmt]
        self.append = append
        self.clip = clip
        self.count = 0    # samples written through this sink
        self.clipped = 0  # samples that were saturated (clip=True only)
        self.f = None
        if open:
            self.open()

    def __str__(self):
        return f"FileSink ({self.count},{self.fmt.name}): {self.path}"

    def open(self):
        self.log.trace("open()")
        if self.f is not None:
            self.log.warning("File is already open!")
        else:
            self.f = open(self.path, "ab" if self.append else "wb")
    def close(self):
        self.log.trace("close()")
        if self.f is None:
            self.log.warning("File is already closed!")
        else:
            self.f.close()
            self.f = None
            if self.clipped:
                self.log.warning("%d samples clipped writing %s", self.clipped, self.path)

    def __enter__(self):
        self.open()
        return self
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def write(self, samples):
        """Append samples in the sink's format"""
        if self.f is None:
            raise pysdr_err.NotOpen("Cannot call `write` when file isn't open")
        n = len(samples)
        if self.clip:
            samples = self._clip(samples)
        self.fmt.write(self.f, samples)
        self.count += n

    def _clip(self, samples):
        limits = self.fmt.limits()
        arr = np.asarray(samples)
        if limits is None or arr.size == 0:
            return arr
        lo, hi = limits
        if arr.dtype.kind == "c":  # clip I and Q separately, in the array's own precision
            parts = np.ascontiguousarray(arr).view(arr.real.dtype)
            bad = ((parts < lo) | (parts > hi)).reshape(-1, 2).any(axis=1)
        else:
            parts = arr
            bad = (parts < lo) | (parts > hi)
        nbad = int(bad.sum())
        if nbad == 0:
            return arr
        self.clipped += nbad
        return np.clip(parts, lo, hi).view(arr.dtype) if arr.dtype.kind == "c" else np.clip(parts, lo, hi)

    def flush(self):
        if self.f is not None:
            self.f.flush()
