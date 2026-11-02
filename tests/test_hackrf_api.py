"""Hardware-free tests for pysdrlib.hackrf.HackRF.

libhackrf is replaced by a fake `lib` object so these pin down the Python-side
behaviour independent of the backend (Cython or pybind11). The same tests must
pass on both.
"""
import os
import types

import numpy as np
import pytest

from pysdrlib import err
from pysdrlib.hackrf import hackrf as hackrf_mod
from pysdrlib.hackrf.config import ConfigHackRF as config
from pysdrlib.hackrf.lib import err as lib_err


class FakeDevice:
    def __init__(self):
        self.calls = []
        self.rx_callback = None

    def _rec(self, name, *args):
        self.calls.append((name, *args))

    def close(self): self._rec("close")
    def set_rx_callback(self, fn):
        self.rx_callback = fn
        self._rec("set_rx_callback")
    def start_rx(self): self._rec("start_rx")
    def stop_rx(self): self._rec("stop_rx")
    def set_freq(self, v): self._rec("set_freq", v)
    def set_sample_rate(self, v): self._rec("set_sample_rate", v)
    def set_lna_gain(self, v): self._rec("set_lna_gain", v)
    def set_vga_gain(self, v): self._rec("set_vga_gain", v)
    def set_txvga_gain(self, v): self._rec("set_txvga_gain", v)
    def set_amp_enable(self, v): self._rec("set_amp_enable", v)
    def set_antenna_enable(self, v): self._rec("set_antenna_enable", v)

    def names(self):
        return [c[0] for c in self.calls]


class FakeHackrfModule:
    """Stands in for `lib.hackrf`."""
    device = FakeDevice
    open_error = None
    last_open = None

    @classmethod
    def init(cls): pass

    @classmethod
    def open(cls):
        if cls.open_error:
            raise cls.open_error
        cls.last_open = ("open",)
        return FakeDevice()

    @classmethod
    def open_by_serial(cls, serial):
        if cls.open_error:
            raise cls.open_error
        cls.last_open = ("open_by_serial", serial)
        return FakeDevice()


@pytest.fixture
def fake_lib(monkeypatch):
    FakeHackrfModule.open_error = None
    FakeHackrfModule.last_open = None
    fake = types.SimpleNamespace(hackrf=FakeHackrfModule, err=lib_err)
    monkeypatch.setattr(hackrf_mod, "lib", fake)
    return fake


@pytest.fixture
def sdr(fake_lib):
    dev = hackrf_mod.HackRF()
    dev.open()
    yield dev
    dev.device.calls.clear()


def last(sdr, name):
    return [c for c in sdr.device.calls if c[0] == name]


# --- open / close --------------------------------------------------------- #
class TestOpenClose:
    def test_open_default(self, fake_lib):
        d = hackrf_mod.HackRF()
        d.open()
        assert d.state["open"]
        assert FakeHackrfModule.last_open == ("open",)

    def test_open_by_serial(self, fake_lib):
        d = hackrf_mod.HackRF()
        d.open(serial_number="abc123")
        assert FakeHackrfModule.last_open == ("open_by_serial", "abc123")

    def test_not_found_maps_to_nodevice(self, fake_lib):
        FakeHackrfModule.open_error = lib_err.NOT_FOUND()
        d = hackrf_mod.HackRF()
        with pytest.raises(err.NoDevice):
            d.open()
        assert not d.state["open"]

    def test_close(self, sdr):
        sdr.close()
        assert not sdr.state["open"]
        assert ("close",) in sdr.device.calls

    def test_context_manager(self, fake_lib):
        with hackrf_mod.HackRF() as d:
            assert d.state["open"]
        assert not d.state["open"]

    def test_requires_open(self, fake_lib):
        d = hackrf_mod.HackRF()
        with pytest.raises(err.NotOpen):
            d.set_freq(100e6)


