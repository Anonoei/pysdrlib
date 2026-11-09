"""UHD (B210)'s own Python behaviour over a fake driver. What every device does is in test_device_contract.py."""
import numpy as np
import pytest

from devices import BY_NAME, UHD_FAKE, FakeDevice

pytest.importorskip("pysdrlib.uhd", exc_type=ImportError)

from pysdrlib import err
from pysdrlib.uhd import images
from pysdrlib.uhd.config import ConfigB210 as config
from pysdrlib.uhd.lib import err as lib_err


@pytest.fixture
def spec():
    return BY_NAME["uhd"]


def calls(sdr, name):
    return sdr.device.of(name)


def test_open_by_serial_goes_into_the_device_address(make_sdr, monkeypatch):
    sdr = make_sdr()
    opened = []
    fake = sdr.__class__.__module__
    import sys
    native = sys.modules[fake].lib.uhd
    monkeypatch.setattr(native, "open", lambda args="": opened.append(args) or FakeDevice(UHD_FAKE))
    sdr.open(serial="31B92DD", args="type=b200")
    assert opened == ["serial=31B92DD,type=b200"]


def test_no_device_is_NoDevice(make_sdr):
    sdr = make_sdr(open_error=lib_err.NOT_FOUND("none"))
    with pytest.raises(err.NoDevice):
        sdr.open()


class TestGain:
    """One stage per direction: the AD9361's PGA"""

    def test_stages(self, sdr):
        assert set(sdr.caps.gains["rx"]) == {"PGA"} and set(sdr.caps.gains["tx"]) == {"PGA"}

    def test_rx_pga_reaches_the_driver(self, sdr):
        assert sdr.set_rx_pga_gain(42) == 42
        assert calls(sdr, "set_rx_gain")[-1] == (42.0, 0)

    def test_tx_pga_has_quarter_db_steps(self, sdr):
        assert sdr.set_tx_pga_gain(10.25) == 10.25
        assert calls(sdr, "set_tx_gain")[-1] == (10.25, 0)

    def test_rx_gain_is_clamped(self, sdr):
        with pytest.warns(Warning):
            assert sdr.set_rx_pga_gain(500) == config.GAIN_RX_PGA_MAX

    def test_generic_gain_is_the_pga(self, sdr):
        sdr.set_rx_gain(30)
        sdr.set_tx_gain(None)
        assert calls(sdr, "set_rx_gain")[-1] == (30.0, 0)
        assert calls(sdr, "set_tx_gain")[-1] == (float(config.DEFAULT_GAIN_TX_PGA), 0)

    def test_agc_is_a_receive_gain_mode(self, sdr):
        assert sdr.gain_modes("rx") == ["manual", "auto"] and sdr.gain_modes("tx") == ["manual"]
        sdr.set_gain_mode("auto")
        sdr.set_gain_mode("manual")
        assert calls(sdr, "set_rx_agc") == [(True, 0), (False, 0)]


class TestControls:
    def test_antenna(self, sdr):
        assert sdr.antennas("rx") == ["TX/RX", "RX2"] and sdr.antennas("tx") == ["TX/RX"]
        sdr.set_antenna("RX2")
        assert calls(sdr, "set_rx_antenna") == [("RX2", 0)]
        with pytest.raises(err.NotSupported):
            sdr.set_antenna("RX2", "tx")

    def test_bandwidth_goes_to_the_requested_direction(self, sdr):
        sdr.set_bandwidth(8e6, "rx")
        sdr.set_bandwidth(4e6, "tx")
        assert calls(sdr, "set_rx_bandwidth")[-1] == (8e6, 0) and calls(sdr, "set_tx_bandwidth")[-1] == (4e6, 0)

    def test_clock_source(self, sdr):
        assert sdr.clock_sources() == ["internal", "external", "gpsdo"]
        sdr.set_clock_source("external")
        assert calls(sdr, "set_clock_source") == [("external",)]

    def test_no_bias_tee(self, sdr):
        assert "bias_t" not in sdr.settings()
        with pytest.raises(err.NotSupported):
            sdr.set_bias_t(True)

    def test_settings(self, sdr):
        assert {"dc_offset_auto", "iq_balance_auto", "time_source", "master_clock_rate"} <= set(sdr.settings())
        sdr.set_setting("dc_offset_auto", False)
        sdr.set_setting("iq_balance_auto", True)
        sdr.set_setting("time_source", "external")
        assert calls(sdr, "set_rx_dc_offset") == [(False, 0)] and calls(sdr, "set_rx_iq_balance") == [(True, 0)]
        assert calls(sdr, "set_time_source") == [("external",)]
        with pytest.raises(err.InvalidValue):
            sdr.set_setting("time_source", "wwv")

    def test_caps_info_and_channels(self, sdr):
        assert sdr.caps.info["mboard_serial"] == "ABC123"
        assert sdr.caps.channels == {"rx": 2, "tx": 2}   # frontends A and B, each way
        assert sdr.caps.shared["freq"] == "direction"    # one synthesizer per direction


