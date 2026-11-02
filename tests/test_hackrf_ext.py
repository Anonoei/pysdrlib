"""Tests for the compiled pybind11 HackRF extension (pysdrlib.hackrf.lib._hackrf).

Needs the extension and libhackrf to be loadable, but not a device, except for
tests marked `hardware`.
"""
import gc
import os
import threading
import time

import numpy as np
import pytest

lib = pytest.importorskip("pysdrlib.hackrf.lib")
from pysdrlib.hackrf.lib import err as lib_err

hackrf = lib.hackrf
HW = os.environ.get("PYSDRLIB_HW") == "1"
needs_hw = [pytest.mark.hardware, pytest.mark.skipif(not HW, reason="set PYSDRLIB_HW=1 with a HackRF attached")]


@pytest.fixture(scope="module", autouse=True)
def _init():
    hackrf.init()


def test_library_version():
    assert isinstance(hackrf.library_version(), str) and hackrf.library_version()
    assert isinstance(hackrf.library_release(), str)


def test_init_is_repeatable():
    hackrf.init()
    hackrf.init()


def test_native_and_python_agree_on_sample_format():
    """The C++ driver's declared layout must match what the Python side converts"""
    from pysdrlib.hackrf import hackrf as py_hackrf
    fmt = py_hackrf.HackRF.SAMPLE_FORMAT
    assert hackrf.device().rx_sample_format() == (fmt.kind, fmt.bits, fmt.storage, fmt.packed)


def test_callback_array_dtype_follows_declared_format():
    from pysdrlib.hackrf import hackrf as py_hackrf
    seen = []
    dev = hackrf.device()
    dev.set_rx_callback(lambda a, v, n: seen.append(a.dtype) or 0)
    dev._simulate_rx(bytes(8), 8, 8)
    assert seen == [py_hackrf.HackRF.SAMPLE_FORMAT.dtype]


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


def test_closed_device_raises_instead_of_crashing():
    dev = hackrf.device()
    assert not dev.is_open
    for call in (dev.start_rx, dev.stop_rx, lambda: dev.set_freq(100_000_000),
                 lambda: dev.set_lna_gain(8), dev.is_streaming):
        with pytest.raises(lib_err.HackRFError):
            call()
    dev.close()  # closing a closed device is fine


# --- callback marshalling (drives the real native streaming path) ---------- #
class TestCallback:
    def test_receives_int8_array_and_lengths(self):
        seen = {}

        def cb(buf, valid, buflen):
            seen.update(dtype=buf.dtype, n=len(buf), valid=valid, buflen=buflen,
                        data=buf.copy(), thread=threading.get_ident())
            return 0

        dev = hackrf.device()
        dev.set_rx_callback(cb)
        raw = bytes([0, 1, 127, 128, 255, 2, 3, 4])  # 128 -> -128, 255 -> -1
        assert dev._simulate_rx(raw, 8, 16) == 0
        assert seen["dtype"] == np.int8
        assert seen["n"] == seen["valid"] == 8
        assert seen["buflen"] == 16
        np.testing.assert_array_equal(seen["data"], np.frombuffer(raw, dtype=np.int8))
        assert seen["thread"] != threading.get_ident()  # ran on a native thread

    def test_return_value_propagates(self):
        dev = hackrf.device()
        dev.set_rx_callback(lambda *a: 1)
        assert dev._simulate_rx(b"\0\0", 2, 2) == 1

    def test_none_return_means_continue(self):
        dev = hackrf.device()
        dev.set_rx_callback(lambda *a: None)
        assert dev._simulate_rx(b"\0\0", 2, 2) == 0

    @pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
    def test_exception_stops_stream_without_crashing(self):
        def boom(*a):
            raise ValueError("boom")
        dev = hackrf.device()
        dev.set_rx_callback(boom)
        assert dev._simulate_rx(b"\0\0", 2, 2) == -1

    def test_no_callback_stops_stream(self):
        assert hackrf.device()._simulate_rx(b"\0\0", 2, 2) == -1

    def test_only_valid_bytes_are_passed(self):
        got = []
        dev = hackrf.device()
        dev.set_rx_callback(lambda b, v, n: got.append(len(b)) or 0)
        dev._simulate_rx(bytes(16), 4, 16)
        assert got == [4]

    def test_callback_released_on_gc(self):
        class Probe:
            def __call__(self, *a): return 0
        probe = Probe()
        import weakref
        ref = weakref.ref(probe)
        dev = hackrf.device()
        dev.set_rx_callback(probe)
        del probe
        assert ref() is not None  # device keeps it alive
        del dev
        gc.collect()
        assert ref() is None

    def test_hackrf_module_callback_end_to_end(self):
        """The real HackRF.rx_callback fed through the native path."""
        from pysdrlib.hackrf import hackrf as py_hackrf
        py_hackrf.cb.reset(16)
        dev = hackrf.device()
        dev.set_rx_callback(py_hackrf.rx_callback)
        raw = np.array([64, -64, 0, 32], dtype=np.int8).tobytes()
        assert dev._simulate_rx(raw, 4, 4) == 0
        np.testing.assert_allclose(py_hackrf.cb.samples, np.array([0.5 - 0.5j, 0 + 0.25j]))


