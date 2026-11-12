"""bladeRF's own extension behaviour (pysdrlib.bladerf.lib._bladerf). What every extension does is in test_native_contract.py.
Needs the extension and libbladeRF, but no device, except for the `hardware` tests.
"""
import time

import numpy as np
import pytest

from devices import BY_NAME, HW

pytest.importorskip("pysdrlib.bladerf", exc_type=ImportError)
from pysdrlib.bladerf import lib
from pysdrlib.bladerf.lib import err as lib_err

bladerf = lib.bladerf


@pytest.fixture
def spec():
    return BY_NAME["bladerf"]


def test_init_is_repeatable():
    bladerf.init()
    bladerf.init()


def test_find_returns_dicts():
    assert all(isinstance(d, dict) for d in bladerf.find())


def test_dir_enum():
    assert {m.name for m in bladerf.DIR.__members__.values()} == {"RX", "TX"}


def test_library_version_is_reported():
    assert bladerf.library_version()


def test_log_level_is_checked():
    bladerf.set_log_level("silent")
    with pytest.raises(ValueError):
        bladerf.set_log_level("loud")


@pytest.mark.skipif(len(bladerf.find()) > 0, reason="a device is attached")
def test_open_without_device_raises_not_found():
    with pytest.raises(lib_err.NOT_FOUND):
        bladerf.open()


def test_open_unknown_serial_raises_not_found():
    with pytest.raises(lib_err.NOT_FOUND):
        bladerf.open("*:serial=does-not-exist")


def test_closed_device_raises_not_open():
    dev = bladerf.device()
    for call in (lambda: dev.set_rx_freq(100_000_000, 0), lambda: dev.start_tx(), lambda: dev.sensors(), lambda: dev.info()):
        with pytest.raises(lib_err.NOT_OPEN):
            call()


def test_error_classes_derive_from_BladeRFError():
    for name in ("NOT_FOUND", "NOT_OPEN", "TIMEOUT", "NODEV", "PERMISSION", "UNSUPPORTED_BOARD", "VALUE"):
        assert issubclass(getattr(lib_err, name), lib_err.BladeRFError)


@pytest.mark.hardware
@pytest.mark.skipif(not HW, reason="set PYSDRLIB_HW=1 with a bladeRF 2.0 micro attached")
class TestHardware:
    @pytest.fixture
    def dev(self):
        if not bladerf.find():
            pytest.skip("no bladeRF attached")
        d = bladerf.open("")
        yield d
        d.close()

    def test_info_and_ranges(self, dev):
        assert dev.info().get("serial") and dev.info()["board"] == "bladerf2"
        assert dev.num_rx_channels() == 2 and dev.num_tx_channels() == 2
        lo, hi, _ = dev.rx_bandwidth_range(0)
        assert 0 < lo < hi

    def test_controls_read_back(self, dev):
        assert dev.set_rx_rate(5e6, 0) == pytest.approx(5e6, rel=1e-3)
        assert dev.get_rx_rate(0) == pytest.approx(5e6, rel=1e-3)
        dev.set_rx_freq(915_000_000, 0)
        assert dev.get_rx_freq(0) == pytest.approx(915e6, abs=100)
        dev.set_rx_gain_stage("full", 30, 0)
        assert dev.get_rx_gain_stage("full", 0) == 30

    def test_sensors(self, dev):
        assert "rfic_temperature" in dev.sensors()

    def test_receives_without_loss(self, dev):
        dev.set_rx_rate(5e6, 0)
        dev.set_rx_freq(915_000_000, 0)
        dev.set_rx_buffer(5_000_000)
        dev.start_rx()
        try:
            assert dev.wait_rx(2_000_000, 5.0) >= 2_000_000
            x = dev.read_rx(2_000_000)
        finally:
            dev.stop_rx()
        assert x.dtype == np.complex64 and len(x) == 2_000_000 and dev.rx_overflows() == 0
        assert not dev.is_streaming()

    def test_two_channels_are_sampled_together(self, dev):
        dev.set_rx_rate(5e6, 0)
        dev.set_rx_freq(915_000_000, 0)
        dev.set_rx_channels([0, 1])
        dev.set_rx_buffer(2_000_000, 2)
        dev.start_rx()
        try:
            assert dev.wait_rx(500_000, 5.0, 0) >= 500_000 and dev.wait_rx(500_000, 5.0, 1) >= 500_000
        finally:
            dev.stop_rx()

    def test_transmit_counts_what_it_sends(self, dev):
        dev.set_tx_rate(2e6, 0)
        dev.set_tx_freq(915_000_000, 0)
        dev.set_tx_gain_stage("dsa", -89, 0)   # no antenna is needed to check the plumbing: keep the output negligible
        dev.set_tx_buffer(500_000)
        dev.start_tx()
        try:
            assert dev.write_tx(np.zeros(200_000, np.complex64), 5.0) == 200_000
            deadline = time.monotonic() + 5
            while dev.tx_stats()["sent"] < 200_000 and time.monotonic() < deadline:
                time.sleep(0.01)
        finally:
            dev.stop_tx()
        assert dev.tx_stats()["sent"] >= 200_000 and not dev.tx_error()