class TestMasterClock:
    """UHD's own choice fails or gives odd decimations; the driver picks an even multiple of the rate"""

    @pytest.mark.parametrize("rate, mcr", [(10e6, 40e6), (1e6, 32e6), (2.4e6, 38.4e6), (30.72e6, 30.72e6), (61.44e6, 61.44e6),
                                           (25e6, 25e6), (200e3, 25.6e6)])
    def test_choice(self, sdr, rate, mcr):
        sdr.device._returns["get_master_clock_rate"] = lambda: 7e6     # nothing suitable running
        assert sdr._master_clock_for(rate) == pytest.approx(mcr)

    @pytest.mark.parametrize("rate, mcr", [(10e6, 20e6), (2e6, 16e6), (1e6, 16e6), (30.72e6, 30.72e6), (15.36e6, 30.72e6)])
    def test_two_channels_keep_the_clock_under_30_72_mhz(self, sdr, rate, mcr):
        sdr.device._returns["get_master_clock_rate"] = lambda: 40e6
        assert sdr._master_clock_for(rate, channels=2) == pytest.approx(mcr)

    def test_keeps_a_running_clock_that_fits(self, sdr):
        sdr.device._returns["get_master_clock_rate"] = lambda: 40e6
        assert sdr._master_clock_for(10e6) == 40e6 and sdr._master_clock_for(40e6) == 40e6
        assert sdr._master_clock_for(8e6) == 32e6        # 40 MHz / 8 MHz is an odd decimation: not kept

    def test_changes_pause_streams(self, sdr):
        sdr.device._returns["get_master_clock_rate"] = lambda: 7e6
        sdr.device._returns["is_streaming"] = lambda: True
        sdr.device._returns["is_transmitting"] = lambda: True
        sdr.set_sample_rate(10e6)
        names = sdr.device.names()
        i = names.index("set_master_clock_rate")
        assert names[i - 2:i] == ["stop_tx", "stop_rx"] and names[i + 1:i + 3] == ["start_rx", "start_tx"]

    def test_pinned_clock_is_left_alone(self, sdr):
        sdr.set_setting("master_clock_rate", 32e6)
        sdr.device.calls.clear()
        sdr.set_sample_rate(10e6)
        assert "set_master_clock_rate" not in sdr.device.names()
        sdr.set_setting("master_clock_rate", 0)       # automatic again


class TestTransmit:
    def test_start_sizes_the_queue_for_the_rate(self, sdr):
        sdr.set_sample_rate(10e6)
        sdr.start_tx()
        assert calls(sdr, "set_tx_buffer")[-1] == (int(type(sdr).TX_BUFFER_SECONDS * sdr.Fs),)
        assert sdr.device.names()[-1] == "start_tx"
        sdr.stop_tx()
        assert sdr.device.names()[-1] == "stop_tx"

    def test_write_needs_start_tx(self, sdr):
        with pytest.raises(err.NotStreaming):    # as read() does without start_rx()
            sdr.write(np.zeros(4, np.complex64))

    def test_write_hands_over_complex64_whatever_it_was_given(self, sdr):
        got = []
        sdr.device._returns["write_tx"] = lambda x, timeout: got.append(x) or len(x)
        sdr.device._returns["tx_error"] = lambda: ""
        sdr.start_tx()
        assert sdr.write(np.ones(8, np.complex128)) == 8
        assert got[0].dtype == np.complex64 and got[0].flags.c_contiguous

    def test_write_keeps_going_until_everything_is_queued(self, sdr):
        sizes = []
        sdr.device._returns["write_tx"] = lambda x, timeout: sizes.append(len(x)) or min(len(x), 3)
        sdr.device._returns["tx_error"] = lambda: ""
        sdr.start_tx()
        assert sdr.write(np.zeros(8, np.complex64)) == 8 and sizes == [8, 5, 2]

    def test_write_reports_a_dead_transmitter(self, sdr):
        sdr.device._returns["write_tx"] = lambda x, timeout: 0
        sdr.device._returns["tx_error"] = lambda: "LIBUSB_ERROR_IO"
        sdr.start_tx()
        with pytest.raises(err.StreamStalled):
            sdr.write(np.zeros(8, np.complex64))


class TestImages:
    def test_an_existing_image_is_used_as_is(self, tmp_path, monkeypatch):
        (tmp_path / "usrp_b210_fpga.bin").write_bytes(b"custom")
        monkeypatch.setenv("UHD_IMAGES_DIR", str(tmp_path))
        monkeypatch.setattr(images, "SYSTEM_DIRS", ())
        assert images.configure() == tmp_path

    def test_nothing_is_downloaded_on_its_own(self, tmp_path, monkeypatch):
        monkeypatch.delenv("UHD_IMAGES_DIR", raising=False)
        monkeypatch.setattr(images, "SYSTEM_DIRS", ())
        monkeypatch.setattr(images, "cache_dir", lambda: tmp_path / "none")
        monkeypatch.setattr(images, "download", lambda: pytest.fail("images must not be downloaded implicitly"))
        assert images.configure() is None


def test_config_limits_are_the_b210s():
    assert config.FREQ_MIN == 50e6 and config.FREQ_MAX == 6e9
    assert config.GAIN_RX_PGA_MAX == 76 and config.GAIN_TX_PGA_MAX == 89.75 and config.GAIN_TX_PGA_STEP == 0.25


def test_error_classes_derive_from_UHDError():
    for name in ("NOT_FOUND", "NOT_OPEN", "USB", "TIMEOUT", "OVERFLOW", "OTHER"):
        assert issubclass(getattr(lib_err, name), lib_err.UHDError)
