"""Simulated device.

A real `NativeDevice` with no hardware. A "streaming thread" hands raw int8 I/Q blocks to a compiled device with no
hardware behind it (`NullDevice`), which converts and queues them as a driver's own thread does, so everything from
there (the queue, the stream watchdog, drop reporting, `read`) is what a radio runs: it can stand in for hardware in
tests and benchmarks of pysdrlib's own layers.

    sdr = pysdrlib.device("sim")
    sdr.open(); sdr.initialize(cf=100e6, Fs=10e6)
    sdr.start_rx(); samples = sdr.get_samples()

The signal is a complex tone (see `set_tone`), phase-continuous across blocks. The raw stream can
use any `convert.SampleFormat` (8-bit, 12-bit, packed, float...) to emulate different hardware.

For testing what reads it, the signal can instead be built from parts, and the stream misbehave on demand:

    sdr = Sim(tones=[(100e3, -20), {"freq": 100.5e6, "level": -10}],   # (offset Hz, dBFS), or one fixed in RF
              noise=-60,                                               # dBFS of white noise
              bursts=[{"offset": -200e3, "level": -15, "period": 0.1, "duration": 0.01}],   # QPSK bursts
              gain_ref=32,          # the levels hold at 32 dB of gain; more scales them up, and the ADC clips at full scale
              retune_delay=0.05)    # a new frequency shows 50 ms of samples after set_rx_freq (stale samples in between)
    sdr.drop(10_000)                # lose samples, as a radio's overflow: readers see a gap
    sdr.stall(2.0); sdr.resume()    # stop delivering (for 2 s, or until resume()); what would have come is lost
    sdr.disconnect()                # unplugged: once what is queued is read, read raises err.Lost
"""
import threading
import time

import numpy as np

from ..base import convert
from ..base.caps import Capabilities, GainStage, Range
from ..base.lib import samples as _samples
from ..base.native import NativeDevice

