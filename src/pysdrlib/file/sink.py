"""Write samples to a file in any of the Formats"""

from pathlib import Path

from .. import logger
from .. import err as pysdr_err
from ..base import Formats

class FileSink:
    """Counterpart of File: `FileSink(path, fmt)`, then `write(samples)`.

    Truncates an existing file unless `append=True`. Usable as a context manager.
    """
    def __init__(self, path, fmt: str, append: bool = False):
        self.log = logger.new("device.FileSink")
        self.path = Path(path)
        self.fmt: Formats = Formats[fmt]
        self.append = append
        self.count = 0  # samples written through this sink
        self.f = None

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
        self.fmt.write(self.f, samples)
        self.count += n

    def flush(self):
        if self.f is not None:
            self.f.flush()
