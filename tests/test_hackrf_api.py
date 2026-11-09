"""HackRF's own Python behaviour over a fake driver. What every device does is in test_device_contract.py."""
import numpy as np
import pytest

from devices import BY_NAME

# A device whose vendor library cannot be built here (missing system packages) is skipped, not failed
pytest.importorskip("pysdrlib.hackrf", exc_type=ImportError)

from pysdrlib import err
from pysdrlib.hackrf.config import ConfigHackRF as config
from pysdrlib.hackrf.lib import err as lib_err


@pytest.fixture
def spec():
    return BY_NAME["hackrf"]


def calls(sdr, name):
    return sdr.device.of(name)


def test_open_by_serial(make_sdr):
    sdr = make_sdr()
    sdr.open(serial_number="abc123")
    assert sdr.state["open"]


class TestSpecificGainBackend:
    """The `_set_*` hooks are the contract between HackRF and the driver."""

    def test_rx_rf_enables_the_amp(self, sdr):
        sdr._set_rx_rf_gain(11)
        sdr._set_rx_rf_gain(0)
        assert calls(sdr, "set_amp_enable") == [(True,), (False,)]

    def test_rx_if_is_the_lna(self, sdr):
        sdr._set_rx_if_gain(24)
        assert calls(sdr, "set_lna_gain") == [(24,)]

    def test_rx_bb_is_the_vga(self, sdr):
        sdr._set_rx_bb_gain(30)
        assert calls(sdr, "set_vga_gain") == [(30,)]

    def test_tx_rf_enables_the_amp(self, sdr):
        sdr._set_tx_rf_gain(11)
        assert calls(sdr, "set_amp_enable") == [(True,)]

    def test_tx_if_is_the_txvga(self, sdr):
        sdr._set_tx_if_gain(20)
        assert calls(sdr, "set_txvga_gain") == [(20,)]

    def test_bias_t_is_the_antenna_port_power(self, sdr):
        sdr.set_bias_t(True)
        assert calls(sdr, "set_antenna_enable") == [(True,)]


class TestSpecificGainPublic:
    @pytest.mark.parametrize("setter,value,driver", [
        ("set_rx_rf_gain", 11, "set_amp_enable"), ("set_rx_if_gain", 24, "set_lna_gain"),
        ("set_rx_bb_gain", 30, "set_vga_gain"), ("set_tx_rf_gain", 11, "set_amp_enable"),
        ("set_tx_if_gain", 20, "set_txvga_gain"),
    ])
    def test_reaches_its_driver_call(self, sdr, setter, value, driver):
        assert getattr(sdr, setter)(value) == value
        assert calls(sdr, driver) == [(bool(value) if driver == "set_amp_enable" else value,)]

    def test_off_step_values_are_lowered_with_a_warning(self, sdr):
        with pytest.warns(Warning):
            assert sdr.set_rx_if_gain(20) == 16   # LNA moves in 8 dB steps


class TestTotalGain:
    def test_rx_default_programs_the_lna_and_vga(self, sdr):
        sdr.set_rx_gain()
        assert calls(sdr, "set_lna_gain") == [(config.DEFAULT_GAIN_RX_IF,)]
        assert calls(sdr, "set_vga_gain") == [(config.DEFAULT_GAIN_RX_BB,)]

    def test_tx_uses_the_amp_only_when_the_txvga_cannot_supply_it(self, sdr):
        sdr.set_tx_gain(45)   # the TXVGA reaches 47 dB on its own: the amp is switched off, whatever it was
        assert calls(sdr, "set_amp_enable") == [(False,)] and calls(sdr, "set_txvga_gain")[-1] == (45,)
        sdr.device.calls.clear()
        sdr.set_tx_gain(58)   # max: the 11 dB amp plus 47 dB of TXVGA
        assert calls(sdr, "set_amp_enable") == [(True,)] and calls(sdr, "set_txvga_gain") == [(47,)]


class TestConfig:
    def test_limits(self):
        assert (config.FREQ_MIN, config.FREQ_MAX) == (1_000_000, 6_000_000_000)
        assert (config.SAMPLE_RATE_MIN, config.SAMPLE_RATE_MAX) == (2_000_000, 20_000_000)

    @pytest.mark.parametrize("name,step,lo,hi", [
        ("RX_RF", 11, 0, 11), ("RX_IF", 8, 0, 40), ("RX_BB", 2, 0, 62),
        ("TX_RF", 11, 0, 11), ("TX_IF", 1, 0, 47),
    ])
    def test_gain_ranges(self, name, step, lo, hi):
        assert getattr(config, f"GAIN_{name}_STEP") == step
        assert getattr(config, f"GAIN_{name}_MIN") == lo
        assert getattr(config, f"GAIN_{name}_MAX") == hi


@pytest.mark.parametrize("name", [
    "INVALID_PARAM", "NOT_FOUND", "BUSY", "NO_MEM", "LIBUSB", "THREAD",
    "STREAMING_THREAD_ERR", "STREAMING_STOPPED", "STREAMING_EXIT_CALLED",
    "USB_API_VERSION", "NOT_LAST_DEVICE", "OTHER",
])
def test_error_classes(name):
    assert issubclass(getattr(lib_err, name), lib_err.HackRFError)


class TestTransmit:
    def test_start_sizes_the_queue_for_the_rate(self, sdr):
        sdr.set_sample_rate(10e6)
        sdr.start_tx()
        assert calls(sdr, "set_tx_buffer")[-1] == (int(type(sdr).TX_BUFFER_SECONDS * sdr.Fs),)
        assert sdr.device.names()[-1] == "start_tx"
        sdr.stop_tx()
        assert sdr.device.names()[-1] == "stop_tx"

    def test_write_needs_start_tx(self, sdr):
        with pytest.raises(err.NotStreaming):
            sdr.write(np.zeros(4, np.complex64))

    def test_write_hands_over_complex64_whatever_it_was_given(self, sdr):
        got = []
        sdr.device._returns["write_tx"] = lambda x, timeout: got.append(x) or len(x)
        sdr.device._returns["tx_error"] = lambda: ""
        sdr.start_tx()
        assert sdr.write(np.ones(8, np.complex128)) == 8
        assert got[0].dtype == np.complex64 and got[0].flags.c_contiguous

    def test_write_reports_a_dead_transmitter(self, sdr):
        sdr.device._returns["write_tx"] = lambda x, timeout: 0
        sdr.device._returns["tx_error"] = lambda: "STREAMING_STOPPED"
        sdr.start_tx()
        with pytest.raises(err.StreamStalled):
            sdr.write(np.zeros(8, np.complex64))

    def test_half_duplex_refuses_tx_while_receiving(self, sdr):
        sdr.start_rx()
        with pytest.raises(err.Busy):
            sdr.start_tx()
        assert "start_tx" not in sdr.device.names()

    def test_half_duplex_refuses_rx_while_transmitting(self, sdr):
        sdr.start_tx()
        with pytest.raises(err.Busy):
            sdr.start_rx()