class Sim(NativeDevice):
    NAME = "Simulated"
    CAPS = Capabilities(
        driver="",
        freq=[Range(1, 10_000_000_000)], default_freq=100_000_000,
        rates=[Range(1_000, 200_000_000)], default_rate=10_000_000,
        gains={"rx": {"IF": GainStage(1, 0, 40, 16), "BB": GainStage(2, 0, 62, 16)}, "tx": {}},
    )
    CAN_TRANSMIT = False
    DUPLEX = False

    SAMPLE_FORMAT = convert.INT8   # the default; `sample_format=` gives an instance its own
    BLOCK_SAMPLES = 131072  # samples per delivery: one HackRF USB transfer (256 KiB)
    MAX_BUFFER_SAMPLES = 2**24

    def __init__(self, realtime=True, block_samples=BLOCK_SAMPLES, tone=None, sample_format=convert.INT8, channels=1,
                 transmit=False, *, tones=None, noise=None, bursts=None, gain_ref=None, retune_delay=0.0, seed=None,
                 channel_db=None):
        """
        realtime: pace delivery to the sample rate. False delivers as fast as the host allows.
        block_samples: complex samples per callback.
        tone: tone frequency in Hz relative to center (default Fs / 8).
        sample_format: layout of the raw stream the "driver" delivers (convert.SampleFormat).
        channels: RX (and TX) channels, like a multi-channel radio: channel k is the same tone turned by k*90 degrees
        transmit: accept start_tx/write; what is written is kept per channel in `transmitted`
        tones: instead of the one tone, these: (offset Hz, level dBFS) each, or a dict with "offset" (from the center) or
            "freq" (fixed in RF: it moves when the radio retunes) and "level"
        noise: dBFS of complex white noise added (None: none)
        bursts: QPSK bursts, dicts of "offset" or "freq", "level", "period" and "duration" (seconds), "symbol_rate" (default Fs/16)
        gain_ref: total receive gain (dB) at which the levels are as given: each dB more scales the signal up one, and the
            ADC clips at full scale. None: the gain doesn't change the signal
        retune_delay: seconds of samples after `set_rx_freq` before the new frequency shows in the signal
        seed: of the noise and burst symbols
        channel_db: dB added to the built signal of each channel (a list, one per channel): channels of different strength
        """
        self.channels = int(channels)
        self.transmit = bool(transmit)
        self.transmitted = {ch: [] for ch in range(self.channels)}
        super().__init__()
        self.SAMPLE_FORMAT = sample_format
        self.realtime = realtime
        self.block_samples = int(block_samples)
        self.blocks = 0         # blocks delivered since start_rx / reset_stats
        self.max_lag = 0.0      # worst seconds the producer ran behind real time
        self._tone = tone
        self._raw = None
        self._thread = None
        self._stamp = False     # stamp the next block with the time (RxMeta.hw_time)
        self._stop = threading.Event()
        self._gains = [{n.lower(): s.default for n, s in self.CAPS.gains["rx"].items()} for _ in range(self.channels)]
        self.channel_db = [0.0] * self.channels
        self.configure(tones=tones, noise=noise, bursts=bursts, gain_ref=gain_ref, retune_delay=retune_delay, seed=seed,
                       channel_db=channel_db)
        self._produced = 0          # samples per channel delivered or lost since open
        self._sim_cf = None         # the frequency the signal is at (None: the device's)
        self._pending_cf = None     # (sample index, frequency): a retune not yet showing
        self._pending_drop = []     # (sample index, count): drops still to happen
        self._deliver_lock = threading.Lock()   # a block's delivery and its count are one step, as `drop` sees them
        self._stall_until = 0.0
        self.disconnected = False
        self._make_block()

    # --- simulation interface --- #
    SIGNAL = ("tone", "tones", "noise", "bursts", "gain_ref", "retune_delay", "seed", "channel_db")

    def configure(self, **signal):
        """Change the signal: any of the constructor's `tone`, `tones`, `noise`, `bursts`, `gain_ref`, `retune_delay`,
        `seed` and `channel_db` (also taken by `open`, so `pysdrlib.open("sim", tones=...)` works). Parts beyond
        +-Fs/2 of the frequency it is tuned to are not received, as a radio's filters would have it"""
        unknown = set(signal) - set(self.SIGNAL)
        if unknown:
            raise TypeError(f"Sim has no signal setting {', '.join(sorted(unknown))} (it has {', '.join(self.SIGNAL)})")
        if "tones" in signal:
            self.tones = [self._part(t) for t in (signal["tones"] or ())]
        if "noise" in signal:
            self.noise = signal["noise"]
        if "bursts" in signal:
            self.bursts = [self._part(b) for b in (signal["bursts"] or ())]
        if "gain_ref" in signal:
            self.gain_ref = signal["gain_ref"]
        if "retune_delay" in signal:
            self.retune_delay = float(signal["retune_delay"] or 0.0)
        if "seed" in signal or not hasattr(self, "_rng"):
            self._rng = np.random.default_rng(signal.get("seed"))
        if signal.get("channel_db") is not None:
            if len(signal["channel_db"]) != self.channels:
                raise ValueError(f"channel_db needs one value per channel ({self.channels})")
            self.channel_db = [float(v) for v in signal["channel_db"]]
        if "tone" in signal:
            self.set_tone(signal["tone"])

    def set_tone(self, freq):
        """Tone frequency in Hz relative to center. Rounded so whole cycles fit in a block."""
        self._tone = freq
        self._make_block()
    def get_tone(self):
        return self._actual_tone

    def drop(self, count, after=0):
        """Lose `count` samples (of every channel), as a radio's overflow: readers see a gap. after: once this many more
        samples are delivered (rounded up to a whole block), so a drop can land inside a read"""
        with self._deliver_lock:
            self._pending_drop.append((self._produced + int(after), int(count)))

    def stall(self, seconds=None):
        """Stop delivering, for `seconds` (None: until `resume`). The samples the radio would have delivered meanwhile
        are lost (a gap), as a real one overflows while nobody takes its transfers"""
        self._stall_until = float("inf") if seconds is None else time.monotonic() + float(seconds)

    def resume(self):
        """End a `stall`"""
        if not self.disconnected:
            self._stall_until = 0.0

    def disconnect(self):
        """As if the radio were unplugged: it stops delivering for good, and once what is queued has been read, `read`
        raises err.Lost"""
        self.disconnected = True
        self.stall()

    def _rx_lost(self):
        return "disconnected (Sim.disconnect): close it, and open it again" if self.disconnected else None

    @property
    def stalled(self):
        return time.monotonic() < self._stall_until

    def pump(self, blocks=1):
        """Deliver `blocks` blocks synchronously from the calling thread, not paced.
        For deterministic tests/benchmarks; not allowed while streaming."""
        if self.state["rx"]:
            raise RuntimeError("pump() can't be used while streaming")
        self._ensure_open("pump")
        for _ in range(blocks):
            self._deliver()

    def reset_stats(self):
        self.blocks = 0
        self.max_lag = 0.0
        self._dropped_reported = 0
        self.device.reset_rx_stats()

    @property
    def dropped(self):
        """Samples discarded because the consumer fell behind"""
        return self.device.rx_stats()["dropped"]

    # --- driver-style plumbing --- #
    @staticmethod
    def _part(spec):
        if isinstance(spec, dict):
            part = dict(spec)
        else:
            offset, level = spec
            part = {"offset": offset, "level": level}
        if ("offset" in part) == ("freq" in part):
            raise ValueError(f"a sim signal part needs one of 'offset' or 'freq': {spec!r}")
        part.setdefault("level", -6.0)
        return part

    @property
    def _synthetic(self):
        return bool(self.tones or self.bursts or self.noise is not None or self.gain_ref is not None
                    or any(self.channel_db))

    def _center(self, index):
        """The frequency the signal is at, at sample `index` (a delayed retune switches there)"""
        pending = self._pending_cf
        if pending is not None and index >= pending[0]:
            self._sim_cf, self._pending_cf = pending[1], None
        return self._sim_cf if self._sim_cf is not None else self._cf

    def _offset(self, part, cf):
        return part["offset"] if "offset" in part else part["freq"] - cf

    def _in_band(self, part, cf, fs):
        """Within +-Fs/2 of the tuning: beyond, a radio's filters remove it (it would alias in otherwise)"""
        return abs(self._offset(part, cf)) < fs / 2

    @property
    def _gain(self):   # channel 0's stages
        return self._gains[0]

    def _received(self, x, ch):
        """Channel `ch`'s samples of the built signal `x`: turned by ch*90 degrees, scaled by its gain, clipped as an ADC
        would"""
        scale = 10 ** (self.channel_db[ch] / 20) * np.exp(0.5j * np.pi * ch)
        if self.gain_ref is not None:
            scale *= 10 ** ((sum(self._gains[ch].values()) - self.gain_ref) / 20)
        out = x * scale
        return (np.clip(out.real, -1, 1) + 1j * np.clip(out.imag, -1, 1)).astype(np.complex64)

    def _synth(self, first, n):
        """`n` samples of the built signal from sample `first`, before gain and the ADC"""
        pending = self._pending_cf
        if pending is not None and first < pending[0] < first + n:   # a retune lands inside: two pieces
            cut = pending[0] - first
            return np.concatenate((self._synth(first, cut), self._synth(first + cut, n - cut)))
        cf, fs = self._center(first), self._Fs
        idx = first + np.arange(n, dtype=np.float64)
        out = np.zeros(n, dtype=np.complex128)
        tones = self.tones or ([] if self.bursts or self.noise is not None else
                               [{"offset": self._tone if self._tone is not None else fs / 8, "level": 20 * np.log10(0.5)}])
        for t in tones:
            if not self._in_band(t, cf, fs):
                continue
            out += 10 ** (t["level"] / 20) * np.exp(2j * np.pi * ((self._offset(t, cf) / fs * idx) % 1.0))
        for b in self.bursts:
            if not self._in_band(b, cf, fs):
                continue
            period, duration = max(1, int(b["period"] * fs)), int(b["duration"] * fs)
            on = (idx % period) < duration
            if not on.any():
                continue
            sps = max(1, int(fs / b.get("symbol_rate", fs / 16)))
            sym = (idx // sps).astype(np.int64)
            sym -= sym[0]
            qpsk = np.exp(1j * (np.pi / 4 + np.pi / 2 * self._rng.integers(0, 4, sym[-1] + 1)))[sym]
            carrier = np.exp(2j * np.pi * ((self._offset(b, cf) / fs * idx) % 1.0))
            out += on * 10 ** (b["level"] / 20) * qpsk * carrier
        if self.noise is not None:
            sigma = np.sqrt(10 ** (self.noise / 10) / 2)
            out += sigma * (self._rng.standard_normal(n) + 1j * self._rng.standard_normal(n))
        return out

    def _lose(self, count):
        if count > 0:
            for slot in range(len(self._rx_channels)):
                self.device._skip_rx(int(count), slot)
            self._produced += int(count)

    def _make_block(self):
        n = self.block_samples
        tone = self._tone if self._tone is not None else self._Fs / 8
        cycles = (int(round(tone * n / self._Fs)) + n // 2) % n - n // 2  # wrap into [-n/2, n/2)
        self._actual_tone = cycles * self._Fs / n
        phase = 2j * np.pi * cycles * np.arange(n) / n
        self._raws = [self.SAMPLE_FORMAT.from_complex64((0.5 * np.exp(phase + 0.5j * np.pi * ch)).astype(np.complex64))
                      for ch in range(self.channels)]
        self._raw = self._raws[0]

    def _deliver(self):
        """What a driver's streaming thread does with a transfer: every channel's block, each to its slot"""
        with self._deliver_lock:
            self._deliver_block()

    def _deliver_block(self):
        if self._stamp:   # a radio with a clock (as a USRP): its time of the stream's first sample
            self._stamp = False
            now = time.time()
            for slot in range(len(self._rx_channels)):
                self.device.stamp_rx(now, slot)
        if self._pending_drop:
            due = [d for d in self._pending_drop if d[0] <= self._produced]
            self._pending_drop = [d for d in self._pending_drop if d[0] > self._produced]
            self._lose(sum(n for _, n in due))
        if self._synthetic:
            x = self._synth(self._produced, self.block_samples)
            raws = {ch: self.SAMPLE_FORMAT.from_complex64(self._received(x, ch)) for ch in set(self._rx_channels)}
        else:
            raws = self._raws
        for slot, ch in enumerate(self._rx_channels):
            self.device._dispatch_rx(raws[ch], None, slot)
        self._produced += self.block_samples
        self.blocks += 1

    def _run(self):
        next_t = time.perf_counter()
        while not self._stop.is_set():
            if self.stalled:
                began = time.perf_counter()
                while self.stalled and not self._stop.is_set():
                    self._stop.wait(min(0.01, max(0.0, self._stall_until - time.monotonic())))
                if self.realtime:   # what the radio produced meanwhile overflowed it
                    with self._deliver_lock:
                        self._lose(int((time.perf_counter() - began) * self._Fs))
                next_t = time.perf_counter()
                continue
            self._deliver()
            if self.realtime:
                next_t += self.block_samples / self._Fs
                delay = next_t - time.perf_counter()
                if delay > 0:
                    self._stop.wait(delay)
                else:
                    self.max_lag = max(self.max_lag, -delay)
            else:
                time.sleep(0)  # let the consumer thread run

    def _open(self, serial_number=None, index=None, **signal):
        """signal: as `configure`'s (anything else raises TypeError). serial_number and index pick a unit on a radio: a
        sim is any unit asked for"""
        self.configure(**signal)
        dev = _samples.NullDevice()
        fmt = self.SAMPLE_FORMAT
        dev.set_format(fmt.kind, fmt.bits, fmt.storage, fmt.packed)
        self.device = dev
        self._rate_changed()   # a queue from the start, for pump()
        return dev
    def _close(self):
        if self.state["rx"]:
            self.stop_rx()
        self.device.close()

    def _start_rx(self):
        super()._start_rx()
        self._stamp = True
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="pysdrlib-sim", daemon=True)
        self._thread.start()
    def _stop_rx(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None
        super()._stop_rx()

    def _capabilities(self, caps):
        caps.channels = {"rx": self.channels, "tx": self.channels if self.transmit else 0}
        return caps
    def _set_rx_channels(self, channels):
        if self.state["rx"]:
            raise RuntimeError("stop_rx() before changing the channels")
    def _set_tx_channels(self, channels):
        pass

    # the transmit side: a sink that keeps what was written
    def _start_tx(self):
        pass
    def _stop_tx(self):
        pass
    def _tx_check(self):
        pass
    def _tx_write(self, samples, timeout, slot=0):
        self.transmitted[self._tx_channels[slot]].append(np.array(samples, dtype=np.complex64))
        return len(samples)
    def _tx_stats(self, slot=0):
        written = sum(len(x) for x in self.transmitted[self._tx_channels[slot]])
        return {"written": written, "sent": written, "underruns": 0, "pending": 0}

    def _set_rx_sample_rate(self, Fs, channel=0):
        self._make_block()
        if not self.state["rx"]:
            self._rate_changed()
    def _set_rx_freq(self, freq, channel=0):
        if getattr(self, "retune_delay", 0) > 0 and self._sim_cf is not None:
            self._pending_cf = (self._produced + int(self.retune_delay * self._Fs), float(freq))
        else:
            self._sim_cf, self._pending_cf = float(freq), None

    # Generic gain splits a total across the stages; the stages are the device-specific controls.
    def _set_rx_gain(self, gain, channel=0):
        rtr = {"if": None, "bb": None}
        stages = self.CAPS.gains["rx"]
        if gain == "default":
            if_gain, bb_gain = stages["IF"].default, stages["BB"].default
        else:
            if_gain = min(gain, stages["IF"].max)
            bb_gain = min(gain - if_gain, stages["BB"].max)
        rtr["if"] = self.set_rx_if_gain(if_gain, channel=channel)
        rtr["bb"] = self.set_rx_bb_gain(bb_gain, channel=channel)
        return rtr
    def _set_rx_if_gain(self, gain, channel=0):
        self._gains[channel]["if"] = gain
    def _set_rx_bb_gain(self, gain, channel=0):
        self._gains[channel]["bb"] = gain
