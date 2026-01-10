"""Wrapper for processing files"""

import os
from pathlib import Path
import numpy as np

from ..base import Formats
from . import err

class File:
    def __init__(self):
        self.fmt: Formats = None # type: ignore
        self.path: Path = None # type: ignore
        self.count = 8192
        self.cur_samp = 0
        self.max_samp = 0
        self._fsize = 0
        self.f = None

    def __str__(self):
        return f"File ({self.cur_samp}/{self.max_samp},{self.fmt.name}): {self.path}"

    def open(self):
        if self.f is None:
            self.f = open(self.path, "rb")
            self.f.seek(self.cur_samp // self.fmt.bytes)
    def close(self):
        if self.f is not None:
            self.f.close()
    def get_samples(self):
        return self.next(self.count)

    # File specific interface
    def set_sample_count(self, count: int):
        self.count = count
    def set_fmt(self, fmt: str):
        self.fmt = Formats[fmt]
        if not self._fsize == 0:
            self.max_samp = self._fsize // self.fmt.bytes
    def get_fmt(self):
        return self.fmt
    def set_path(self, path):
        if not isinstance(path, Path):
            path = Path(path)
        self.path = path
        self._fsize = path.stat().st_size
        self.max_samp = self._fsize // self.fmt.bytes
    def get_path(self):
        return self.path

    def reset(self):
        self.cur_samp = 0

    def next(self, count: int):
        # count *= 2
        if self.cur_samp + count > self.max_samp:
            raise err.Overflow(f"{self.cur_samp}+{count} > {self.max_samp}")
        samps = self._read(count)
        self.cur_samp += count
        return samps
    def prev(self, count: int):
        # count *= 2
        if self.cur_samp - count < 0:
            raise err.Overflow(f"{self.cur_samp}-{count} < 0")
        self.cur_samp -= count
        samps = self._read(count)
        return samps
    def _read(self, count: int):
        samps = self.fmt.read(self.f, count)
        self.cur_samp += count
        return samps

    def forward(self, count: int):
        """Return <count> samples until EOF"""
        while self.cur_samp + count <= self.max_samp:
            # return self.next(count)
            yield self.next(count)
    def reverse(self, count: int):
        while self.cur_samp - count >= 0:
            # return self.prev(count)
            yield self.prev(count)

    def percent(self):
        """Return percent of file read"""
        return float(self.cur_samp/self.max_samp)*100

    def __call__(self, count):
        return self.forward(count)
