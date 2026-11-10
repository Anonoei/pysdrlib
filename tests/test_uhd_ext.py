"""UHD's own extension behaviour (pysdrlib.uhd.lib._uhd). What every extension does is in test_native_contract.py.
Needs the extension and libuhd, but no device, except for the `hardware` tests.
"""
import time

import numpy as np
import pytest

from devices import BY_NAME, HW

pytest.importorskip("pysdrlib.uhd", exc_type=ImportError)
from pysdrlib.uhd import images, lib
from pysdrlib.uhd.lib import err as lib_err

uhd = lib.uhd


@pytest.fixture
def spec():
    return BY_NAME["uhd"]


def test_init_is_repeatable():
    uhd.init()
    uhd.init()


def test_find_returns_dicts():
    assert all(isinstance(d, dict) for d in uhd.find())


def test_dir_enum():
    assert {m.name for m in uhd.DIR.__members__.values()} == {"RX", "TX"}


def test_the_extension_needs_no_boost():
    """The C shim keeps UHD's C++ headers (and Boost) out of the extension's build"""
    from pathlib import Path
    native = Path(lib.__file__).resolve().parents[3] / "pysdrlib" / "_native" / "uhd"
    for file in native.glob("uhd_[dp]*.[ch]pp"):
        text = file.read_text()
        assert "#include <uhd" not in text and "#include <boost" not in text, file.name


@pytest.mark.skipif(len(uhd.find()) > 0, reason="a device is attached")
def test_open_without_device_raises_not_found():
    with pytest.raises(lib_err.NOT_FOUND):
        uhd.open()


def test_open_unknown_serial_raises_not_found():
    with pytest.raises(lib_err.NOT_FOUND):
        uhd.open("serial=does-not-exist")


def test_closed_device_raises_not_open():
    dev = uhd.device()
    for call in (lambda: dev.set_rx_freq(100e6, 0), lambda: dev.start_tx(), lambda: dev.time_now(), lambda: dev.info()):
        with pytest.raises(lib_err.NOT_OPEN):
            call()


@pytest.mark.hardware
@pytest.mark.skipif(not HW, reason="set PYSDRLIB_HW=1 with a USRP attached")
class TestHardware:
    @pytest.fixture
    def dev(self):
        if not uhd.find():
            pytest.skip("no USRP attached")
        images.configure()
        d = uhd.open("")
        yield d
        d.close()

    def test_info_and_ranges(self, dev):
        assert dev.info().get("mboard_serial")
        assert dev.num_rx_channels() >= 1 and dev.num_tx_channels() >= 1
        lo, hi = dev.rx_bandwidth_range(0)
        assert 0 < lo < hi
        assert "RX2" in dev.rx_antennas(0) and "internal" in dev.clock_sources()

    def test_controls_read_back(self, dev):
        dev.set_sample_rate(5e6)
        assert dev.get_rx_rate(0) == pytest.approx(5e6, rel=1e-3)
        dev.set_freq(915_000_000)
        assert dev.get_rx_freq(0) == pytest.approx(915e6, abs=100)
        dev.set_rx_gain(30, 0)
        assert dev.get_rx_gain(0) == pytest.approx(30)
        dev.set_rx_antenna("RX2", 0)
        assert dev.get_rx_antenna(0) == "RX2"

    def test_sensors(self, dev):
        assert "lo_locked" in dev.rx_sensors(0) and "ref_locked" in dev.mboard_sensors()

    def test_receives_without_loss(self, dev):
        dev.set_sample_rate(5e6)
        dev.set_freq(915_000_000)
        dev.set_rx_buffer(5_000_000)
        dev.start_rx()
        try:
            assert dev.wait_rx(2_000_000, 5.0) >= 2_000_000
            x = dev.read_rx(2_000_000)
        finally:
            dev.stop_rx()
        assert x.dtype == np.complex64 and len(x) == 2_000_000 and dev.rx_overflows() == 0
        assert not dev.is_streaming()

    def test_transmit_counts_what_it_sends(self, dev):
        dev.set_sample_rate(2e6)
        dev.set_freq(915_000_000)
        dev.set_tx_gain(0, 0)       # no antenna is needed to check the plumbing: keep the output negligible
        dev.set_tx_buffer(500_000)
        dev.start_tx()
        try:
            assert dev.write_tx(np.zeros(200_000, np.complex64), 5.0) == 200_000
            deadline = time.monotonic() + 5
            while dev.tx_stats()["sent"] < 200_000 and time.monotonic() < deadline:
                time.sleep(0.01)
        finally:
            dev.stop_tx()
        stats = dev.tx_stats()
        assert stats["sent"] == 200_000 and stats["async_errors"] == 0 and not dev.tx_error()
