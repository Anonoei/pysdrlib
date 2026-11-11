"""The hardware devices pysdrlib drives through compiled extensions, described once for the contract tests.

Adding a device: add a `Spec` to SPECS. Every `test_*_contract.py` then runs against it.
"""
import importlib
import os
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Callable

import numpy as np
import pytest

from pysdrlib.base.buffer import RotatingBuffer

HW = os.environ.get("PYSDRLIB_HW") == "1"

# One I/Q pair per row, as every device's SAMPLE_FORMAT should convert them
SAMPLES = np.array([0.5 - 0.5j, 0 + 0.25j], dtype=np.complex64)


@dataclass
class Spec:
    name: str                       # package name: pysdrlib.<name>
    cls: str                        # device class, in pysdrlib.<name>.<module>
    module: str                     # module of the class
    encode: Callable                # complex64 SAMPLES -> raw interleaved I/Q in the device's native dtype
    rate: float                     # a sample rate the device supports exactly
    fake_returns: dict = field(default_factory=dict)   # what the fake driver returns, by method
    closed_calls: tuple = ()        # (name, args) driver calls that must raise on a closed device
    present: Callable = None        # native module -> number of attached devices
    configure: Callable = None      # native device -> None: tune it so it streams
    atol: float = 0                 # how far converted SAMPLES may be from the originals (quantization)
    freq_call: str = "set_freq"             # the driver call that tunes the receiver (its first argument is the frequency)
    rate_call: str = "set_sample_rate"      # ... and sets its sample rate

    def freqs(self, sdr):
        """The frequencies the driver was asked for, in order"""
        return [args[0] for args in sdr.device.of(self.freq_call)]

    def rates(self, sdr):
        return [args[0] for args in sdr.device.of(self.rate_call)]

    def _import(self, name):
        try:
            return importlib.import_module(name)
        except ImportError as exc:
            pytest.skip(f"{self.name} unavailable: {exc}")

    @property
    def package(self):
        return self._import(f"pysdrlib.{self.name}")

    @property
    def device_module(self):
        self.package
        return self._import(f"pysdrlib.{self.name}.{self.module}")

    @property
    def device_class(self):
        return getattr(self.device_module, self.cls)

    @property
    def caps(self):
        """The device class's declared capabilities (`CAPS`)"""
        return self.device_class.CAPS

    @property
    def lib(self):
        """pysdrlib.<name>.lib: the compiled extension (`.<name>`) and its errors (`.err`)"""
        self.package
        return self._import(f"pysdrlib.{self.name}.lib")

    @property
    def native(self):
        return getattr(self.lib, self.name)

    @property
    def ext(self):
        """pysdrlib._ext.<name>: how its vendor library is built and loaded"""
        return importlib.import_module(f"pysdrlib._ext.{self.name}")

    def raw(self):
        """(bytes, ndarray) of SAMPLES as the driver delivers them"""
        array = self.encode(SAMPLES)
        return array.tobytes(), array


class FakeDevice:
    """Stands in for the extension's device: records every call, returns what the spec says.

    Receiving works like the extension's buffered path (`set_rx_buffer`, `read_rx`, `rx_stats`), with the
    driver's part played by `deliver`: the test hands it raw bytes as a USB transfer would. The conversion
    and ring are pysdrlib's Python ones here; the real C++ ones are tested in test_native_contract.py.
    """
    def __init__(self, returns, fmt=None):
        self.calls = []
        self.rx_callback = None
        self._returns = returns
        self.format = fmt
        self.clock = time.monotonic        # a test may replace it, with the watchdog's
        self._ring = RotatingBuffer()
        self._rings = [self._ring]          # one per slot (channel of a multi-channel stream), fed together
        self._reset_counts()

    def _reset_counts(self):
        self._ring._dropped = 0
        self._samples = self._max_chunk = self._calls = 0
        self._last = None

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        def call(*args):
            self.calls.append((name, *args))
            result = self._returns.get(name)
            return result(*args) if callable(result) else result
        return call

    def set_rx_callback(self, fn):
        self.rx_callback = fn
        self.calls.append(("set_rx_callback",))

    # --- the buffered receive path --- #
    def set_rx_buffer(self, samples, slots=1):
        self.calls.append(("set_rx_buffer", samples) if slots == 1 else ("set_rx_buffer", samples, slots))
        self._rings = [self._ring] + [RotatingBuffer() for _ in range(slots - 1)]
        for ring in self._rings:
            ring.reset(samples)

    def reset_rx_stats(self):
        self.calls.append(("reset_rx_stats",))
        self._reset_counts()

    def read_rx(self, count=None, slot=0):
        return self._rings[slot].read(count)

    def read_rx_indexed(self, count, slot=0):
        return self._rings[slot].read_indexed(count)

    def wait_rx(self, samples, timeout, slot=0):
        return self._rings[slot].wait(samples, timeout)

    def read_rx_into(self, out, timeout, slot=0):
        ring = self._rings[slot]
        ring.wait(len(out), timeout)
        x, first = ring.read_indexed(len(out))
        out[:len(x)] = x
        return (len(x), first, *self.rx_counters(slot))

    def rx_counters(self, slot=0):
        s = self.rx_stats(slot)
        return (s["samples"], s["total"], s["dropped"], s["max_chunk"], -1.0 if s["age"] is None else s["age"])

    def rx_stats(self, slot=0):
        ring = self._rings[slot]
        return {"samples": self._samples, "total": ring.total, "dropped": ring.dropped, "pending": ring.available,
                "max_chunk": self._max_chunk, "calls": self._calls, "busy": 0.0,
                "age": None if self._last is None else self.clock() - self._last}

    def deliver(self, raw, valid=None, slot=None):
        """The driver delivering one transfer of raw bytes (`valid` of them are good), to one slot or (None) to every
        slot, as a multi-channel driver does (each channel's own samples: here the same ones)"""
        samples = self.format.to_complex64(raw, valid)
        for k, ring in enumerate(self._rings):
            if slot is None or k == slot:
                ring.update(samples)
        self._samples += len(samples)
        self._max_chunk = max(self._max_chunk, len(samples))
        self._calls += 1
        self._last = self.clock()

    def names(self):
        return [c[0] for c in self.calls]

    def of(self, name):
        """The arguments of each call to `name`"""
        return [c[1:] for c in self.calls if c[0] == name]


