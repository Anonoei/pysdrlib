"""RX888's own extension behaviour (pysdrlib.extio.lib._extio). What every extension does is in
test_native_contract.py. Needs the extension and libsddc_core (built on first use) but no device,
except for the `hardware` tests, which need PYSDRLIB_HW=1 and an RX888 with an antenna on the V port.
"""
import time

import numpy as np
import pytest

from devices import BY_NAME, HW

pytest.importorskip("pysdrlib.extio", exc_type=ImportError)   # loads (or builds) libsddc_core
from pysdrlib.extio import lib
from pysdrlib.extio import Device as RX888

config = RX888.CAPS
from pysdrlib.extio.lib import err as lib_err

extio = lib.extio
HF, VHF = 7_100_000, 100_000_000


@pytest.fixture
def spec():
    return BY_NAME["extio"]


def test_sample_rates_match_the_config():
    rates = extio.device().sample_rates()
    assert rates == sorted(rates)
    assert (rates[0], rates[-1]) == (config.rates[0].lo, config.rates[0].hi)
    assert config.default_rate in rates


def test_device_count():
    assert extio.device_count() >= 0


@pytest.mark.skipif(extio.device_count() > 0, reason="a device is attached")
def test_open_without_device_raises_not_found():
    with pytest.raises(lib_err.NOT_FOUND):
        extio.open()


def test_closed_device_raises_not_open():
    dev = extio.device()
    for call in (lambda: dev.name, lambda: dev.firmware, lambda: dev.set_rx_if_gain(0.0), lambda: dev.set_dither(True)):
        with pytest.raises(lib_err.NOT_OPEN):
            call()


@pytest.mark.hardware
@pytest.mark.skipif(not HW, reason="set PYSDRLIB_HW=1 with an RX888 attached")
class TestHardware:
    @pytest.fixture
    def dev(self):
        if not extio.device_count():
            pytest.skip("no RX888 attached")
        d = extio.open()
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
        assert "RX888" in dev.name and dev.firmware > 0

    def test_input_follows_frequency(self, dev):
        dev.set_freq(HF)
        assert not dev.is_vhf
        dev.set_freq(VHF)
        assert dev.is_vhf
        dev.set_freq(HF)
        assert not dev.is_vhf

    def test_out_of_range_frequency(self, dev):
        with pytest.raises(lib_err.INVALID_PARAM):
            dev.set_freq(1)
        with pytest.raises(lib_err.INVALID_PARAM):
            dev.set_freq(10_000_000_000)

    def test_sample_rate_snaps_to_nearest(self, dev):
        assert dev.set_sample_rate(5e6) in (4e6, 8e6)
        assert dev.set_sample_rate(16e6) == 16e6

    def test_gain_stages_report_the_applied_step(self, dev):
        dev.set_freq(VHF)
        rf = dev.set_rx_rf_gain(20.0)
        assert rf is not None and abs(rf - 20.0) < 5
        assert dev.set_rx_rf_gain(1000.0) >= rf   # clamped to the top step
        dev.set_freq(HF)
        assert dev.set_rx_rf_gain(-100.0) <= 0     # the HF attenuator never adds gain

    def test_gain_survives_switching_inputs(self, dev):
        dev.set_freq(HF)
        dev.set_rx_rf_gain(-10.0)
        dev.set_freq(VHF)
        dev.set_freq(HF)   # re-applies the requested gains without raising

    def test_adc_controls(self, dev):
        for setter in (dev.set_dither, dev.set_pga):
            setter(True)
            setter(False)

    def test_stream_is_complex_float(self, dev):
        dev.set_freq(VHF)
        dev.set_sample_rate(2e6)
        got = self.stream(dev)
        assert got, "no data within the capture window"
        assert all(a.dtype == np.float32 and len(a) % 2 == 0 and np.isfinite(a).all() for a in got)

    def test_stream_runs_at_the_requested_rate(self, dev):
        dev.set_freq(HF)
        dev.set_sample_rate(8e6)
        start = time.perf_counter()
        got = self.stream(dev, 0.3)
        elapsed = time.perf_counter() - start
        samples = sum(len(a) // 2 for a in got)
        assert 0.5 * 8e6 * 0.3 < samples < 1.5 * 8e6 * elapsed

    def test_vhf_antenna_has_signal(self, dev):
        """Needs an antenna on the V port; FM broadcast is the strongest thing at 100 MHz"""
        dev.set_freq(VHF)
        dev.set_sample_rate(2e6)
        dev.set_rx_rf_gain(25.0)
        dev.set_rx_if_gain(25.0)
        iq = np.concatenate(self.stream(dev, 0.5)).view(np.complex64)
        n = 4096
        psd = np.mean(np.abs(np.fft.fft(iq[: len(iq) // n * n].reshape(-1, n) * np.blackman(n), axis=1)) ** 2, axis=0)
        assert 10 * np.log10(psd.max() / np.median(psd)) > 15

    def test_reopen_after_close(self, dev):
        dev.close()
        d = extio.open()
        d.set_freq(HF)
        d.close()
