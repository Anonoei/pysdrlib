"""Simulated device.

A real, generic `Device` with no hardware. A "streaming thread" delivers raw int8 I/Q blocks
through the same path a driver uses (callback -> conversion -> RotatingBuffer -> get_samples),
so it can stand in for hardware in tests and benchmarks of pysdrlib's own layers.

    sdr = pysdrlib.device("sim")
    sdr.open(); sdr.initialize(cf=100e6, Fs=10e6)
    sdr.start_rx(); samples = sdr.get_samples()

The signal is a complex tone (see `set_tone`), phase-continuous across blocks. The raw stream can
use any `convert.SampleFormat` (8-bit, 12-bit, packed, float...) to emulate different hardware.
"""
import threading
import time
from types import SimpleNamespace

import numpy as np

from ..base.buffer import RotatingBuffer
from ..base import convert
from ..base.device import Device

from .config import ConfigSim as config

MAX_BUFFER_SAMPLES = 2**24

class Sim(Device):
    NAME = "Simulated"
    CONFIG = config
    CAN_TRANSMIT = False
    DUPLEX = False

    BLOCK_SAMPLES = 131072  # samples per delivery: one HackRF USB transfer (256 KiB)

    def __init__(self, realtime=True, block_samples=BLOCK_SAMPLES, tone=None, sample_format=convert.INT8):
        """
        realtime: pace delivery to the sample rate. False delivers as fast as the host allows.
        block_samples: complex samples per callback.
        tone: tone frequency in Hz relative to center (default Fs / 8).
        sample_format: layout of the raw stream the "driver" delivers (convert.SampleFormat).
        """
        super().__init__()
        self.SAMPLE_FORMAT = sample_format
        self.realtime = realtime
        self.block_samples = int(block_samples)
        self.buffer = RotatingBuffer()
        self.blocks = 0         # blocks delivered since start_rx / reset_stats
        self.max_lag = 0.0      # worst seconds the producer ran behind real time
        self._tone = tone
        self._raw = None
        self._thread = None
        self._stop = threading.Event()
        self._gain = {"if": config.DEFAULT_GAIN_RX_IF, "bb": config.DEFAULT_GAIN_RX_BB}
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
        self.buffer.clear_dropped()

    @property
    def dropped(self):
        """Samples discarded because the consumer fell behind"""
        return self.buffer.dropped

    # --- driver-style plumbing --- #
    def _make_block(self):
        n = self.block_samples
        tone = self._tone if self._tone is not None else self._Fs / 8
        cycles = (int(round(tone * n / self._Fs)) + n // 2) % n - n // 2  # wrap into [-n/2, n/2)
        self._actual_tone = cycles * self._Fs / n
        phase = 2j * np.pi * cycles * np.arange(n) / n
        self._raw = self.SAMPLE_FORMAT.from_complex64((0.5 * np.exp(phase)).astype(np.complex64))

    def _rx_callback(self, buffer, valid_length, buffer_length):
        """Same shape as a driver's callback (see hackrf.rx_callback)"""
        self.buffer.update(self.SAMPLE_FORMAT.to_complex64(buffer, valid_length))
        return 0

    def _deliver(self):
        self._rx_callback(self._raw, self._raw.nbytes, self._raw.nbytes)
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
        return SimpleNamespace(name="sim")
    def _close(self):
        if self.state["rx"]:
            self.stop_rx()
        self.device = None

    def _start_rx(self):
        self._resize_buffer()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="pysdrlib-sim", daemon=True)
        self._thread.start()
    def _stop_rx(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None
    def _get_samples(self):
        return self.buffer.samples

    # the generic read path (Device.read) on the ring the "driver" fills
    def _rx_wait(self, count, timeout):
        return self.buffer.wait(int(count), float(timeout))
    def _rx_read(self, count):
        return self.buffer.read_indexed(int(count))
    def _rx_position(self):
        return self.buffer.total, self.buffer.age
    def _rx_flush(self):
        return len(self.buffer.read())

    def _resize_buffer(self):
        self.buffer.reset(min(int(0.5 * self._Fs), MAX_BUFFER_SAMPLES))

    def _set_sample_rate(self, Fs):
        self._make_block()
        if not self.state["rx"]:
            self._resize_buffer()
    def _set_freq(self, freq):
        pass

    # Generic gain splits a total across the stages; the stages are the device-specific controls.
    def _set_rx_gain(self, gain):
        rtr = {"if": None, "bb": None}
        if gain == "default":
            if_gain, bb_gain = config.DEFAULT_GAIN_RX_IF, config.DEFAULT_GAIN_RX_BB
        else:
            if_gain = min(gain, config.GAIN_RX_IF_MAX)
            bb_gain = min(gain - if_gain, config.GAIN_RX_BB_MAX)
        rtr["if"] = self.set_rx_if_gain(if_gain)
        rtr["bb"] = self.set_rx_bb_gain(bb_gain)
        return rtr
    def set_rx_if_gain(self, gain=None):
        """IF gain stage (device-specific)"""
        return self._set_gain_stage("RX", "IF", gain, self._set_rx_if_gain)
    def set_rx_bb_gain(self, gain=None):
        """Baseband gain stage (device-specific)"""
        return self._set_gain_stage("RX", "BB", gain, self._set_rx_bb_gain)
    def _set_rx_if_gain(self, gain):
        self._gain["if"] = gain
    def _set_rx_bb_gain(self, gain):
        self._gain["bb"] = gain
