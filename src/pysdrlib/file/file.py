"""Wrapper for processing files"""

import os
from pathlib import Path

import numpy as np

from .. import logger
from .. import err as pysdr_err
from ..base import Formats
from . import err

class File:
    """Read samples from a file like a device: `File(path, fmt)`, then `next`, `forward`, `seek`...

    `fmt` names one of `pysdrlib.Formats` (e.g. `"cf64"`, `"ci16t"`; see `pysdrlib.base.formats`). Samples are returned as stored, never scaled.
    Usable as a context manager, which opens and closes it:

        with File("capture.cfile", "cf64") as f:      # cf64: two float32 per sample
            for block in f.forward(8192, partial=True):
                ...
    """
    def __init__(self, path=None, fmt: str | None = None):
        self.log = logger.new("device.File")
        self.fmt: Formats = None # type: ignore
        self.path: Path = None # type: ignore
        self.count = 8192
        self.cur_samp = 0
        self.max_samp = 0
        self._fsize = 0
        self.f = None
        if fmt is not None:
            self.set_fmt(fmt)
        if path is not None:
            self.set_path(path)

    def __str__(self):
        return f"File ({self.cur_samp}/{self.max_samp},{self.fmt.name}): {self.path}"

    # Invariant while open: the file position is cur_samp * fmt.bytes
    def open(self):
        """Open the file for reading, at the current position"""
        self.log.trace("open()")
        if self.f is not None:
            self.log.warning("File is already open!")
        else:
            self.f = open(self.path, "rb")
            self._seek(self.cur_samp)
    def close(self):
        """Close the file; the position is kept"""
        self.log.trace("close()")
        if self.f is None:
            self.log.warning("File is already closed!")
        else:
            self.f.close()
            self.f = None

    def __enter__(self):
        self.open()
        return self
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def get_samples(self):
        """The next `count` samples (see `set_sample_count`), as a device's `get_samples` would"""
        return self.next(self.count)

    # File specific interface
    def set_sample_count(self, count: int):
        """Samples returned by `get_samples` (default 8192)"""
        self.log.trace("set_sample_count(%s)", count)
        self.count = count

    def set_fmt(self, fmt: str):
        """Set the sample format by name (a `pysdrlib.Formats` member); the file length in samples follows"""
        self.log.trace("set_fmt(%s)", fmt)
        self.fmt = Formats[fmt]
        if not self._fsize == 0:
            self.max_samp = self._fsize // self.fmt.bytes
    def get_fmt(self):
        """The sample format (a `pysdrlib.Formats` member)"""
        return self.fmt

    def set_path(self, path):
        """Set the file to read. Raises `FileNotFoundError` if it doesn't exist"""
        self.log.trace("set_path(%s)", path)
        if not isinstance(path, Path):
            path = Path(path)
        self.path = path
        self._fsize = path.stat().st_size
        if self.fmt is not None:
            self.max_samp = self._fsize // self.fmt.bytes
    def get_path(self):
        """The file being read (a `pathlib.Path`)"""
        return self.path

    def reset(self):
        """Go back to the first sample"""
        self.log.trace("reset()")
        self.cur_samp = 0
        if self.f is not None:
            self._seek(0)
    def seek(self, samp: int):
        """Move to sample index `samp`"""
        self.log.trace("seek(%s)", samp)
        if samp < 0 or samp > self.max_samp:
            raise err.Overflow(f"seek({samp}) outside 0..{self.max_samp}")
        self.cur_samp = samp
        if self.f is not None:
            self._seek(samp)
    def next(self, count: int, partial: bool = False):
        """Return the next <count> samples and move forward.

        With `partial=True`, a request running past EOF returns the remaining
        samples (possibly an empty array) instead of raising Overflow.
        """
        self.log.trace("next()")
        self._ensure_open("next")
        if partial:
            count = max(0, min(count, self.max_samp - self.cur_samp))
        if self.cur_samp + count > self.max_samp:
            raise err.Overflow(f"{self.cur_samp}+{count} > {self.max_samp}")
        samps = self._read(count)
        if len(samps) < count:
            raise err.Underflow(f"self._read({count}) at idx {self.cur_samp}, got {len(samps)}")
        self.cur_samp += count
        return samps
    def prev(self, count: int):
        """Move back <count> samples and return them (the samples just before the old position)"""
        self.log.trace("prev()")
        self._ensure_open("prev")
        if self.cur_samp - count < 0:
            raise err.Overflow(f"{self.cur_samp}-{count} < 0")
        self.cur_samp -= count
        self._seek(self.cur_samp)
        samps = self._read(count)
        self._seek(self.cur_samp)  # reading advanced the file; stay at cur_samp
        if len(samps) < count:
            raise err.Underflow(f"self._read({count}) at idx {self.cur_samp}, got {len(samps)}")
        return samps

    def _seek(self, samp: int):
        self.f.seek(samp * self.fmt.bytes) # type: ignore

    def _ensure_open(self, name):
        if self.f is None:
            raise pysdr_err.NotOpen(f"Cannot call `{name}` when file isn't open")

    def _read(self, count: int):
        """Read <count> samples at the current position (does not touch cur_samp)"""
        return self.fmt.read(self.f, count)

    def forward(self, count: int, partial: bool = False):
        """Yield blocks of <count> samples until EOF.

        With `partial=True`, a short final block is yielded too.
        """
        while self.cur_samp + count <= self.max_samp:
            yield self.next(count)
        if partial and self.cur_samp < self.max_samp:
            yield self.next(count, partial=True)
    def reverse(self, count: int):
        """Yield blocks of `count` samples from the current position back toward the start, each one before the last"""
        while self.cur_samp - count >= 0:
            # return self.prev(count)
            yield self.prev(count)

    def percent(self, fraction: bool = True):
        """Return how much of the file has been read: 0.0-1.0 by default, 0-100 with `fraction=False`"""
        if self.max_samp == 0:
            return 0.0
        done = float(self.cur_samp/self.max_samp)
        return done if fraction else done * 100

    def __call__(self, count, partial: bool = False):
        return self.forward(count, partial)
