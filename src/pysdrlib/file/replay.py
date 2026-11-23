"""A recording played back as a receiving device, and a file that what is transmitted is recorded to:
`pysdrlib.open("file", path=..., tx_path=...)`"""
import collections
import threading
import time

import numpy as np

from ..base import convert
from ..base.caps import Capabilities, Range
from ..base.lib import samples as _samples
from ..base.native import NativeDevice
from .. import err
from .file import File
from .sink import FileSink

class Replay(NativeDevice):
    """A recording, received like a radio: the same `read`, `stream`, `RxMeta` and drop reporting, from a thread that
    delivers it in blocks at its sample rate (or as fast as it is read, `realtime=False`).

        sdr = pysdrlib.open("file", path="capture.sigmf")                          # SigMF: format, rate, frequency
        sdr = pysdrlib.open("file", path="capture.cfile", fmt="cf32", rate=2e6, freq=100e6, loop=True)
        sdr.initialize()            # the recording's rate and frequency
        sdr.start_rx()

    The frequency follows the recording: each SigMF capture is a retune (an `Event` in `RxMeta`, and `get_rx_freq`).
    `set_rx_freq` only relabels the stream (a recording can't be retuned), and the rate is the recording's. At the end,
    `loop` starts it again; otherwise delivery stops and `eof` is True (`read` then times out).

    With `tx_path` it also transmits, into a file: `start_tx` and `write` as on a radio, at the transmit rate when
    `realtime` (a write blocks while more than `TX_BUFFER_SECONDS` are ahead of the clock; written late, an underrun is
    counted), the transmit frequency recorded as SigMF captures. Without `path` it only transmits.

        sdr = pysdrlib.open("file", tx_path="sent.sigmf", rate=2e6)
        sdr.initialize(cf=915e6, Fs=2e6); sdr.start_tx(); sdr.write(samples)"""
    NAME = "File replay"
    CAPS = Capabilities(
        driver="",
        freq=[Range(0, 1e12)], default_freq=100_000_000,
        rates=[Range(1, 1e10)], default_rate=2_000_000,
        gains={"rx": {}, "tx": {}},
    )
    CAN_TRANSMIT = True   # with tx_path (`caps.channels["tx"]` says, as opened)
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
        self._next_samp = None         # the recording's sample after the last block delivered
        self._freq = None              # the recording's frequency at the playback position
        self._retunes = collections.deque()   # (stream index, freq) from the playback thread, for the reader's
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()   # the file, between the playback thread and seek
        self.sink: FileSink = None  # type: ignore   # what is transmitted goes here (tx_path)
        self._tx = {"written": 0, "underruns": 0, "t0": None, "sent0": 0}

    def _open(self, path=None, fmt=None, rate=None, freq=None, loop=False, realtime=True, block_samples=65536,
              tx_path=None, tx_format="cf32"):
        """path: the recording; fmt: its `Formats` name (from the metadata for SigMF); rate, freq: its sample rate and
        centre frequency (needed, unless the SigMF metadata has them: given, they override it); loop: start again at
        the end; realtime: deliver (and transmit) at the sample rate (False: as fast as it is read or written).
        tx_path, tx_format: the file what is transmitted is written to (SigMF for a .sigmf name), in this format"""
        if path is None and tx_path is None:
            raise ValueError("open('file', path=...): the recording to play (and/or tx_path=...: a file to transmit to)")
        f = None
        if path is not None:
            f = File(path, fmt, scale=True)
            if not f.fmt.is_complex:
                raise ValueError(f"{f.fmt.name} is a real format: only complex (I/Q) recordings can be played")
            rate = rate if rate is not None else f.rate
            if rate is None:
                raise ValueError(f"{path}: give the sample rate (rate=...); the recording doesn't say")
        self.file, self.loop, self.realtime = f, bool(loop), bool(realtime)
        self.block_samples = int(block_samples)
        self._rate = float(rate) if rate is not None else None   # the recording's: the only rate it plays at
        self._label = freq                 # overrides the recording's frequencies
        self._freq = self._freq_at(0) if f is not None else freq
        self._eof = False
        if self._rate is not None:
            self._Fs = self._rate
        if tx_path is not None:
            self.sink = FileSink(tx_path, tx_format, clip=True, scale=True, freq=freq,
                                 rate=self._rate, hw=type(self).NAME)
            self._tx = {"written": 0, "underruns": 0, "t0": None, "sent0": 0}
        if f is not None:
            f.open()
        if self.sink is not None:
            self.sink.open()
        dev = _samples.NullDevice()
        fmt32 = self.SAMPLE_FORMAT
        dev.set_format(fmt32.kind, fmt32.bits, fmt32.storage, fmt32.packed)
        self.device = dev
        self._rate_changed()
        return dev

    def _capabilities(self, caps):
        if self._rate is not None:
            caps.rates = [Range(self._rate, self._rate)]
            caps.default_rate = self._rate
        if self._freq is not None:
            caps.default_freq = self._freq
        caps.channels = {"rx": 1, "tx": 1 if self.sink is not None else 0}
        caps.shared = {**caps.shared, "freq": "direction", "rate": "direction"}   # a recording and a file: unrelated
        return caps

    def _close(self):
        if self.state["rx"]:
            self.stop_rx()
        if self.state["tx"]:
            self.stop_tx()
        self.device.close()
        if self.file is not None:
            self.file.close()
        if self.sink is not None:
            self.sink.close()
            self.sink = None

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
        self._need_recording("seek")
        if not 0 <= sample <= self.length:
            raise ValueError(f"seek({sample}) outside 0..{self.length}")
        with self._lock:
            self._seek_to = int(sample)
            self._eof = False
        if not self.state["rx"]:
            self._apply_seek(0)

    def _need_recording(self, what):
        if self.file is None:
            raise err.NotSupported(f"{what}: no recording to play (open('file', path=...)); this one only transmits")

    def _freq_at(self, sample):
        if self._label is not None or self.file is None or self.file.meta is None:
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
        if f.cur_samp != self._next_samp:   # the start, a loop or a seek: when the recording took this sample
            t0 = f.meta.start_time if f.meta is not None else None
            if t0 is not None:
                self.device.stamp_rx(t0 + f.cur_samp / self._rate, 0)
        with self._lock:
            x = f.next(n)
        self._next_samp = f.cur_samp
        return np.ascontiguousarray(x, dtype=np.complex64)

    def _run(self):
        total = 0
        next_t = time.perf_counter()
        while not self._stop.is_set():
            x = self._next_block(total)
            if x is None:
                self._stop.wait(0.05)
                continue
            if not self.realtime:   # as fast as it is read: wait for room, rather than lose any
                room = self._queue_samples() - len(x)
                while not self._stop.is_set():   # samples a read is still copying out hold their slots too
                    s = self.device.rx_stats()
                    if s["pending"] + s["reading"] <= room:
                        break
                    self._stop.wait(0.001)
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
        self._need_recording("start_rx")
        super()._start_rx()
        self._next_samp = None
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
        meta = self.file.meta if self.file is not None else None
        if self._label is not None or meta is None or meta.freq is None:
            self._label = freq
        self._freq = freq
    def _set_rx_sample_rate(self, Fs, channel=0):
        if not self.state["rx"]:
            self._rate_changed()
    def _set_rx_gain(self, gain, channel=0):
        return {}

    # --- transmitting, into tx_path --- #
    def _set_tx_freq(self, freq, channel=0):
        self.sink.retune(freq)
    def _set_tx_sample_rate(self, Fs, channel=0):
        if self.sink.count == 0 or self.sink.rate is None:
            self.sink.rate = Fs
        elif Fs != self.sink.rate:
            self.log.warning("transmit rate changed to %s after %d samples were written at %s", Fs, self.sink.count,
                             self.sink.rate)
    def _set_tx_gain(self, gain, channel=0):
        return {}

    def _tx_rate(self):
        return self.get_tx_sample_rate() or self.sink.rate or self._Fs

    def _start_tx(self):
        if self.sink.rate is None:
            self.sink.rate = self._tx_rate()
        self._tx.update(t0=None, sent0=self._tx["written"])
    def _stop_tx(self):
        self.sink.flush()
    def _tx_check(self):
        pass

    def _sent(self, now):
        """Samples the clock has played out since start_tx (all that was written, when not realtime)"""
        tx = self._tx
        if not self.realtime or tx["t0"] is None:
            return tx["written"]
        return min(tx["written"], tx["sent0"] + int((now - tx["t0"]) * self._tx_rate()))

    def _tx_write(self, samples, timeout, slot=0):
        tx = self._tx
        if self.realtime:
            now = time.perf_counter()   # (monotonic ticks every ~15.6 ms on Windows before Python 3.13)
            if tx["t0"] is None or self._sent(now) >= tx["written"]:   # the first, or written after it all went out
                if tx["t0"] is not None:
                    tx["underruns"] += 1
                tx.update(t0=now, sent0=tx["written"])
            ahead = tx["written"] + len(samples) - self._sent(now) - int(self.TX_BUFFER_SECONDS * self._tx_rate())
            if ahead > 0:   # wait for the clock (as a radio's queue makes write wait)
                wait = ahead / self._tx_rate()
                if 0 <= timeout < wait:
                    keep = max(0, len(samples) - int(ahead - timeout * self._tx_rate()))
                    time.sleep(timeout)
                    samples = samples[:keep]
                else:
                    time.sleep(wait)
        self.sink.write(samples)
        tx["written"] += len(samples)
        return len(samples)

    def _tx_stats(self, slot=0):
        tx = self._tx
        sent = self._sent(time.perf_counter())
        return {"written": tx["written"], "sent": sent, "underruns": tx["underruns"], "pending": tx["written"] - sent}
    def _set_rx_channels(self, channels):
        pass
    def _set_tx_channels(self, channels):
        pass
