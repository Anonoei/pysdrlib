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
    def config(self):
        return self.device_class.CONFIG

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
    def set_rx_buffer(self, samples):
        self.calls.append(("set_rx_buffer", samples))
        self._ring.reset(samples)

    def reset_rx_stats(self):
        self.calls.append(("reset_rx_stats",))
        self._reset_counts()

    def read_rx(self, count=None):
        return self._ring.read(count)

    def read_rx_indexed(self, count):
        return self._ring.read_indexed(count)

    def wait_rx(self, samples, timeout):
        return self._ring.wait(samples, timeout)

    def rx_stats(self):
        return {"samples": self._samples, "total": self._ring.total, "dropped": self._ring.dropped, "pending": self._ring.available,
                "max_chunk": self._max_chunk, "calls": self._calls, "busy": 0.0,
                "age": None if self._last is None else self.clock() - self._last}

    def deliver(self, raw, valid=None):
        """The driver delivering one transfer of raw bytes (`valid` of them are good)"""
        samples = self.format.to_complex64(raw, valid)
        self._ring.update(samples)
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

RX888_RATES = [2e6, 4e6, 8e6, 16e6, 32e6]

SPECS = [
    Spec("hackrf", "HackRF", "hackrf",
         encode=lambda iq: (iq.view(np.float32) * 128).astype(np.int8),
         rate=10e6,
         closed_calls=(("start_rx",), ("stop_rx",), ("set_freq", 100_000_000), ("set_lna_gain", 8)),
         present=_hackrf_present, configure=_hackrf_configure),
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