# --- tuning ---------------------------------------------------------------- #
class TestTuning:
    def test_set_freq(self, sdr):
        assert sdr.set_freq(100e6) == 100e6
        assert last(sdr, "set_freq") == [("set_freq", 100_000_000)]
        assert isinstance(last(sdr, "set_freq")[0][1], int)

    def test_freq_clamped(self, sdr):
        with pytest.warns(Warning):
            sdr.set_freq(config.FREQ_MAX * 2)
        assert last(sdr, "set_freq")[-1][1] == config.FREQ_MAX

    def test_set_sample_rate_resizes_buffer(self, sdr):
        sdr.set_sample_rate(10e6)
        assert last(sdr, "set_sample_rate") == [("set_sample_rate", 10_000_000)]
        assert hackrf_mod.cb._N == int(0.5 * 10e6)

    def test_sample_rate_clamped(self, sdr):
        with pytest.warns(Warning):
            sdr.set_sample_rate(1)
        assert last(sdr, "set_sample_rate")[-1][1] == config.SAMPLE_RATE_MIN

    def test_defaults_when_none(self, sdr):
        sdr.set_freq()
        sdr.set_sample_rate()
        assert last(sdr, "set_freq")[-1][1] == config.DEFAULT_FREQ
        assert last(sdr, "set_sample_rate")[-1][1] == config.DEFAULT_SAMPLE_RATE


# --- device-specific gain setters (backend contract) ----------------------- #
class TestSpecificGainBackend:
    """The `_set_*` hooks are the contract between HackRF and the backend."""

    def test_rx_rf_enables_amp(self, sdr):
        sdr._set_rx_rf_gain(11)
        sdr._set_rx_rf_gain(0)
        assert last(sdr, "set_amp_enable") == [("set_amp_enable", True), ("set_amp_enable", False)]

    def test_rx_if_is_lna(self, sdr):
        sdr._set_rx_if_gain(24)
        assert last(sdr, "set_lna_gain") == [("set_lna_gain", 24)]

    def test_rx_bb_is_vga(self, sdr):
        sdr._set_rx_bb_gain(30)
        assert last(sdr, "set_vga_gain") == [("set_vga_gain", 30)]

    def test_tx_rf_enables_amp(self, sdr):
        sdr._set_tx_rf_gain(11)
        assert last(sdr, "set_amp_enable") == [("set_amp_enable", True)]

    def test_tx_if_is_txvga(self, sdr):
        sdr._set_tx_if_gain(20)
        assert last(sdr, "set_txvga_gain") == [("set_txvga_gain", 20)]

    def test_bias_t(self, sdr):
        sdr.set_bias_t(True)
        assert last(sdr, "set_antenna_enable") == [("set_antenna_enable", True)]


# --- public device-specific setters ---------------------------------------- #
# Device.set_{rx,tx}_{if,bb}_gain in base/device.py currently dispatch to
# `_set_rx_rf_gain` (copy/paste bug), so power-user setters hit the wrong
# libhackrf call. These tests describe the intended behaviour.
class TestSpecificGainPublic:
    def test_rx_rf(self, sdr):
        assert sdr.set_rx_rf_gain(11) == 11
        assert last(sdr, "set_amp_enable") == [("set_amp_enable", True)]


    def test_rx_if(self, sdr):
        sdr.set_rx_if_gain(24)
        assert last(sdr, "set_lna_gain") == [("set_lna_gain", 24)]


    def test_rx_bb(self, sdr):
        sdr.set_rx_bb_gain(30)
        assert last(sdr, "set_vga_gain") == [("set_vga_gain", 30)]

    def test_tx_rf(self, sdr):
        sdr.set_tx_rf_gain(11)
        assert last(sdr, "set_amp_enable") == [("set_amp_enable", True)]


    def test_tx_if(self, sdr):
        sdr.set_tx_if_gain(20)
        assert last(sdr, "set_txvga_gain") == [("set_txvga_gain", 20)]


# --- generic gain ---------------------------------------------------------- #
class TestGenericGain:
    def test_rx_gain_returns_all_stages(self, sdr):
        out = sdr.set_rx_gain(40)
        assert set(out) == {"rf", "if", "bb"}

    def test_rx_gain_default_returns_stages(self, sdr):
        out = sdr.set_rx_gain()
        assert set(out) == {"rf", "if", "bb"}

    def test_tx_gain_uses_amp_only_when_txvga_cannot_supply_it(self, sdr):
        sdr.set_tx_gain(45)   # TXVGA reaches 47 dB on its own: no amp
        assert not last(sdr, "set_amp_enable")   # amp left alone
        assert last(sdr, "set_txvga_gain")[-1] == ("set_txvga_gain", 45)
        sdr.device.calls.clear()
        sdr.set_tx_gain(58)   # max: needs the 11 dB amp plus 47 dB TXVGA
        assert last(sdr, "set_amp_enable") == [("set_amp_enable", True)]
        assert last(sdr, "set_txvga_gain") == [("set_txvga_gain", 47)]

    def test_tx_gain_returns_stages(self, sdr):
        assert set(sdr.set_tx_gain()) == {"rf", "if"}


    def test_rx_gain_default_programs_lna_and_vga(self, sdr):
        sdr.set_rx_gain()
        assert last(sdr, "set_lna_gain") == [("set_lna_gain", config.DEFAULT_GAIN_RX_IF)]
        assert last(sdr, "set_vga_gain") == [("set_vga_gain", config.DEFAULT_GAIN_RX_BB)]


