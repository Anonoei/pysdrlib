"""
TCP I/Q stream bindings (pybind11): the socket is read on a thread of the device's own, without the GIL
"""
from __future__ import annotations
import collections.abc
import numpy
import numpy.typing
from pysdrlib.tcpiq.lib import err
import typing
__all__: list[str] = ['connect', 'device', 'err', 'listen']
class device:
    def __init__(self) -> None:
        """
        A closed device; use the module's open() to get an open one.
        """
    def _dispatch_rx(self, data: collections.abc.Buffer, valid: typing.Any = None, slot: typing.SupportsInt | typing.SupportsIndex = 0) -> int:
        ...
    def _skip_rx(self, samples: typing.SupportsInt | typing.SupportsIndex, slot: typing.SupportsInt | typing.SupportsIndex = 0) -> None:
        ...
    def close(self) -> None:
        ...
    def is_streaming(self) -> bool:
        ...
    def read_rx(self, count: typing.Any = None, slot: typing.SupportsInt | typing.SupportsIndex = 0) -> numpy.typing.NDArray[numpy.complex64]:
        ...
    def read_rx_into(self, out: typing.Annotated[numpy.typing.ArrayLike, numpy.complex64], timeout: typing.SupportsFloat | typing.SupportsIndex, slot: typing.SupportsInt | typing.SupportsIndex = 0) -> tuple[int, int, int, int, int, int, float]:
        ...
    def reset_rx_stats(self) -> None:
        ...
    def rx_counters(self, slot: typing.SupportsInt | typing.SupportsIndex = 0) -> tuple[int, int, int, int, float]:
        ...
    def rx_sample_format(self) -> tuple[str, int, int, bool]:
        ...
    def rx_stats(self, slot: typing.SupportsInt | typing.SupportsIndex = 0) -> dict:
        ...
    def set_rx_buffer(self, samples: typing.SupportsInt | typing.SupportsIndex, slots: typing.SupportsInt | typing.SupportsIndex = 1) -> None:
        """
        Queue up to `samples` complex samples (the oldest are dropped past that) in each of `slots` queues (one per channel of a multi-channel stream); discards pending ones.
        """
    def stamp_rx(self, t: typing.SupportsFloat | typing.SupportsIndex, slot: typing.SupportsInt | typing.SupportsIndex = 0) -> None:
        ...
    def start_rx(self) -> None:
        ...
    def stop_rx(self) -> None:
        ...
    def take_rx_stamps(self, slot: typing.SupportsInt | typing.SupportsIndex = 0) -> list:
        ...
    def wait_rx(self, samples: typing.SupportsInt | typing.SupportsIndex, timeout: typing.SupportsFloat | typing.SupportsIndex, slot: typing.SupportsInt | typing.SupportsIndex = 0) -> int:
        """
        Wait until `samples` are queued (at most the queue's size) or `timeout` seconds pass (negative: no limit); returns how many are queued.
        """
    @property
    def connections(self) -> int:
        ...
    @property
    def eof(self) -> bool:
        ...
    @property
    def is_open(self) -> bool:
        ...
    @property
    def listening(self) -> bool:
        ...
    @property
    def peer(self) -> str:
        ...
    @property
    def port(self) -> int:
        ...
    @property
    def rx_slots(self) -> int:
        ...
def connect(host: str, port: typing.SupportsInt | typing.SupportsIndex, timeout: typing.SupportsFloat | typing.SupportsIndex, kind: str, bits: typing.SupportsInt | typing.SupportsIndex, storage: typing.SupportsInt | typing.SupportsIndex, packed: bool = False, big_endian: bool = False) -> device:
    """
    Connect to a server that sends raw I/Q in the given layout
    """
def listen(host: str, port: typing.SupportsInt | typing.SupportsIndex, kind: str, bits: typing.SupportsInt | typing.SupportsIndex, storage: typing.SupportsInt | typing.SupportsIndex, packed: bool = False, big_endian: bool = False) -> device:
    """
    Listen for a client that sends raw I/Q in the given layout (port 0: any free port, see device.port)
    """
