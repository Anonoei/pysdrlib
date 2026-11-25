"""`ShmDevice`: a stream another process publishes (`ShmStream`), received like a radio"""
import threading
import time

import numpy as np

from .. import err
from ..base import convert
from ..base.caps import Capabilities, Range
from ..base.lib import samples as _samples
from ..base.native import NativeDevice
from . import ring, stream


class ShmDevice(NativeDevice):
    """A stream published on this machine by `ShmStream`, received like a radio: the same `read`, `stream`, `RxMeta`
    and drop reporting.

        sdr = pysdrlib.open("shm@rpps.ch3")              # or pysdrlib.open("shm", name="rpps.ch3")
        sdr.initialize()                                  # the stream's rate and frequency
        x, m = sdr.read(65536, meta=True)

    The rate and frequency are the publisher's: setting them only clamps to them (raises with `strict`). When the
    publisher retunes, the stream follows, with a "freq" or "rate" `Event` in `RxMeta`. Receiving starts with what is
    published next, not what the ring still holds.

    `RxMeta.index` counts the publisher's gaps, and a reader that fell a ring behind, as lost samples (`gaps`).
    `hw_time` is the wall-clock time of the first sample, from the time the publisher gave (or took) for the first
    sample of the generation, so `RxMeta.wall_time` is too.

    When the publisher closes the stream (or its process ends), `read` raises `err.Lost` once what is there is read."""
    NAME = "Shared-memory stream"
    CAPS = Capabilities(
        driver="",
        freq=[Range(0, 1e12)], default_freq=100_000_000,
        rates=[Range(1, 1e10)], default_rate=2_000_000,
        gains={"rx": {}, "tx": {}},
    )
    CAN_TRANSMIT = False
    STALL_TIMEOUT = None          # a publisher may pause: that isn't a stalled radio
    MAX_BUFFER_SAMPLES = 2**24
    SAMPLE_FORMAT = convert.FLOAT32
    POLL_SECONDS = 0.002          # how often the ring is looked at while it has nothing new
    ALIVE_SECONDS = 0.5           # how often the publisher's process is checked while nothing arrives
    BLOCK_SAMPLES = 65536

    def __init__(self):
        super().__init__()
        self.manifest: dict = None  # type: ignore   # the stream's manifest (its generation as received)
        self.directory = None
        self._ended = None          # why the stream ended (the publisher closed it, or is gone)
        self._thread = None
        self._stop = threading.Event()

    @classmethod
    def find(cls, directory=None):
        """The streams published on this machine (a transmit channel in an air (`pysdrlib.air`) is virtual: listed by
        `find(virtual=True)`)"""
        from ..devices import DeviceInfo
        found = []
        for m in stream.streams(directory):
            info = m.get("info") or {}
            extra = {"info": info, "virtual": True} if info.get("air") else {"info": info}
            found.append(DeviceInfo("shm", f"{m['name']} ({m['Fs'] / 1e6:g} MS/s at {m['cf'] / 1e6:g} MHz)", m["name"],
                                    open_args={"name": m["name"]}, extra=extra))
        return found

    def _open(self, name=None, serial_number=None, directory=None):
        """name: the stream's name; serial: its name, or the end of it (what a target string "shm@NAME" gives);
        neither: the only stream there is. directory: where the manifests are (see `stream.stream_dir`)"""
        serial = serial_number
        live = stream.streams(directory)
        names = [m["name"] for m in live]
        if name is None and serial is not None:
            matches = [n for n in names if n == serial] or [n for n in names if n.endswith(serial)]
            if len(matches) != 1:
                raise err.NoDevice(f"{'no' if not matches else 'more than one'} stream is named {serial!r} "
                                   f"(streams: {', '.join(names) or 'none'})", reason="none_attached" if not names else "unmet")
            name = matches[0]
        if name is None:
            if len(names) != 1:
                raise err.NoDevice(f"open('shm', name=...): which stream? ({', '.join(names)})" if names else
                                   f"no stream is published in {stream.stream_dir(directory)}", reason="none_attached" if not names else "unmet")
            name = names[0]
        m = next((m for m in live if m["name"] == name), None)
        if m is None:
            raise err.NoDevice(f"no stream {name!r} is published (streams: {', '.join(names) or 'none'})",
                               reason="none_attached" if not names else "unmet")
        self.manifest, self.directory, self._ended = m, directory, None
        self._Fs, self._cf = float(m["Fs"]), float(m["cf"])
        dev = _samples.NullDevice()
        fmt = self.SAMPLE_FORMAT
        dev.set_format(fmt.kind, fmt.bits, fmt.storage, fmt.packed)
        self.device = dev
        self._rate_changed()
        return dev

    def _capabilities(self, caps):
        m = self.manifest
        if m is not None:
            caps.rates, caps.default_rate = [Range(m["Fs"], m["Fs"])], m["Fs"]
            caps.freq, caps.default_freq = [Range(m["cf"], m["cf"])], m["cf"]
        caps.channels = {"rx": 1, "tx": 0}
        return caps

    @property
    def name(self) -> str:
        """The stream's name"""
        return self.manifest["name"] if self.manifest else None

    def _close(self):
        if self.state["rx"]:
            self.stop_rx()
        self.device.close()

    def _rx_lost(self):
        return self._ended

    # --- the publisher's ring, on a thread --- #
    def _attach(self, m):
        try:
            return ring.ShmReader(m["segment"])
        except (FileNotFoundError, ValueError):   # gone (retuned again, or closed) before we got to it
            return None

    def _latest(self):
        return stream.read_manifest(stream.manifest_path(self.manifest["name"], self.directory))

    def _follow(self, new):
        """The publisher retuned: the samples delivered from here on are at the new generation's rate and frequency"""
        old, self.manifest = self.manifest, new
        self._caps = None
        if new["Fs"] != old["Fs"]:
            self._tune_rate(float(new["Fs"]), "rx", 0, False)
        if new["cf"] != old["cf"]:
            self._tune_freq(float(new["cf"]), "rx", 0, False)

    def _stamp(self, m, index):
        """The wall-clock time of the ring's sample `index` becomes the radio time of the next sample delivered"""
        if m.get("wall_time") is None:   # published after the manifest we have: the anchor came with the first samples
            latest = self._latest() or {}
            if latest.get("segment") == m["segment"]:
                m["wall_time"], m["wall_index"] = latest.get("wall_time"), latest.get("wall_index")
            elif m["segment"] in (latest.get("anchors") or {}):   # retuned since: the manifest keeps the anchor
                m["wall_time"], m["wall_index"] = latest["anchors"][m["segment"]]
        if m.get("wall_time") is not None and m.get("wall_index") is not None:
            self.device.stamp_rx(m["wall_time"] + (index - m["wall_index"]) / m["Fs"], 0)

    def _run(self, m, reader, pos):
        expect = None                 # the ring index the next sample delivered should have (None: whatever comes)
        buf = np.empty(self.BLOCK_SAMPLES, dtype=np.complex64)
        checked = time.monotonic()
        try:
            while not self._stop.is_set():
                n, first = reader.read(buf, pos) if reader is not None else (0, pos)
                if n:
                    if first != expect:
                        if expect is not None:
                            self.device._skip_rx(first - expect, 0)
                        self._stamp(m, first)
                    self.device._dispatch_rx(buf[:n].view(np.float32), None, 0)
                    pos = expect = first + n
                    continue
                pos = max(pos, first)
                if reader is None or reader.closed:   # retuned (a new generation is published first) or closed
                    new = self._latest()
                    if new is not None and new["segment"] != m["segment"]:
                        if reader is not None:
                            reader.close()
                        reader, pos, expect = self._attach(new), 0, None
                        m = dict(new)
                        self._follow(new)
                        continue
                    self._ended = "the publisher closed the stream: open it again when it is published again"
                elif time.monotonic() - checked > self.ALIVE_SECONDS:
                    checked = time.monotonic()
                    if not stream._alive(m.get("pid")):
                        self._ended = f"the publisher (process {m.get('pid')}) is gone"
                self._stop.wait(self.POLL_SECONDS)
        finally:
            if reader is not None:
                reader.close()

    def _start_rx(self):
        super()._start_rx()
        self._ended = None
        self._stop.clear()
        m = dict(self.manifest)
        reader = self._attach(m)
        pos = reader.head if reader is not None else 0   # now: what is published after start_rx returns is received
        self._thread = threading.Thread(target=self._run, args=(m, reader, pos), name="pysdrlib-shm", daemon=True)
        self._thread.start()

    def _stop_rx(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None
        super()._stop_rx()

    def _set_rx_freq(self, freq, channel=0):
        pass   # the publisher's (caps hold it to that)

    def _set_rx_sample_rate(self, Fs, channel=0):
        pass

    def _set_rx_gain(self, gain, channel=0):
        pass   # no stages: a total of 0 dB

    def _set_rx_channels(self, channels):
        pass
