"""RTL-SDR's own Python behaviour over a fake driver. What every device does is in test_device_contract.py."""
import pytest

from devices import BY_NAME
from pysdrlib.rtl_sdr.config import ConfigRTL_SDR as config


@pytest.fixture
def spec():
    return BY_NAME["rtl_sdr"]


def calls(sdr, name):
    return sdr.device.of(name)


class TestSampleRate:
    def test_rates_in_the_gap_snap_to_the_nearest_edge_with_a_warning(self, sdr):
        low, high = config.SAMPLE_RATE_GAP
        with pytest.warns(Warning):
            assert sdr.set_sample_rate(400_000) == low
        with pytest.warns(Warning):
            assert sdr.set_sample_rate(800_000) == high
        assert calls(sdr, "set_sample_rate") == [(low,), (high,)]
        assert sdr.device.of("set_rx_buffer")[-1] == (int(type(sdr).BUFFER_SECONDS * high),)

    def test_the_edges_are_valid(self, sdr):
        low, high = config.SAMPLE_RATE_GAP
        assert sdr.set_sample_rate(low) == low
        assert sdr.set_sample_rate(high) == high

    def test_the_rate_the_hardware_settles_on_is_used(self, make_sdr, spec):
        spec.fake_returns["set_sample_rate"] = lambda v: v + 100
        try:
            sdr = make_sdr()
            sdr.open()
            with pytest.warns(Warning):
                assert sdr.set_sample_rate(2_000_000) == 2_000_100
        finally:
            spec.fake_returns["set_sample_rate"] = lambda v: v


class TestGain:
    def test_stage_passes_db(self, sdr):
        assert sdr.set_rx_rf_gain(19.7) == 19.7
        assert calls(sdr, "set_rx_rf_gain") == [(19.7,)]

    def test_stage_is_clamped_with_a_warning(self, sdr):
        with pytest.warns(Warning):
            sdr.set_rx_rf_gain(config.GAIN_RX_RF_MAX + 10)
        assert calls(sdr, "set_rx_rf_gain")[-1] == (float(config.GAIN_RX_RF_MAX),)

    def test_total_gain_is_the_tuner_gain(self, sdr):
        assert sdr.set_rx_gain() == {"rf": config.DEFAULT_GAIN_RX_RF}
        assert sdr.set_rx_gain(40) == {"rf": 40}
        assert calls(sdr, "set_rx_rf_gain")[-1] == (40.0,)

    def test_agc(self, sdr):
        sdr.set_agc(True)
        sdr.set_agc(0)
        assert calls(sdr, "set_agc") == [(True,), (False,)]


class TestControls:
    def test_bias_t(self, sdr):
        sdr.set_bias_t(1)
        assert calls(sdr, "set_bias_t") == [(True,)]

    def test_frequency_correction(self, sdr):
        sdr.set_freq_correction(-12.0)
        assert calls(sdr, "set_freq_correction") == [(-12,)]


class TestOpen:
    def test_by_index_or_serial(self, spec, monkeypatch):
        from types import SimpleNamespace
        opened = []
        native = SimpleNamespace(open=lambda i: opened.append(("index", i)) or object(),
                                 open_by_serial=lambda s: opened.append(("serial", s)) or object())
        monkeypatch.setattr(spec.device_module, "lib", SimpleNamespace(rtl_sdr=native, err=spec.lib.err))
        spec.device_class().open(index=2)
        spec.device_class().open(serial_number="00000001")
        assert opened == [("index", 2), ("serial", "00000001")]

    def test_a_dongle_that_cannot_be_opened_is_no_device(self, make_sdr, spec):
        from pysdrlib import err
        sdr = make_sdr(open_error=spec.lib.err.OPEN_FAILED("no permission"))
        with pytest.raises(err.NoDevice, match="permission"):
            sdr.open()
