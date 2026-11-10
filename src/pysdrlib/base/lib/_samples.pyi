"""
Sample conversion and ring buffer (pybind11)
"""
from __future__ import annotations
import collections.abc
import numpy
import numpy.typing
import typing
__all__: list[str] = ['Ring', 'ShmRing', 'decode', 'encode', 'shm_segment_size']
class Ring:
    """
    Thread-safe ring of complex64 samples that drops the oldest when full.
    """
    def __init__(self) -> None:
        ...
    def clear_dropped(self) -> int:
        """
        Return the dropped count and reset it to 0.
        """
    def read(self, count: typing.Any = None) -> numpy.typing.NDArray[numpy.complex64]:
        """
        Remove and return up to `count` of the oldest pending samples (all if None).
        """
    def read_indexed(self, count: typing.SupportsInt | typing.SupportsIndex) -> tuple[numpy.typing.NDArray[numpy.complex64], int]:
        """
        (samples, index of the first): up to `count` of the oldest pending samples.
        """
    def reset(self, arg0: typing.SupportsInt | typing.SupportsIndex) -> None:
        """
        Set the capacity; discards pending samples and clears `dropped`.
        """
    def skip(self, count: typing.SupportsInt | typing.SupportsIndex) -> None:
        """
        Record `count` samples lost before the ring: they take indices (a reader sees the jump) but no room.
        """
    def update(self, arg0: typing.Annotated[numpy.typing.ArrayLike, numpy.complex64]) -> None:
        """
        Append samples, dropping the oldest pending ones if capacity is exceeded.
        """
    def wait(self, count: typing.SupportsInt | typing.SupportsIndex, timeout: typing.SupportsFloat | typing.SupportsIndex) -> int:
        """
        Wait until `count` samples are pending (at most the capacity) or `timeout` seconds pass (negative: no limit); returns how many are pending.
        """
    @property
    def age(self) -> typing.Any:
        """
        Seconds since the last write (None: nothing written since reset_stats).
        """
    @property
    def available(self) -> int:
        ...
    @property
    def capacity(self) -> int:
        ...
    @property
    def dropped(self) -> int:
        ...
    @property
    def total(self) -> int:
        """
        Samples written since reset(): the index the next one gets.
        """
class ShmRing:
    """
    Complex64 samples in memory that several processes map: one writer, any number of readers with a cursor each (see hardware_shm.hpp).
    """
    def __init__(self, buffer: collections.abc.Buffer, capacity: typing.Any = None, stream_id: typing.SupportsInt | typing.SupportsIndex = 0) -> None:
        """
        With `capacity`, the writer: lays the ring out in `buffer` (zeroed, ShmRing.segment_size(capacity) bytes). Without, a reader of one a writer laid out.
        """
    def close(self) -> None:
        """
        Writer: no more samples are coming.
        """
    def publish(self, samples: typing.Annotated[numpy.typing.ArrayLike, numpy.complex64], first: typing.SupportsInt | typing.SupportsIndex) -> None:
        """
        Writer: append samples whose first has stream index `first` (at or after head; later records a gap).
        """
    def read(self, out: typing.Annotated[numpy.typing.ArrayLike, numpy.complex64], pos: typing.SupportsInt | typing.SupportsIndex) -> tuple[int, int]:
        """
        Reader: fill `out` with up to len(out) samples from stream index `pos`; returns (count, index of the first). The index is past `pos` when the reader was lapped or was in a gap; with count 0 it is where to continue.
        """
    def release(self) -> None:
        """
        Let go of the buffer (before the segment is closed).
        """
    @property
    def capacity(self) -> int:
        ...
    @property
    def closed(self) -> bool:
        ...
    @property
    def head(self) -> int:
        """
        The stream index after the newest published sample.
        """
    @property
    def stream_id(self) -> int:
        ...
def decode(raw: collections.abc.Buffer, kind: str, bits: typing.SupportsInt | typing.SupportsIndex, storage: typing.SupportsInt | typing.SupportsIndex, packed: bool = False, big_endian: bool = False, valid: typing.Any = None) -> numpy.typing.NDArray[numpy.complex64]:
    ...
def encode(iq: typing.Annotated[numpy.typing.ArrayLike, numpy.complex64], kind: str, bits: typing.SupportsInt | typing.SupportsIndex, storage: typing.SupportsInt | typing.SupportsIndex, packed: bool = False, big_endian: bool = False) -> numpy.typing.NDArray[numpy.uint8]:
    ...
def shm_segment_size(capacity: typing.SupportsInt | typing.SupportsIndex) -> int:
    """
    Bytes of memory a ShmRing of `capacity` samples needs.
    """
