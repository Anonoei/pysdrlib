"""A TCP stream of raw I/Q samples (receive only), as the client or the server: see the README"""
from ..base import convert
from ..base.caps import Capabilities, Range
from ..base.native import NativeDevice
from .. import err

from . import lib

class TcpIq(NativeDevice):
    """Samples over TCP, raw interleaved I/Q. Connect to a server that sends them (`host`, `port`), or listen for a
    client that does (`listen=True`). `cf` and `Fs` say what the stream is (nothing is sent back).

    The socket is read on a thread of the extension's own, without the GIL, as fast as the network delivers: a sender is
    never held up by Python. Samples not read within `BUFFER_SECONDS` are dropped (oldest first) and reported like any
    native device's (`on_drop`, `warn.Dropped`)."""
    NAME = "TCP IQ"
    CAPS = Capabilities(
        driver="",
        freq=[Range(0, 1e12)], default_freq=100_000_000,
        rates=[Range(1, 1e10)], default_rate=2_000_000,
        gains={"rx": {}, "tx": {}},
    )
    CAN_TRANSMIT = False
    DUPLEX = False
    STALL_TIMEOUT = None          # the sender sets the pace, and a server may have no client yet
    MAX_BUFFER_SAMPLES = 2**24    # the rate is only declared: don't let it size a huge queue
    SAMPLE_FORMAT = convert.FLOAT32   # the default; `open(sample_format=...)` gives an instance its own

    device: lib.tcpiq.device   # the extension's device (None until `open`)

    def _open(self, host=None, port=1234, timeout=5.0, listen=False, sample_format=convert.FLOAT32):
        """host, port: the server to connect to; or, with `listen`, the address to listen on (host default: every
        interface; port 0: any free one, see `port`). timeout: seconds to connect.
        sample_format: layout of what the other end sends, a `convert.SampleFormat` or a name ("cf32", "ci16",
        "cu8", ...: one of `pysdrlib.tcpiq.FORMATS`)"""
        f = self.SAMPLE_FORMAT = convert.sample_format(sample_format)
        layout = (f.kind, f.bits, f.storage, f.packed, f.byteorder == ">")
        if listen:
            host = "0.0.0.0" if host is None else host
            try:
                return lib.tcpiq.listen(host, int(port), *layout)
            except lib.err.TcpIqError as exc:
                raise err.NoDevice(f"cannot listen on {host}:{port}: {exc}") from exc
        if host is None:
            raise err.NoDevice("tcpiq needs a server: open('tcpiq', host=..., port=...), or listen=True to be one")
        try:
            return lib.tcpiq.connect(host, int(port), float(timeout), *layout)
        except lib.err.TcpIqError as exc:
            raise err.NoDevice(str(exc)) from exc

    @property
    def eof(self):
        """The server closed the stream (connected only: a listening device accepts the next client)"""
        return self.device is not None and self.device.eof
    @property
    def peer(self):
        """"host:port" of the other end, or None when nothing is connected"""
        return (self.device.peer or None) if self.device is not None else None
    @property
    def connections(self):
        """Clients accepted since `open` (listening)"""
        return self.device.connections if self.device is not None else 0
    @property
    def reconnects(self):
        """Clients that connected while one was (listening): each took over, as a sender reconnecting after its
        connection died does"""
        return self.device.replaced if self.device is not None else 0
    @property
    def port(self):
        """The local port (the one chosen, after listening on port 0)"""
        return self.device.port if self.device is not None else None

    RTL_TUNERS = {0: "unknown", 1: "E4000", 2: "FC0012", 3: "FC0013", 4: "FC2580", 5: "R820T", 6: "R828D"}

    @property
    def rtl_tcp(self):
        """What the rtl_tcp server said about its dongle, when the stream began with rtl_tcp's 12-byte header (which
        is skipped, not read as samples): {"tuner": name, "tuner_type": n, "gain_count": n}; None otherwise (or not yet)"""
        d = self.device
        if d is None or not d.rtl_header:
            return None
        return {"tuner": self.RTL_TUNERS.get(d.rtl_tuner, f"type {d.rtl_tuner}"), "tuner_type": d.rtl_tuner,
                "gain_count": d.rtl_gain_count}

    def _set_rx_freq(self, freq, channel=0):
        pass   # declared, not sent: the sender decides what it streams
    def _set_rx_sample_rate(self, Fs, channel=0):
        self._rate_changed()

    def _set_rx_gain(self, gain, channel=0):
        return {}   # a stream has no gain to set (initialize asks)

    def _set_rx_channels(self, channels):
        pass
    def _set_tx_channels(self, channels):
        pass
