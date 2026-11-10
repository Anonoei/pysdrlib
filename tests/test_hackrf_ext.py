"""HackRF's own extension behaviour (pysdrlib.hackrf.lib._hackrf). What every extension does is in
test_native_contract.py. Needs the extension and libhackrf, but no device, except for the `hardware` tests.
"""
import time

import pytest

from devices import BY_NAME, HW

pytest.importorskip("pysdrlib.hackrf", exc_type=ImportError)
from pysdrlib.hackrf import lib
from pysdrlib.hackrf.lib import err as lib_err

hackrf = lib.hackrf


@pytest.fixture
def spec():
    return BY_NAME["hackrf"]


@pytest.fixture(scope="module", autouse=True)
def _init():
    hackrf.init()


def test_library_version():
    assert isinstance(hackrf.library_version(), str) and hackrf.library_version()
    assert isinstance(hackrf.library_release(), str)


def test_init_is_repeatable():
    hackrf.init()
    hackrf.init()


def test_constants():
    assert hackrf.BYTES_PER_BLOCK == 2 * hackrf.SAMPLES_PER_BLOCK


def test_err_enum_values_match_libhackrf():
    assert hackrf.ERR.SUCCESS.value == 0
    assert hackrf.ERR.NOT_FOUND.value == -5
    assert hackrf.ERR.OTHER.value == -9999


def test_rf_path_filter_enum():
    assert {m.name for m in hackrf.RF_PATH_FILTER.__members__.values()} == {"BYPASS", "LOW_PASS", "HIGH_PASS"}


@pytest.mark.skipif(len(hackrf.device_list()) > 0, reason="a device is attached")
def test_open_without_device_raises_not_found():
    with pytest.raises(lib_err.NOT_FOUND):
        hackrf.open()


def test_open_unknown_serial_raises_not_found():
    with pytest.raises(lib_err.NOT_FOUND):
        hackrf.open_by_serial("does-not-exist")


def test_errors_are_hackrf_errors():
    with pytest.raises(lib_err.HackRFError):
        hackrf.open_by_serial("does-not-exist")


def test_device_list_matches_hardware():
    devs = hackrf.device_list()
    assert len(devs) == devs.device_count == len(devs.serial_numbers) == len(devs.usb_board_ids)
    devs.close()
    devs.close()  # idempotent
    assert len(devs) == 0


def test_closed_device_gain_setters_raise():
    with pytest.raises(lib_err.HackRFError):
        hackrf.device().set_lna_gain(8)


@pytest.mark.hardware
@pytest.mark.skipif(not HW, reason="set PYSDRLIB_HW=1 with a HackRF attached")
class TestHardware:
    @pytest.fixture(autouse=True)
    def _attached(self):
        if not len(hackrf.device_list()):
            pytest.skip("no HackRF attached")

    def test_info(self):
        dev = hackrf.open()
        try:
            assert hackrf.board_id_read(dev) in (0, 1, 2, 3, 4, 5)
            assert hackrf.version_string_read(dev)
            part, serial = hackrf.board_partid_serialno_read(dev)
            assert serial in hackrf.device_list().serial_numbers
            assert hackrf.usb_api_version_read(dev).count(".") == 1
        finally:
            dev.close()

    def test_open_by_serial_and_list(self):
        devs = hackrf.device_list()
        assert len(devs) >= 1
        hackrf.open_by_serial(devs.serial_numbers[0]).close()
        hackrf.device_list_open(devs, 0).close()

    def test_second_open_is_busy(self):
        dev = hackrf.open()
        try:
            with pytest.raises(lib_err.HackRFError):
                hackrf.open()
        finally:
            dev.close()

    def test_every_setter(self):
        dev = hackrf.open()
        try:
            dev.set_rx_freq(100_000_000)
            dev.set_rx_sample_rate(10_000_000)
            dev.set_baseband_filter_bandwidth(8_000_000)
            for amp in (True, False):
                dev.set_rx_rf_gain(amp)
                dev.set_tx_rf_gain(amp)
                dev.set_amp_enable(amp)
            for g in (0, 8, 16, 24, 32, 40):
                dev.set_rx_if_gain(g)
                dev.set_lna_gain(g)
            for g in (0, 2, 30, 62):
                dev.set_rx_bb_gain(g)
                dev.set_vga_gain(g)
            for g in (0, 20, 47):
                dev.set_tx_if_gain(g)
                dev.set_txvga_gain(g)
            with pytest.raises(lib_err.INVALID_PARAM):
                dev.set_rx_if_gain(100)  # out of range (libhackrf rounds 7 down, rejects >40)
            with pytest.raises(lib_err.INVALID_PARAM):
                dev.set_rx_bb_gain(100)
        finally:
            dev.close()

    def test_stream_rate(self):
        """10 MS/s of int8 I/Q is 20 MB/s"""
        got = [0]
        dev = hackrf.open()
        try:
            dev.set_rx_sample_rate(10_000_000)
            dev.set_rx_freq(100_000_000)
            dev.set_rx_callback(lambda buf, valid, buflen: got.__setitem__(0, got[0] + len(buf)) or 0)
            dev.start_rx()
            time.sleep(0.5)
            dev.stop_rx()
        finally:
            dev.close()
        assert got[0] > 2_000_000

    def test_callback_return_nonzero_stops_the_stream(self):
        dev = hackrf.open()
        try:
            dev.set_rx_sample_rate(10_000_000)
            dev.set_rx_callback(lambda *a: 1)
            dev.start_rx()
            time.sleep(0.3)
            assert not dev.is_streaming()
        finally:
            dev.close()

    def test_transmit_silence(self):
        """Transmits zeros at minimum gain with the amp off, so nothing meaningful is radiated"""
        import numpy as np
        dev = hackrf.open()
        try:
            dev.set_rx_sample_rate(2_000_000)
            dev.set_rx_freq(915_000_000)
            dev.set_tx_rf_gain(False)
            dev.set_tx_if_gain(0)
            dev.set_tx_buffer(500_000)
            dev.start_tx()
            assert dev.is_transmitting()
            assert dev.write_tx(np.zeros(400_000, np.complex64), 5.0) == 400_000
            time.sleep(0.3)
            stats = dev.tx_stats()
            dev.stop_tx()
        finally:
            dev.close()
        assert stats["written"] == 400_000 and stats["sent"] > 0
        assert not dev.tx_error()
