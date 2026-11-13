"""A TCP stream of raw I/Q samples (receive only): see the README"""
import socket
import threading

from ..base import convert
from ..base.buffer import RotatingBuffer
from ..base.caps import Capabilities, Range
from ..base.device import Device
from .. import err

MAX_BUFFER_SAMPLES = 2**24
CHUNK_BYTES = 1 << 18

class TcpIq(Device):
    """Samples from a TCP server that sends raw interleaved I/Q. Open it with `host` and `port`; `cf` and `Fs` say what
    the stream is (nothing is sent to the server)"""
    NAME = "TCP IQ"
    CAPS = Capabilities(
        driver="",
        freq=[Range(0, 1e12)], default_freq=100_000_000,
        rates=[Range(1, 1e10)], default_rate=2_000_000,
        gains={"rx": {}, "tx": {}},
    )
    CAN_TRANSMIT = False
    DUPLEX = False

    def __init__(self, sample_format=convert.FLOAT32):
        """sample_format: layout of what the server sends (a `pysdrlib.base.convert.SampleFormat`)"""
        super().__init__()
        self.SAMPLE_FORMAT = sample_format
        self.buffer = RotatingBuffer()
        self.buffers = [self.buffer]
        self._sock = None
        self._thread = None
        self._stop = threading.Event()
        self.eof = False        # the server closed the stream

    def _open(self, host=None, port=1234, timeout=5.0):
        """host, port: the server. timeout: seconds to connect"""
        if host is None:
            raise err.NoDevice("tcpiq needs a server: open('tcpiq', host=..., port=...)")
        try:
            self._sock = socket.create_connection((host, int(port)), timeout=timeout)
        except OSError as exc:
            raise err.NoDevice(f"cannot connect to {host}:{port}: {exc}") from exc
        self._sock.settimeout(0.2)   # so the receive thread notices a stop
        self.eof = False
        return self._sock
    def _close(self):
        if self.state["rx"]:
            self.stop_rx()
        if self._sock is not None:
            self._sock.close()
        self._sock = None
        self.device = None

    def _set_rx_freq(self, freq, channel=0):
        pass   # declared, not sent: the server decides what it streams
    def _set_rx_sample_rate(self, Fs, channel=0):
        if not self.state["rx"]:
            self._resize_buffer()

    def _resize_buffer(self):
        self.buffer.reset(min(int(0.5 * self._Fs), MAX_BUFFER_SAMPLES))

    def _start_rx(self):
        self._resize_buffer()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="pysdrlib-tcpiq", daemon=True)
        self._thread.start()
    def _stop_rx(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    def _run(self):
        """Receive, keep whole samples, convert, hand to the ring"""
        unit = self.SAMPLE_FORMAT.bytes_per_sample
        pending = bytearray()
        while not self._stop.is_set():
            try:
                data = self._sock.recv(CHUNK_BYTES)
            except socket.timeout:
                continue
            except OSError:
                data = b""
            if not data:
                self.eof = True
                return
            pending += data
            whole = len(pending) - len(pending) % unit
            if whole:
                self.buffer.update(self.SAMPLE_FORMAT.to_complex64(bytes(pending[:whole]), whole))
                del pending[:whole]

    def _get_samples(self):
        return self.buffer.samples

    # the generic read path (Device.read) on the ring the receive thread fills
    def _rx_wait(self, count, timeout, slot=0):
        return self.buffer.wait(int(count), float(timeout))
    def _rx_read(self, count, slot=0):
        return self.buffer.read_indexed(int(count))
    def _rx_position(self, slot=0):
        return self.buffer.total, self.buffer.age
    def _rx_flush(self, slot=0):
        return len(self.buffer.read())

    def _set_rx_gain(self, gain, channel=0):
        return {}   # a stream has no gain to set (initialize asks)

    def _set_rx_channels(self, channels):
        pass
    def _set_tx_channels(self, channels):
        pass
