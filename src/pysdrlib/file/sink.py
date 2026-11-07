"""Write samples to a file in any of the Formats"""

from pathlib import Path

import numpy as np

from .. import logger
from .. import err as pysdr_err
from ..base import Formats
from ..base.io import RangeError

class FileSink:
    """Counterpart of File: `FileSink(path, fmt)`, then `write(samples)`.

    Truncates an existing file unless `append=True` (the format of an existing file is not checked).
    `open=True` opens it on construction.
    `clip=True` saturates values outside the format's range (and stores NaN as 0 in integer formats)
    instead of raising ValueError, and logs one warning
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
        """Append samples (a 1-D array-like) in the sink's format.

        Values outside the format's range raise ValueError unless the sink was made with `clip=True`.
        """
        if self.f is None:
            raise pysdr_err.NotOpen("Cannot call `write` when file isn't open")
        arr = np.asarray(samples)
        if arr.ndim != 1:
            raise ValueError(f"write() takes 1-D samples, got shape {arr.shape}; flatten or write per channel")
        nclipped = 0
        if self.clip:
            arr, nclipped = self._clip(arr)
        try:
            self.fmt.write(self.f, arr)
        except RangeError as e:
            raise RangeError(f"{e} (FileSink(clip=True) saturates instead)") from e
        # only after the write succeeded, so a failed write leaves the counters alone
        self.count += len(arr)
        self.clipped += nclipped

    def _clip(self, arr):
        limits = self.fmt.limits()
        if limits is None or arr.size == 0:
            return arr, 0
        lo, hi = limits
        is_complex = arr.dtype.kind == "c"
        # clip I and Q separately, in the array's own precision
        parts = np.ascontiguousarray(arr).view(arr.real.dtype) if is_complex else arr
        bad = (parts < lo) | (parts > hi)
        if parts.dtype.kind == "f" and self.fmt.integer():
            nan = np.isnan(parts)  # NaN has no integer value: store 0 and count it (inf saturates)
            if nan.any():
                parts = np.where(nan, 0, parts).astype(parts.dtype, copy=False)
                bad |= nan
        if is_complex:
            bad = bad.reshape(-1, 2).any(axis=1)
        nbad = int(bad.sum())
        if nbad == 0:
            return arr, 0
        out = np.clip(parts, lo, hi)
        return (out.view(arr.dtype) if is_complex else out), nbad

    def flush(self):
        if self.f is not None:
            self.f.flush()