# --- rx path --------------------------------------------------------------- #
class TestRx:
    def test_start_stop(self, sdr):
        sdr.start_rx()
        assert sdr.state["rx"]
        assert sdr.device.rx_callback is hackrf_mod.rx_callback
        # callback must be installed before streaming begins
        names = sdr.device.names()
        assert names.index("set_rx_callback") < names.index("start_rx")
        sdr.stop_rx()
        assert not sdr.state["rx"]
        assert "stop_rx" in sdr.device.names()

    def test_rx_callback_conversion(self):
        hackrf_mod.cb.reset(16)
        raw = np.array([127, -128, 0, 64, -64, 0, 32, -32], dtype=np.int8)
        rtr = hackrf_mod.rx_callback(raw, len(raw), len(raw))
        assert rtr == 0
        s = hackrf_mod.cb.samples
        assert s.dtype == np.complex64  # every device returns complex64, no complex128 intermediate
        expected = np.array([127 - 128j, 0 + 64j, -64 + 0j, 32 - 32j]) / 128
        np.testing.assert_allclose(s[-4:], expected)

    def test_rx_callback_respects_valid_length(self):
        hackrf_mod.cb.reset(16)
        raw = np.arange(8, dtype=np.int8)
        hackrf_mod.rx_callback(raw, 4, 8)
        s = hackrf_mod.cb.samples  # reading consumes the buffer; read once
        np.testing.assert_allclose(s, np.array([0 + 1j, 2 + 3j]) / 128)


# --- config ---------------------------------------------------------------- #
class TestConfig:
    def test_limits(self):
        assert config.FREQ_MIN == 1_000_000
        assert config.FREQ_MAX == 6_000_000_000
        assert config.SAMPLE_RATE_MIN == 2_000_000
        assert config.SAMPLE_RATE_MAX == 20_000_000

    @pytest.mark.parametrize("name,step,lo,hi", [
        ("RX_RF", 11, 0, 11), ("RX_IF", 8, 0, 40), ("RX_BB", 2, 0, 62),
        ("TX_RF", 11, 0, 11), ("TX_IF", 1, 0, 47),
    ])
    def test_gain_ranges(self, name, step, lo, hi):
        assert getattr(config, f"GAIN_{name}_STEP") == step
        assert getattr(config, f"GAIN_{name}_MIN") == lo
        assert getattr(config, f"GAIN_{name}_MAX") == hi


# --- error hierarchy ------------------------------------------------------- #
@pytest.mark.parametrize("name", [
    "INVALID_PARAM", "NOT_FOUND", "BUSY", "NO_MEM", "LIBUSB", "THREAD",
    "STREAMING_THREAD_ERR", "STREAMING_STOPPED", "STREAMING_EXIT_CALLED",
    "USB_API_VERSION", "NOT_LAST_DEVICE", "OTHER",
])
def test_error_classes(name):
    assert issubclass(getattr(lib_err, name), lib_err.HackRFError)


# --- hardware-gated -------------------------------------------------------- #
@pytest.mark.hardware
@pytest.mark.skipif(os.environ.get("PYSDRLIB_HW") != "1", reason="set PYSDRLIB_HW=1 with a HackRF attached")
def test_hardware_rx_roundtrip():
    import time
    from pysdrlib import hackrf
    with hackrf.Device() as sdr:
        sdr.initialize(cf=100e6, Fs=10e6)
        sdr.start_rx()
        time.sleep(1)
        samples = sdr.get_samples()
        sdr.stop_rx()
    assert samples.dtype.kind == "c"
    assert len(samples) > 0


def test_rx_callback_does_not_build_complex128(monkeypatch):
    """The callback feeds the buffer complex64 directly (no double-precision intermediate)."""
    seen = []
    monkeypatch.setattr(hackrf_mod.cb, "update", lambda samples: seen.append(samples))
    hackrf_mod.rx_callback(np.array([1, 2, 3, 4], dtype=np.int8), 4, 4)
    assert seen[0].dtype == np.complex64
    np.testing.assert_allclose(seen[0], np.array([1 + 2j, 3 + 4j]) / 128)
