"""A recording played back as a receiving device: `pysdrlib.open("file", path=...)`"""
import collections
import threading
import time

import numpy as np

from ..base import convert
from ..base.caps import Capabilities, Range
from ..base.lib import samples as _samples
from ..base.native import NativeDevice
from .file import File

class Replay(NativeDevice):
    """A recording, received like a radio: the same `read`, `stream`, `RxMeta` and drop reporting, from a thread that
    delivers it in blocks at its sample rate (or as fast as it is read, `realtime=False`).

        sdr = pysdrlib.open("file", path="capture.sigmf")                          # SigMF: format, rate, frequency
        sdr = pysdrlib.open("file", path="capture.cfile", fmt="cf32", rate=2e6, freq=100e6, loop=True)
        sdr.initialize()            # the recording's rate and frequency
        sdr.start_rx()

    The frequency follows the recording: each SigMF capture is a retune (an `Event` in `RxMeta`, and `get_rx_freq`).
    `set_rx_freq` only relabels the stream (a recording can't be retuned), and the rate is the recording's. At the end,
    `loop` starts it again; otherwise delivery stops and `eof` is True (`read` then times out)."""
    NAME = "File replay"
    CAPS = Capabilities(
        driver="",
        freq=[Range(0, 1e12)], default_freq=100_000_000,
        rates=[Range(1, 1e10)], default_rate=2_000_000,
        gains={"rx": {}, "tx": {}},
    )
    CAN_TRANSMIT = False
    DUPLEX = False
    STALL_TIMEOUT = None          # the recording ends: that is not a stalled radio
    MAX_BUFFER_SAMPLES = 2**24
    SAMPLE_FORMAT = convert.FLOAT32   # File scales to complex64 before delivering

    def __init__(self):
        super().__init__()
        self.file: File = None  # type: ignore
        self.loop = False
        self.realtime = True
        self.block_samples = 65536
        self._eof = False
        self._seek_to = None
        self._freq = None              # the recording's frequency at the playback position
        self._retunes = collections.deque()   # (stream index, freq) from the playback thread, for the reader's
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()   # the file, between the playback thread and seek

    def _open(self, path=None, fmt=None, rate=None, freq=None, loop=False, realtime=True, block_samples=65536):
        """path: the recording; fmt: its `Formats` name (from the metadata for SigMF); rate, freq: its sample rate and
        centre frequency (needed, unless the SigMF metadata has them: given, they override it); loop: start again at
        the end; realtime: deliver at the sample rate (False: as fast as it is read, nothing dropped)"""
        if path is None:
            raise ValueError("open('file', path=...): the recording to play")
        f = File(path, fmt, scale=True)
        if not f.fmt.is_complex:
            raise ValueError(f"{f.fmt.name} is a real format: only complex (I/Q) recordings can be played")
        rate = rate if rate is not None else f.rate
        if rate is None:
            raise ValueError(f"{path}: give the sample rate (rate=...); the recording doesn't say")
        self.file, self.loop, self.realtime = f, bool(loop), bool(realtime)
        self.block_samples = int(block_samples)
        self._rate = float(rate)
        self._label = freq                 # overrides the recording's frequencies
        self._freq = self._freq_at(0)
        self._eof = False
        self._Fs = self._rate
        f.open()
        dev = _samples.NullDevice()
        fmt32 = self.SAMPLE_FORMAT
        dev.set_format(fmt32.kind, fmt32.bits, fmt32.storage, fmt32.packed)
        self.device = dev
        self._rate_changed()
        return dev

    def _capabilities(self, caps):
        caps.rates = [Range(self._rate, self._rate)]
        caps.default_rate = self._rate
        if self._freq is not None:
            caps.default_freq = self._freq
        return caps

    def _close(self):
        if self.state["rx"]:
            self.stop_rx()
        self.device.close()
        self.file.close()

    # --- the recording --- #
    @property
    def eof(self):
        """Playback reached the end of the recording (and `loop` is off)"""
        return self._eof
    @property
    def position(self):
        """The sample of the recording delivered next"""
        return self.file.cur_samp if self.file is not None else 0
    @property
    def length(self):
        """Samples in the recording"""
        return self.file.max_samp if self.file is not None else 0

    def seek(self, sample):
        """Continue playback from `sample` of the recording (a jump in what is received, not a gap in the stream:
        `flush()` to drop what was queued before it)"""
        self._ensure_open("seek")
        if not 0 <= sample <= self.length:
            raise ValueError(f"seek({sample}) outside 0..{self.length}")
        with self._lock:
            self._seek_to = int(sample)
            self._eof = False
        if not self.state["rx"]:
            self._apply_seek(0)

    def _freq_at(self, sample):
        if self._label is not None or self.file.meta is None:
            return self._label
        return self.file.meta.freq_at(sample)

    def _apply_seek(self, total):
        with self._lock:
            if self._seek_to is None:
                return
            self.file.seek(self._seek_to)
            self._seek_to = None
        self._retuned(total, self._freq_at(self.position))

    def _retuned(self, total, freq):
        """The recording's frequency is `freq` from stream index `total` on"""
        if freq is not None and freq != self._freq:
            self._freq = freq
            self._retunes.append((total, freq))

    def _take_retunes(self):
        """On the reader's thread: the playback thread's frequency changes, as receive events"""
        while self._retunes:
            total, freq = self._retunes.popleft()
            self._tuning[("freq", "rx", 0)] = self._cf = freq
            if self.state["rx"]:
                self._rx_events.append((total, "freq", freq, 0))

    def _meta(self, *args, **kwargs):
        self._take_retunes()
        return super()._meta(*args, **kwargs)
    def read_available(self, *args, **kwargs):
        got = super().read_available(*args, **kwargs)
        self._take_retunes()   # after: the playback thread queues a retune before the samples it applies to
        return got
    def get_rx_freq(self, channel=0):
        self._take_retunes()
        return super().get_rx_freq(channel)

    def _next_block(self, total):
        """The samples to deliver next, up to the next capture (so a retune falls between blocks), or None at the end"""
        self._apply_seek(total)
        f = self.file
        if f.cur_samp >= f.max_samp:
            if not self.loop or f.max_samp == 0:
                self._eof = True
                return None
            f.seek(0)
            self._retuned(total, self._freq_at(0))
        n = min(self.block_samples, f.max_samp - f.cur_samp, max(1, self._queue_samples() // 2))
        meta = f.meta
        if meta is not None and self._label is None:
            later = [s for s, _ in meta.captures if s > f.cur_samp]
            if later:
                n = min(n, later[0] - f.cur_samp)
        self._retuned(total, self._freq_at(f.cur_samp))
        with self._lock:
            x = f.next(n)
        return np.ascontiguousarray(x, dtype=np.complex64)

    def _run(self):
        total = 0
        next_t = time.perf_counter()
        while not self._stop.is_set():
            x = self._next_block(total)
            if x is None:
                self._stop.wait(0.05)
                continue
            if not self.realtime:   # as fast as it is read: wait for room, rather than drop the oldest
                room = self._queue_samples() - len(x)
                while self.device.rx_stats()["pending"] > room and not self._stop.wait(0.001):
                    pass
            self.device._dispatch_rx(x.view(np.float32), None, 0)
            total += len(x)
            if self.realtime:
                next_t += len(x) / self._rate
                delay = next_t - time.perf_counter()
                if delay > 0:
                    self._stop.wait(delay)
                elif delay < -1.0:
                    next_t = time.perf_counter()   # far behind (a slow disk): don't burst to catch up

    def _queue_samples(self):
        size = int(type(self).BUFFER_SECONDS * self._Fs)
        return min(size, type(self).MAX_BUFFER_SAMPLES)

    def _start_rx(self):
        super()._start_rx()
        self._retunes.clear()
        if self._freq is not None:   # what the first block is at (the playback thread runs ahead of _cf)
            self._rx_now[("freq", 0)] = self._freq
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="pysdrlib-replay", daemon=True)
        self._thread.start()
    def _stop_rx(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None
        super()._stop_rx()

    def _set_rx_freq(self, freq, channel=0):
        """A label. Where the recording has frequencies (SigMF captures), its next capture changes it again"""
        meta = self.file.meta
        if self._label is not None or meta is None or meta.freq is None:
            self._label = freq
        self._freq = freq
    def _set_rx_sample_rate(self, Fs, channel=0):
        if not self.state["rx"]:
            self._rate_changed()
    def _set_rx_gain(self, gain, channel=0):
        return {}
    def _set_rx_channels(self, channels):
        pass
    def _set_tx_channels(self, channels):
        pass