# --- real hardware ---------------------------------------------------------- #
@pytest.mark.hardware
@pytest.mark.skipif(not HW, reason="set PYSDRLIB_HW=1 with a HackRF attached")
class TestHardware:
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
        d1 = hackrf.open_by_serial(devs.serial_numbers[0])
        d1.close()
        d2 = hackrf.device_list_open(devs, 0)
        d2.close()

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
            dev.set_freq(100_000_000)
            dev.set_sample_rate(10_000_000)
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

    def test_stream_delivers_data(self):
        chunks = []
        lock = threading.Lock()

        def cb(buf, valid, buflen):
            with lock:
                chunks.append(len(buf))
            return 0

        dev = hackrf.open()
        try:
            dev.set_sample_rate(10_000_000)
            dev.set_freq(100_000_000)
            dev.set_rx_callback(cb)
            dev.start_rx()
            assert dev.is_streaming()
            time.sleep(0.5)
            dev.stop_rx()
            assert not dev.is_streaming()
        finally:
            dev.close()
        total = sum(chunks)
        # 10 MS/s * 2 bytes * 0.5 s = 10 MB; allow generous slack
        assert total > 2_000_000

    def test_callback_return_nonzero_stops_stream(self):
        dev = hackrf.open()
        try:
            dev.set_sample_rate(10_000_000)
            dev.set_rx_callback(lambda *a: 1)
            dev.start_rx()
            time.sleep(0.3)
            assert not dev.is_streaming()
        finally:
            dev.close()

    def test_python_loop_reads_while_streaming(self):
        """Main thread keeps the GIL busy while the native thread calls back."""
        count = [0]

        def cb(buf, valid, buflen):
            count[0] += 1
            return 0

        dev = hackrf.open()
        try:
            dev.set_sample_rate(10_000_000)
            dev.set_rx_callback(cb)
            dev.start_rx()
            end = time.time() + 2
            x = 0
            while time.time() < end:
                x += sum(range(1000))
            dev.stop_rx()
        finally:
            dev.close()
        assert count[0] > 50

    def test_restart_cycles(self):
        dev = hackrf.open()
        try:
            dev.set_sample_rate(10_000_000)
            dev.set_rx_callback(lambda *a: 0)
            for _ in range(5):
                dev.start_rx()
                time.sleep(0.1)
                dev.stop_rx()
        finally:
            dev.close()

    def test_gc_while_streaming_does_not_deadlock(self):
        dev = hackrf.open()
        dev.set_sample_rate(10_000_000)
        dev.set_rx_callback(lambda *a: 0)
        dev.start_rx()
        time.sleep(0.2)
        del dev
        gc.collect()
        # device must be reopenable afterwards
        d2 = hackrf.open()
        d2.close()

    def test_close_while_streaming(self):
        dev = hackrf.open()
        dev.set_sample_rate(10_000_000)
        dev.set_rx_callback(lambda *a: 0)
        dev.start_rx()
        time.sleep(0.2)
        dev.close()
        assert not dev.is_open
