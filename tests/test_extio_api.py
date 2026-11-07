"""RX888's own Python behaviour over a fake driver. What every device does is in test_device_contract.py."""
import pytest

from devices import BY_NAME

# A device whose vendor library cannot be built here (missing system packages) is skipped, not failed
pytest.importorskip("pysdrlib.extio", exc_type=ImportError)

from pysdrlib.extio.config import ConfigRX888 as config


@pytest.fixture
def spec():
    return BY_NAME["extio"]


def calls(sdr, name):
    return sdr.device.of(name)


class TestSampleRate:
    def test_a_supported_rate_is_kept(self, sdr):
        assert sdr.set_sample_rate(16e6) == 16e6

    def test_an_unsupported_rate_snaps_to_the_nearest_with_a_warning(self, sdr):
        with pytest.warns(Warning):
            assert sdr.set_sample_rate(5e6) == 4e6
        assert sdr.Fs == 4e6
        assert sdr.device.of("set_rx_buffer")[-1] == (int(type(sdr).BUFFER_SECONDS * 4e6),)   # sized for the rate it got


class TestGain:
    def test_stage_setters_pass_db(self, sdr):
        assert sdr.set_rx_rf_gain(-10) == -10
        assert sdr.set_rx_if_gain(12.5) == 12.5
        assert calls(sdr, "set_rx_rf_gain") == [(-10.0,)]
        assert calls(sdr, "set_rx_if_gain") == [(12.5,)]

    def test_stage_is_clamped_with_a_warning(self, sdr):
        with pytest.warns(Warning):
            sdr.set_rx_if_gain(config.GAIN_RX_IF_MAX + 10)
        assert calls(sdr, "set_rx_if_gain")[-1] == (float(config.GAIN_RX_IF_MAX),)

    def test_default_total_gain(self, sdr):
        assert sdr.set_rx_gain() == {"rf": 0, "if": 0}

    def test_total_gain_fills_the_if_stage_then_the_rf_stage(self, sdr):
        sdr.set_rx_gain(config.GAIN_RX_IF_MAX + 5)
        assert calls(sdr, "set_rx_if_gain")[-1] == (float(config.GAIN_RX_IF_MAX),)
        assert calls(sdr, "set_rx_rf_gain")[-1] == (5.0,)

    def test_total_gain_below_the_if_range_attenuates(self, sdr):
        sdr.set_rx_gain(config.GAIN_RX_IF_MIN - 3)
        assert calls(sdr, "set_rx_if_gain")[-1] == (float(config.GAIN_RX_IF_MIN),)
        assert calls(sdr, "set_rx_rf_gain")[-1] == (-3.0,)


class TestAdcControls:
    def test_bias_t_applies_to_the_active_input(self, sdr):
        sdr.set_bias_t(True)
        assert calls(sdr, "set_bias_t") == [(True,)]

    @pytest.mark.parametrize("setter,driver", [("set_dither", "set_dither"), ("set_random", "set_random"), ("set_pga", "set_pga")])
    def test_toggles(self, sdr, setter, driver):
        getattr(sdr, setter)(1)
        assert calls(sdr, driver) == [(True,)]
