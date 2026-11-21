"""Write samples to a file in any of the Formats"""

from pathlib import Path

import numpy as np

from .. import logger
from .. import err as pysdr_err
from ..base import Formats
from ..base.io import RangeError
from . import sigmf as _sigmf

class FileSink:
    """Counterpart of File: `FileSink(path, fmt)`, then `write(samples)`.

    Truncates an existing file unless `append=True` (the format of an existing file is not checked).
    `open=True` opens it on construction.
    `scale=True` takes samples as a device gives them (full scale at +-1) and stores them at the format's full scale, the
    inverse of `File(..., scale=True)`. `clip=True` saturates values outside the format's range (and stores NaN as 0 in integer formats)
    instead of raising ValueError, and logs one warning
    ("N samples clipped") when the sink closes. `clipped` holds the running count. Usable as a context manager.

    A SigMF recording is written for a path ending in ".sigmf" (or ".sigmf-data"/".sigmf-meta"), or with `sigmf=True`:
    the samples in `<name>.sigmf-data`, and `<name>.sigmf-meta` with the format, `rate`, `freq` and `hw` (a description
    of the radio). The meta file is written on open and kept up to date by `flush` and `close`. `write(samples, meta=m)`
    with a device's `RxMeta` fills in the rate and frequency, starts a new capture at each retune, and annotates where
    samples were lost; `retune(freq)` starts a capture by hand.
    """
    def __init__(self, path, fmt: str = "cf32", append: bool = False, open: bool = False, clip: bool = False,
                 scale: bool = False, sigmf: bool | None = None, rate: float | None = None, freq: float | None = None, hw: str | None = None):
        self.log = logger.new("device.FileSink")
        self.sigmf = _sigmf.is_sigmf(path) if sigmf is None else bool(sigmf)
        self.path = _sigmf.paths(path)[0] if self.sigmf else Path(path)
        self.fmt: Formats = Formats.get(fmt)
        if self.sigmf and self.fmt.sigmf is None:
            raise ValueError(f"SigMF has no datatype for {self.fmt.name}")
        if self.sigmf and append:
            raise ValueError("appending to a SigMF recording is not supported")
        self.append = append
        self.clip = clip
        self.scale = scale   # samples come with full scale at +-1, like a device's (see Formats.unscale)
        self.count = 0    # samples written through this sink
        self.clipped = 0  # samples that were saturated (clip=True only)
        self.rate = rate
        self.hw = hw
        self.captures = [(0, freq)]   # (first sample, frequency) (SigMF)
        self.annotations = []
        self._datetime = None
        self._last_index = None       # the stream index after the last RxMeta written
        self._rate_warned = False
        self.f = None
        if open:
            self.open()

    def __str__(self):
        return f"FileSink ({self.count},{self.fmt.name}): {self.path}"

    def open(self):
        """Open the file for writing (truncating it unless `append=True`)"""
        self.log.trace("open()")
        if self.f is not None:
            self.log.warning("File is already open!")
        else:
            self.f = open(self.path, "ab" if self.append else "wb")
            if self.sigmf:
                self._datetime = _sigmf.now()
                self._write_meta()
    def close(self):
        """Close the file (writing the SigMF metadata), logging a warning if `clip=True` saturated any samples"""
        self.log.trace("close()")
        if self.f is None:
            self.log.warning("File is already closed!")
        else:
            self.f.close()
            self.f = None
            if self.sigmf:
                self._write_meta()
            if self.clipped:
                self.log.warning("%d samples clipped writing %s", self.clipped, self.path)

    def __enter__(self):
        self.open()
        return self
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    @property
    def freq(self):
        """The frequency of the samples being written now (SigMF: the last capture's)"""
        return self.captures[-1][1]

    def retune(self, freq):
        """The samples written from now on are at `freq` (a new SigMF capture)"""
        if self.captures[-1][0] == self.count:
            self.captures[-1] = (self.count, freq)
        elif freq != self.freq:
            self.captures.append((self.count, freq))

    def _note(self, meta):
        """What a device's RxMeta says about the block about to be written"""
        if self.rate is None:
            self.rate = meta.rate
        elif meta.rate != self.rate and self.sigmf and not self._rate_warned:
            self._rate_warned = True
            self.log.warning("the sample rate changed to %s while recording %s (SigMF has one rate)", meta.rate, self.path)
        if meta.freq is not None and meta.freq != self.freq:
            self.retune(meta.freq)
        if self._last_index is not None and meta.index > self._last_index:
            lost = meta.index - self._last_index   # between blocks: dropped, or skipped by a flush
            self.annotations.append({"core:sample_start": self.count, "core:comment": f"{lost} samples missing before"})
        for offset, n in meta.gaps:
            if offset or self._last_index is None:
                self.annotations.append({"core:sample_start": self.count + offset, "core:comment": f"{n} samples lost before"})
        self._last_index = meta.index + meta.count

    def _events(self, meta, base):
        for e in meta.events:
            if e.kind == "freq":
                self.captures.append((base + e.offset, e.value))

    def write(self, samples, meta=None):
        """Append samples (a 1-D array-like) in the sink's format.

        meta: the `RxMeta` a device's `read(..., meta=True)` returned with them: the rate, frequency, retunes and lost
        samples are recorded (in the SigMF metadata).
        Values outside the format's range raise ValueError unless the sink was made with `clip=True`.
        """
        if self.f is None:
            raise pysdr_err.NotOpen("Cannot call `write` when file isn't open")
        arr = np.asarray(samples)
        if arr.ndim != 1:
            raise ValueError(f"write() takes 1-D samples, got shape {arr.shape}; flatten or write per channel")
        if self.scale:
            arr = self.fmt.unscale(arr)
        nclipped = 0
        if self.clip:
            arr, nclipped = self._clip(arr)
        try:
            self.fmt.write(self.f, arr)
        except RangeError as e:
            raise RangeError(f"{e} (FileSink(clip=True) saturates instead)") from e
        # only after the write succeeded, so a failed write leaves the counters alone
        if meta is not None:
            self._note(meta)
            self._events(meta, self.count)
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
        """Write buffered samples to disk (and the SigMF metadata so far)"""
        if self.f is not None:
            self.f.flush()
            if self.sigmf:
                self._write_meta()

    def _write_meta(self):
        captures = [c for k, c in enumerate(self.captures) if k == 0 or c[0] < max(self.count, 1)]
        _sigmf.write(self.path, self.fmt, self.rate, captures, self._datetime, self.annotations,
                     {"core:hw": self.hw} if self.hw else None)
