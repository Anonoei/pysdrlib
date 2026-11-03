"""What every compiled extension (pysdrlib.<device>.lib) must do, run against each one in tests/devices.py.

The streaming path is driven through the extensions' `_simulate_rx` hook, so most of this needs the
extension and its vendor library to load but no hardware. The `hardware` tests need PYSDRLIB_HW=1 and
a device attached, and skip per device otherwise.
"""
import gc
import threading
import time
import weakref

import numpy as np
import pytest

from devices import SAMPLES, SPECS


@pytest.fixture
def native(spec):
    return spec.native


@pytest.fixture
def lib_err(spec):
    return spec.lib.err


def test_native_and_python_agree_on_sample_format(spec, native):
    """The C++ driver's declared layout must match what the Python side converts"""
    fmt = spec.device_class.SAMPLE_FORMAT
    assert native.device().rx_sample_format() == (fmt.kind, fmt.bits, fmt.storage, fmt.packed)


def test_errors_derive_from_one_base(spec, lib_err):
    bases = [c for c in vars(lib_err).values() if isinstance(c, type) and issubclass(c, Exception)]
    root = next(c for c in bases if c.__bases__ == (Exception,))
    assert all(issubclass(c, root) for c in bases)


class TestClosedDevice:
    def test_raises_instead_of_crashing(self, spec, native, lib_err):
        dev = native.device()
        assert not dev.is_open and not dev.is_streaming()
        for name, *args in spec.closed_calls:
            with pytest.raises(Exception) as exc:
                getattr(dev, name)(*args)
            assert type(exc.value).__module__ == lib_err.__name__, (name, exc.value)

    def test_closing_twice_is_fine(self, native):
        dev = native.device()
        dev.close()
        dev.close()

    def test_collecting_one_is_fine(self, native):
        dev = native.device()
        del dev
        gc.collect()


def test_errors_stay_with_their_own_module():
    """Every driver throws the same C++ error type; each module must translate only its own"""
    opened = [(s, s.native) for s in SPECS]
    for spec, native in opened:
        with pytest.raises(Exception) as exc:
            native.device().start_rx()
        assert type(exc.value).__module__ == spec.lib.err.__name__


class TestCallback:
    """The path from a driver's streaming thread to Python, without hardware"""

    @pytest.fixture
    def dev(self, native):
        return native.device()

    def test_receives_the_native_dtype_and_lengths(self, dev, spec):
        seen = {}
        data, raw = spec.raw()

        def cb(buf, valid, buflen):
            seen.update(dtype=buf.dtype, data=buf.copy(), valid=valid, buflen=buflen, thread=threading.get_ident())
            return 0

        dev.set_rx_callback(cb)
        assert dev._simulate_rx(data, len(data), 2 * len(data)) == 0
        assert seen["dtype"] == spec.device_class.SAMPLE_FORMAT.dtype
        assert seen["valid"] == len(data) and seen["buflen"] == 2 * len(data)
        np.testing.assert_array_equal(seen["data"], raw)
        assert seen["thread"] != threading.get_ident()   # ran on a native thread

    def test_only_the_valid_bytes_are_passed(self, dev, spec):
        got = []
        data, raw = spec.raw()
        dev.set_rx_callback(lambda b, v, n: got.append(b.nbytes) or 0)
        dev._simulate_rx(data, raw.dtype.itemsize * 2, len(data))
        assert got == [raw.dtype.itemsize * 2]

    def test_valid_longer_than_the_data_is_rejected(self, dev):
        dev.set_rx_callback(lambda *a: 0)
        with pytest.raises(ValueError):
            dev._simulate_rx(b"\0\0", 100, 100)

    def test_return_value_propagates(self, dev):
        dev.set_rx_callback(lambda *a: 1)
        assert dev._simulate_rx(b"\0\0\0\0", 4, 4) == 1

    def test_none_means_continue(self, dev):
        dev.set_rx_callback(lambda *a: None)
        assert dev._simulate_rx(b"\0\0\0\0", 4, 4) == 0

    @pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
    def test_exception_stops_the_stream_without_crashing(self, dev):
        def boom(*a):
            raise ValueError("boom")
        dev.set_rx_callback(boom)
        assert dev._simulate_rx(b"\0\0\0\0", 4, 4) == -1

    def test_no_callback_stops_the_stream(self, dev):
        assert dev._simulate_rx(b"\0\0\0\0", 4, 4) == -1

    def test_callback_is_released_with_the_device(self, native):
        class Probe:
            def __call__(self, *a): return 0
        probe = Probe()
        ref = weakref.ref(probe)
        dev = native.device()
        dev.set_rx_callback(probe)
        del probe
        assert ref() is not None   # the device keeps it alive
        del dev
        gc.collect()
        assert ref() is None

    def test_python_device_end_to_end(self, dev, spec):
        """The device class's own callback, fed through the native path, ends up in its buffer"""
        sdr = spec.device_class()
        sdr._buffer.reset(16)
        dev.set_rx_callback(sdr._rx_callback)
        data, _ = spec.raw()
        assert dev._simulate_rx(data, len(data), len(data)) == 0
        np.testing.assert_allclose(sdr._buffer.samples, SAMPLES)


@pytest.mark.hardware
class TestHardware:
    """Needs the device. Anything specific to one device is in test_<device>_ext.py."""

    @pytest.fixture
    def dev(self, spec, hardware):
        dev = hardware.open()
        spec.configure(dev)
        yield dev
        dev.close()

    def stream(self, dev, seconds=0.3):
        """Stream briefly; return the sizes (bytes) of the chunks delivered"""
        sizes = []
        dev.set_rx_callback(lambda buf, valid, buflen: sizes.append(buf.nbytes) or 0)
        dev.start_rx()
        assert dev.is_streaming()
        time.sleep(seconds)
        dev.stop_rx()
        assert not dev.is_streaming()
        return sizes

    def test_open_and_close(self, dev):
        assert dev.is_open
        dev.close()
        assert not dev.is_open and not dev.is_streaming()

    def test_stream_delivers_data(self, dev, spec):
        sizes = self.stream(dev)
        itemsize = spec.device_class.SAMPLE_FORMAT.dtype.itemsize
        assert sizes and all(s % (2 * itemsize) == 0 for s in sizes)   # whole I/Q pairs

    def test_restart_cycles(self, dev):
        for _ in range(3):
            assert self.stream(dev, 0.15)

    def test_close_while_streaming(self, dev):
        dev.set_rx_callback(lambda *a: 0)
        dev.start_rx()
        time.sleep(0.2)
        dev.close()
        assert not dev.is_open and not dev.is_streaming()

    def test_collecting_while_streaming_does_not_deadlock(self, spec, hardware):
        def run():
            dev = hardware.open()
            spec.configure(dev)
            dev.set_rx_callback(lambda *a: 0)
            dev.start_rx()
            time.sleep(0.2)
            del dev
            gc.collect()
        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(30)
        assert not t.is_alive(), "deadlock releasing a streaming device"
        hardware.open().close()   # and the device is usable afterwards

    @pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
    def test_a_raising_callback_does_not_crash(self, dev):
        def boom(*a):
            raise RuntimeError("boom")
        dev.set_rx_callback(boom)
        dev.start_rx()
        time.sleep(0.2)
        dev.close()

    def test_python_loop_keeps_the_gil_busy_while_streaming(self, dev):
        count = [0]
        dev.set_rx_callback(lambda *a: count.__setitem__(0, count[0] + 1) or 0)
        dev.start_rx()
        end = time.time() + 0.3
        while time.time() < end:
            sum(range(1000))
        dev.stop_rx()
        assert count[0] > 0
