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
                 transmit=False):
        """
        realtime: pace delivery to the sample rate. False delivers as fast as the host allows.
        block_samples: complex samples per callback.
        tone: tone frequency in Hz relative to center (default Fs / 8).
        sample_format: layout of the raw stream the "driver" delivers (convert.SampleFormat).
        channels: RX (and TX) channels, like a multi-channel radio: channel k is the same tone turned by k*90 degrees
        transmit: accept start_tx/write; what is written is kept per channel in `transmitted`
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
        self._stop = threading.Event()
        self._gain = {n.lower(): s.default for n, s in self.CAPS.gains["rx"].items()}
        self._make_block()

    # --- simulation interface --- #
    def set_tone(self, freq):
        """Tone frequency in Hz relative to center. Rounded so whole cycles fit in a block."""
        self._tone = freq
        self._make_block()
    def get_tone(self):
        return self._actual_tone

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
        for slot, ch in enumerate(self._rx_channels):
            self.device._dispatch_rx(self._raws[ch], None, slot)
        self.blocks += 1

    def _run(self):
        next_t = time.perf_counter()
        while not self._stop.is_set():
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

    def _open(self, *args, **kwargs):
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
        pass

    # Generic gain splits a total across the stages; the stages are the device-specific controls.
    def _set_rx_gain(self, gain, channel=0):
        rtr = {"if": None, "bb": None}
        stages = self.CAPS.gains["rx"]
        if gain == "default":
            if_gain, bb_gain = stages["IF"].default, stages["BB"].default
        else:
            if_gain = min(gain, stages["IF"].max)
            bb_gain = min(gain - if_gain, stages["BB"].max)
        rtr["if"] = self.set_rx_if_gain(if_gain)
        rtr["bb"] = self.set_rx_bb_gain(bb_gain)
        return rtr
    def _set_rx_if_gain(self, gain, channel=0):
        self._gain["if"] = gain
    def _set_rx_bb_gain(self, gain, channel=0):
        self._gain["bb"] = gain
