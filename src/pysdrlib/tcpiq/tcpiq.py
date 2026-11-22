"""A TCP stream of raw I/Q samples (receive only), as the client or the server: see `pysdrlib.tcpiq`"""
from ..base import convert
from ..base.caps import Capabilities, Range
from ..base.native import NativeDevice
from .. import err, warn

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
    RATE_TOLERANCE = 0.1   # warn when samples arrive this far (relatively) from the declared rate ...
    RATE_WINDOWS = 3       # ... in this many measurements in a row (about a second each)

    device: lib.tcpiq.device   # the extension's device (None until `open`)

    def _open(self, host=None, port=1234, timeout=5.0, listen=False, sample_format=convert.FLOAT32, reconnect_timeout=10.0):
        """host, port: the server to connect to; or, with `listen`, the address to listen on (host default: every
        interface; port 0: any free one, see `port`). timeout: seconds to connect.
        reconnect_timeout (listening): seconds to wait for a client that dropped to come back (or another to connect);
        after that, a read with nothing queued raises err.Lost, until one does. None: wait as long as it takes. Before
        the first client, a read waits as long as it takes.
        sample_format: layout of what the other end sends, a `convert.SampleFormat` or a name ("cf32", "ci16",
        "cu8", ...: one of `pysdrlib.tcpiq.FORMATS`)"""
        self.reconnect_timeout = None if reconnect_timeout is None else float(reconnect_timeout)
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

    @property
    def measured_rate(self):
        """Samples per second arriving, over the latest second or so (receiving or not), or None when not known yet
        (or nothing is arriving). Only a sender paced in real time arrives at its sample rate: this checks the declared
        `Fs` (a warning when they disagree), it never sets it"""
        rate = self.device.rx_rate if self.device is not None else 0.0
        return rate or None

    @property
    def rate_mismatch(self):
        """Whether `measured_rate` is more than RATE_TOLERANCE (relatively) from the declared `Fs`, or None while it
        isn't known. The latest measurement alone: `read` warns (warn.RateMismatch) only after RATE_WINDOWS in a row"""
        rate = self.measured_rate
        if rate is None or not self._Fs:
            return None
        return abs(rate - self._Fs) > self.RATE_TOLERANCE * self._Fs

    def _check_counters(self, *counters, have_samples):
        super()._check_counters(*counters, have_samples=have_samples)
        d = self.device
        windows = d.rate_windows
        if windows == getattr(self, "_rate_seen", None):
            return
        self._rate_seen = windows
        rate = d.rx_rate
        stream = d.connections
        if stream != getattr(self, "_rate_stream", None):   # a new client: judge it afresh
            self._rate_stream, self._rate_off, self._rate_warned = stream, 0, False
        if not rate or abs(rate - self._Fs) <= self.RATE_TOLERANCE * self._Fs:
            self._rate_off = 0
            return
        self._rate_off += 1
        if self._rate_off >= self.RATE_WINDOWS and not self._rate_warned:
            self._rate_warned = True
            warn.RateMismatch(f"{self.NAME}: samples arrive at {rate:,.0f}/s, but Fs is declared as {self._Fs:,.0f}; "
                              "set_rx_sample_rate() to what the sender streams (a sender not paced in real time, "
                              "replaying a file, also arrives faster)")

    def _set_rx_sample_rate(self, Fs, channel=0):
        self._rate_off, self._rate_warned = 0, False   # judge the new rate afresh
        self._rate_changed()

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

    def _rx_lost(self):
        """Connected: the server closed the stream, or keepalive found it gone. Listening: the client dropped
        `reconnect_timeout` seconds ago, and none has connected since"""
        d, limit = self.device, getattr(self, "reconnect_timeout", None)
        if d is None:
            return None
        if not d.listening:
            return ("the server closed the stream, or stopped answering: open the device again to reconnect"
                    if d.eof else None)
        if limit is None:
            return None
        gone = d.disconnected_for
        if gone > limit:
            return (f"the client dropped {gone:.1f}s ago and has not reconnected (reconnect_timeout {limit:g}s); "
                    "still listening: reading works again once one connects")
        return None

    def _set_rx_freq(self, freq, channel=0):
        pass   # declared, not sent: the sender decides what it streams

    def _set_rx_gain(self, gain, channel=0):
        return {}   # a stream has no gain to set (initialize asks)

    def _set_rx_channels(self, channels):
        pass
    def _set_tx_channels(self, channels):
        pass
