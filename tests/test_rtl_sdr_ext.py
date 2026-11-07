"""RTL-SDR's own extension behaviour (pysdrlib.rtl_sdr.lib._rtl_sdr). What every extension does is in
test_native_contract.py. Needs the extension and librtlsdr (built on first use) but no dongle, except for
the `hardware` tests, which need PYSDRLIB_HW=1 and an RTL-SDR attached.
"""
import time

import numpy as np
import pytest

from devices import BY_NAME, HW

pytest.importorskip("pysdrlib.rtl_sdr", exc_type=ImportError)   # loads (or builds) librtlsdr
from pysdrlib.rtl_sdr import lib
from pysdrlib.rtl_sdr.config import ConfigRTL_SDR as config
from pysdrlib.rtl_sdr.lib import err as lib_err

rtl = lib.rtl_sdr


@pytest.fixture
def spec():
    return BY_NAME["rtl_sdr"]


def test_device_count():
    assert rtl.device_count() >= 0


@pytest.mark.skipif(rtl.device_count() > 0, reason="a dongle is attached")
def test_open_without_device_raises_not_found():
    with pytest.raises(lib_err.NOT_FOUND):
        rtl.open()
    with pytest.raises(lib_err.NOT_FOUND):
        rtl.open_by_serial("nonexistent")


def test_info_of_a_missing_dongle_raises_not_found():
    with pytest.raises(lib_err.NOT_FOUND):
        rtl.device_info(rtl.device_count())


@pytest.mark.hardware
@pytest.mark.skipif(not HW, reason="set PYSDRLIB_HW=1 with an RTL-SDR attached")
class TestHardware:
    @pytest.fixture
    def dev(self):
        if not rtl.device_count():
            pytest.skip("no RTL-SDR attached")
        d = rtl.open()
        yield d
        d.close()

    def stream(self, dev, seconds=0.4):
        got = []
        dev.set_rx_callback(lambda a, v, n: got.append(a))
        dev.start_rx()
        time.sleep(seconds)
        dev.stop_rx()
        return got

    def test_info(self, dev):
        assert dev.tuner != "unknown" and dev.gains()

    def test_gain_snaps_to_a_tuner_step(self, dev):
        steps = dev.gains()
        got = dev.set_rx_rf_gain(20.0)
        assert got in steps and abs(got - 20.0) <= 5
        assert dev.set_rx_rf_gain(1000.0) == max(steps)

    def test_sample_rate(self, dev):
        assert dev.set_sample_rate(config.DEFAULT_SAMPLE_RATE) == config.DEFAULT_SAMPLE_RATE
        with pytest.raises(lib_err.INVALID_PARAM):
            dev.set_sample_rate(500_000)

    def test_stream_is_uint8_pairs(self, dev):
        dev.set_freq(100_000_000)
        dev.set_sample_rate(2_400_000)
        got = self.stream(dev)
        assert got, "no data within the capture window"
        assert all(a.dtype == np.uint8 and len(a) % 2 == 0 for a in got)
        assert not dev.is_streaming()

    def test_stream_runs_at_the_requested_rate(self, dev):
        dev.set_freq(100_000_000)
        dev.set_sample_rate(2_400_000)
        start = time.perf_counter()
        got = self.stream(dev, 0.5)
        elapsed = time.perf_counter() - start
        samples = sum(len(a) // 2 for a in got)
        assert 0.5 * 2.4e6 * 0.5 < samples < 1.5 * 2.4e6 * elapsed

    def test_restart_and_reopen(self, dev):
        dev.set_sample_rate(2_400_000)
        assert self.stream(dev, 0.2) and self.stream(dev, 0.2)
        dev.close()
        d = rtl.open()
        d.close()
