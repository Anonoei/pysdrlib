"""BladeRF's own Python behaviour over a fake driver. What every device does is in test_device_contract.py."""
import numpy as np
import pytest

from devices import BY_NAME, BLADERF_FAKE, FakeDevice

pytest.importorskip("pysdrlib.bladerf", exc_type=ImportError)

from pysdrlib import err
from pysdrlib.bladerf import Device as BladeRF

config = BladeRF.CAPS
from pysdrlib.bladerf.lib import err as lib_err


@pytest.fixture
def spec():
    return BY_NAME["bladerf"]


def calls(sdr, name):
    return sdr.device.of(name)


def test_open_by_serial_goes_into_the_device_string(make_sdr, monkeypatch):
    sdr = make_sdr()
    opened = []
    import sys
    native = sys.modules[sdr.__class__.__module__].lib.bladerf
    monkeypatch.setattr(native, "open", lambda args="": opened.append(args) or FakeDevice(BLADERF_FAKE))
    sdr.open(serial="ABC123")
    assert opened == ["*:serial=ABC123"]


@pytest.mark.parametrize("raised", [lib_err.NOT_FOUND("none"), lib_err.UNSUPPORTED_BOARD("a bladerf1"), lib_err.PERMISSION("denied")])
def test_a_unit_that_cannot_be_opened_is_NoDevice(make_sdr, raised):
    sdr = make_sdr(open_error=raised)
    with pytest.raises(err.NoDevice):
        sdr.open()


class TestGain:
    """One stage per direction, under libbladeRF's names"""

    def test_stages(self, sdr):
        assert set(sdr.caps.gains["rx"]) == {"FULL"} and set(sdr.caps.gains["tx"]) == {"DSA"}

    def test_rx_full_reaches_the_driver_in_whole_db(self, sdr):
        assert sdr.set_rx_full_gain(42) == 42
        assert calls(sdr, "set_rx_gain_stage")[-1] == ("full", 42, 0)

    def test_tx_dsa_is_an_attenuation(self, sdr):
        assert sdr.set_tx_dsa_gain(-30) == -30
        assert calls(sdr, "set_tx_gain_stage")[-1] == ("dsa", -30, 0)

    def test_tx_default_is_well_below_full_power(self):
        assert config.stage("tx", "DSA").default <= -40 and config.stage("tx", "DSA").max == 0

    def test_a_channel_of_its_own(self, sdr):
        sdr.set_rx_full_gain(20, channel=1)
        assert calls(sdr, "set_rx_gain_stage")[-1] == ("full", 20, 1)

    def test_rx_gain_is_clamped(self, sdr):
        with pytest.warns(Warning):
            assert sdr.set_rx_full_gain(500) == config.stage("rx", "FULL").max

    def test_generic_gain_is_the_one_stage(self, sdr):
        sdr.set_rx_gain_db(30)
        sdr.set_tx_gain_db(-20)
        assert calls(sdr, "set_rx_gain_stage")[-1] == ("full", 30, 0)
        assert calls(sdr, "set_tx_gain_stage")[-1] == ("dsa", -20, 0)

    def test_agc_is_a_receive_gain_mode(self, sdr):
        from pysdrlib.bladerf.lib import bladerf as native
        assert sdr.rx_gain_modes() == ["manual", "auto"] and sdr.tx_gain_modes() == ["manual"]
        sdr.set_rx_gain_mode("auto")
        sdr.set_rx_gain_mode("manual")
        assert calls(sdr, "set_rx_gain_mode") == [(native.GAIN_MODE.DEFAULT, 0), (native.GAIN_MODE.MGC, 0)]


class TestControls:
    def test_no_antennas(self, sdr):
        assert sdr.rx_antennas() == [] and sdr.tx_antennas() == []

    def test_bandwidth_goes_to_the_requested_direction(self, sdr):
        sdr.set_rx_bandwidth(8e6)
        sdr.set_tx_bandwidth(4e6)
        assert calls(sdr, "set_rx_bandwidth")[-1] == (8e6, 0) and calls(sdr, "set_tx_bandwidth")[-1] == (4e6, 0)

    def test_clock_source(self, sdr):
        assert sdr.clock_sources() == ["internal", "external"]
        sdr.set_clock_source("external")
        assert calls(sdr, "set_clock_select") == [("external",)]

    def test_bias_tee_powers_every_receive_channel(self, sdr):
        sdr.set_bias_t(True)
        assert calls(sdr, "set_rx_bias_tee") == [(True, 0), (True, 1)]

    def test_clock_output_setting(self, sdr):
        assert "clock_output" in sdr.settings()
        sdr.set_setting("clock_output", True)
        assert calls(sdr, "set_clock_output") == [(True,)]

    def test_frequency_goes_in_whole_hertz(self, sdr):
        sdr.set_rx_freq(915e6)
        sdr.set_tx_freq(433.92e6)
        assert calls(sdr, "set_rx_freq")[-1] == (915_000_000, 0) and calls(sdr, "set_tx_freq")[-1] == (433_920_000, 0)

    def test_caps_info_and_channels(self, sdr):
        assert sdr.caps.info["serial"] == "ABC123"
        assert sdr.caps.channels == {"rx": 2, "tx": 2}
        assert sdr.caps.shared["freq"] == "direction"    # one synthesizer per direction

    def test_sensors(self, sdr):
        assert sdr.sensors() == {"rfic_temperature": 41.5, "pll_locked": True}


class TestRate:
    def test_rx_rate_is_what_the_hardware_chose(self, sdr):
        sdr.device._returns["set_rx_rate"] = lambda rate, c=0: 9_999_998
        assert sdr.set_rx_sample_rate(10e6) == 9_999_998
        assert calls(sdr, "set_rx_buffer")[-1] == (int(type(sdr).BUFFER_SECONDS * 9_999_998),)


class TestTransmit:
    def test_start_sizes_the_queue_for_the_rate(self, sdr):
        sdr.set_rx_sample_rate(10e6)
        sdr.start_tx()
        assert calls(sdr, "set_tx_buffer")[-1] == (int(type(sdr).TX_BUFFER_SECONDS * sdr.get_rx_sample_rate()),)
        assert sdr.device.names()[-1] == "start_tx"
        sdr.stop_tx()
        assert sdr.device.names()[-1] == "stop_tx"

    def test_write_needs_start_tx(self, sdr):
        with pytest.raises(err.NotStreaming):
            sdr.write(np.zeros(4, np.complex64))

    def test_write_hands_over_complex64_whatever_it_was_given(self, sdr):
        got = []
        sdr.device._returns["write_tx"] = lambda x, timeout, slot=0: got.append(x) or len(x)
        sdr.device._returns["tx_error"] = lambda: ""
        sdr.start_tx()
        assert sdr.write(np.ones(8, np.complex128)) == 8
        assert got[0].dtype == np.complex64 and got[0].flags.c_contiguous

    def test_write_reports_a_dead_transmitter(self, sdr):
        sdr.device._returns["write_tx"] = lambda x, timeout, slot=0: 0
        sdr.device._returns["tx_error"] = lambda: "bladerf_sync_tx: I/O error"
        sdr.start_tx()
        with pytest.raises(err.StreamStalled):
            sdr.write(np.zeros(8, np.complex64))


def test_config_limits_are_the_micros():
    assert (config.freq[0].lo, config.freq[0].hi) == (70e6, 6e9)
    assert config.rates[0].lo == 520_834 and config.rates[0].hi == 61.44e6
    assert config.stage("tx", "DSA").min == -89