def fake_lib(spec, open_error=None):
    """A replacement for pysdrlib.<name>.<module>.lib whose open() gives FakeDevices"""
    def open_(*args):
        if open_error:
            raise open_error
        return FakeDevice(spec.fake_returns, spec.device_class.SAMPLE_FORMAT)
    native = SimpleNamespace(open=open_, open_by_serial=open_, init=lambda: None)
    return SimpleNamespace(**{spec.name: native, "err": spec.lib.err})


def _hackrf_present(native):
    return len(native.device_list())

def _hackrf_configure(device):
    device.set_sample_rate(10_000_000)
    device.set_freq(100_000_000)

def _rtl_sdr_configure(device):
    device.set_sample_rate(2_400_000)
    device.set_freq(100_000_000)

def _extio_configure(device):
    device.set_freq(7_100_000)
    device.set_sample_rate(8e6)

def _uhd_present(native):
    # A B2xx that lost its firmware (replugged) is only found once libuhd can load it, and a libuhd pysdrlib built
    # has no images of its own: point it at some before looking
    from pysdrlib.uhd import images
    images.configure()
    return len(native.find())

def _uhd_configure(device):
    device.set_rx_rate(10e6, 0)
    device.set_rx_freq(100_000_000, 0)

_uhd_rate = {"value": 10e6}   # the rate the fake radio is running at

UHD_FAKE = {
    "set_rx_rate": lambda rate, c=0: _uhd_rate.update(value=rate),
    "get_rx_rate": lambda c=0: _uhd_rate["value"], "get_master_clock_rate": lambda: 40e6,
    "num_rx_channels": lambda: 2, "num_tx_channels": lambda: 2,
    "rx_bandwidth_range": lambda c=0: (200e3, 56e6), "tx_bandwidth_range": lambda c=0: (200e3, 56e6),
    "set_rx_bandwidth": lambda bw, c=0: bw, "set_tx_bandwidth": lambda bw, c=0: bw,
    "rx_antennas": lambda c=0: ["TX/RX", "RX2"], "tx_antennas": lambda c=0: ["TX/RX"],
    "clock_sources": lambda: ["internal", "external", "gpsdo"], "info": lambda: {"mboard_serial": "ABC123"},
    "get_time_source": lambda: "internal", "mboard_sensors": lambda: {}, "rx_sensors": lambda c=0: {}, "tx_sensors": lambda c=0: {},
}

RX888_RATES = [2e6, 4e6, 8e6, 16e6, 32e6]

SPECS = [
    Spec("hackrf", "HackRF", "hackrf",
         encode=lambda iq: (iq.view(np.float32) * 128).astype(np.int8),
         rate=10e6,
         closed_calls=(("start_rx",), ("stop_rx",), ("set_freq", 100_000_000), ("set_lna_gain", 8)),
         present=_hackrf_present, configure=_hackrf_configure),
    Spec("uhd", "B210", "uhd",
         encode=lambda iq: (iq.view(np.float32) * 32768).clip(-32768, 32767).astype(np.int16),
         rate=10e6, atol=1 / 32768, freq_call="set_rx_freq", rate_call="set_rx_rate",
         fake_returns=UHD_FAKE,
         closed_calls=(("start_rx",), ("stop_rx",), ("set_rx_freq", 100_000_000, 0), ("set_rx_gain", 10.0, 0),
                       ("set_rx_agc", True, 0)),
         present=_uhd_present, configure=_uhd_configure),
    Spec("extio", "RX888", "rx888",
         encode=lambda iq: iq.view(np.float32),
         rate=8e6,
         fake_returns={"sample_rates": lambda: RX888_RATES,
                       "set_sample_rate": lambda v: min(RX888_RATES, key=lambda r: abs(r - v))},
         closed_calls=(("start_rx",), ("stop_rx",), ("set_freq", 7_100_000),
                       ("set_sample_rate", 8e6), ("set_rx_rf_gain", 0.0), ("set_bias_t", False)),
         present=lambda native: native.device_count(), configure=_extio_configure),
    Spec("rtl_sdr", "RTLSDR", "rtlsdr",
         encode=lambda iq: np.rint(iq.view(np.float32) * 127.5 + 127.5).astype(np.uint8),   # offset binary
         rate=2.4e6, atol=1 / 127.5,
         fake_returns={"set_sample_rate": lambda v: v},
         closed_calls=(("start_rx",), ("stop_rx",), ("set_freq", 100_000_000), ("set_sample_rate", 2_400_000),
                       ("set_rx_rf_gain", 30.0), ("set_agc", True), ("set_bias_t", False), ("gains",)),
         present=lambda native: native.device_count(), configure=_rtl_sdr_configure),
]

BY_NAME = {spec.name: spec for spec in SPECS}
