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

from reference import buffer_numpy, convert_numpy

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

    def test_callback_takes_precedence_over_the_buffer(self, dev):
        dev.set_rx_buffer(16)
        dev.set_rx_callback(lambda *a: 1)
        assert dev._simulate_rx(b"\0\0\0\0", 4, 4) == 1
        assert dev.rx_available == 0


def fmt_args(fmt):
    return fmt.kind, fmt.bits, fmt.storage, fmt.packed, fmt.byteorder


class TestBufferedRx:
    """The default receive path: the driver's thread converts to complex64 and queues, with no Python"""

    @pytest.fixture
    def dev(self, native):
        return native.device()

    @pytest.fixture
    def fmt(self, spec):
        return spec.device_class.SAMPLE_FORMAT

    def raw(self, fmt, n, seed=0):
        """n random complex samples, as the device delivers them"""
        iq = (np.random.default_rng(seed).uniform(-1, 1, 2 * n).astype(np.float32)).view(np.complex64)
        return fmt.from_complex64(iq)

    def test_nothing_is_queued_until_the_buffer_is_sized(self, dev, spec):
        assert dev._simulate_rx(spec.raw()[0], 4, 4) == -1   # as with no callback: stop streaming
        dev.set_rx_buffer(8)
        assert dev._simulate_rx(spec.raw()[0], len(spec.raw()[0]), 100) == 0

    def test_conversion_is_native_and_identical_to_the_numpy_reference(self, dev, spec, fmt):
        dev.set_rx_buffer(100_000)
        raw = self.raw(fmt, 4096)
        assert dev._simulate_rx(raw.tobytes(), raw.nbytes, raw.nbytes) == 0   # a native thread, no callback
        got = dev.read_rx()
        assert got.dtype == np.complex64
        np.testing.assert_array_equal(got, convert_numpy.SampleFormat(*fmt_args(fmt)).to_complex64(raw))

    def test_only_the_valid_bytes_are_converted(self, dev, spec, fmt):
        dev.set_rx_buffer(100)
        data, raw = spec.raw()
        dev._simulate_rx(data + data, len(data), 2 * len(data))
        np.testing.assert_allclose(dev.read_rx(), SAMPLES, rtol=0, atol=spec.atol or 1e-7)

    def test_a_partial_trailing_sample_is_ignored(self, dev, spec, fmt):
        dev.set_rx_buffer(100)
        data, _ = spec.raw()
        dev._simulate_rx(data + b"\x01", len(data) + 1, len(data) + 1)
        assert len(dev.read_rx()) == len(SAMPLES)

    def test_reading_clears_and_can_be_partial(self, dev, fmt):
        dev.set_rx_buffer(100)
        raw = self.raw(fmt, 10)
        dev._simulate_rx(raw.tobytes(), raw.nbytes, raw.nbytes)
        want = fmt.to_complex64(raw)
        assert dev.rx_available == 10
        np.testing.assert_array_equal(dev.read_rx(4), want[:4])
        np.testing.assert_array_equal(dev.read_rx(), want[4:])
        assert len(dev.read_rx()) == 0 and dev.rx_available == 0

    def test_the_oldest_samples_are_dropped_and_counted(self, dev, fmt):
        dev.set_rx_buffer(10)
        raw = self.raw(fmt, 15)
        want = fmt.to_complex64(raw)
        for chunk in (raw[: 2 * 6], raw[2 * 6:]):   # 6, then 9 more: 5 pending are over
            dev._simulate_rx(chunk.tobytes(), chunk.nbytes, chunk.nbytes)
        np.testing.assert_array_equal(dev.read_rx(), want[-10:])
        assert dev.rx_stats()["dropped"] == 5

    def test_one_transfer_bigger_than_the_buffer(self, dev, fmt):
        dev.set_rx_buffer(4)
        raw = self.raw(fmt, 10)
        dev._simulate_rx(raw.tobytes(), raw.nbytes, raw.nbytes)
        np.testing.assert_array_equal(dev.read_rx(), fmt.to_complex64(raw)[-4:])
        assert dev.rx_stats()["dropped"] == 6

    def test_a_zero_size_buffer_drops_everything(self, dev, fmt):
        dev.set_rx_buffer(0)
        raw = self.raw(fmt, 5)
        assert dev._simulate_rx(raw.tobytes(), raw.nbytes, raw.nbytes) == 0
        assert len(dev.read_rx()) == 0 and dev.rx_stats()["dropped"] == 5

    def test_resizing_discards_what_is_pending(self, dev, fmt):
        dev.set_rx_buffer(50)
        raw = self.raw(fmt, 5)
        dev._simulate_rx(raw.tobytes(), raw.nbytes, raw.nbytes)
        dev.set_rx_buffer(60)
        assert dev.rx_available == 0 and dev.rx_stats()["dropped"] == 0

    def test_matches_the_python_ring_for_any_sequence_of_transfers(self, dev, fmt):
        """The same drop-oldest semantics as the original numpy RotatingBuffer, wrap-around included"""
        rng = np.random.default_rng(7)
        ring = buffer_numpy.RotatingBuffer()
        ring.reset(64)
        dev.set_rx_buffer(64)
        for i in range(300):
            n = int(rng.integers(0, 150))
            raw = self.raw(fmt, n, seed=i)
            dev._dispatch_rx(raw)
            ring.update(fmt.to_complex64(raw))
            if rng.random() < 0.4:
                count = None if rng.random() < 0.5 else int(rng.integers(0, 80))
                np.testing.assert_array_equal(dev.read_rx(count), ring.read(count))
            assert dev.rx_available == ring.available and dev.rx_stats()["dropped"] == ring.dropped

    def test_stats(self, dev, fmt):
        dev.set_rx_buffer(1000)
        stats = dev.rx_stats()
        assert stats["samples"] == stats["calls"] == stats["max_chunk"] == 0 and stats["age"] is None
        for n in (10, 30, 20):
            raw = self.raw(fmt, n)
            dev._dispatch_rx(raw)
        stats = dev.rx_stats()
        assert (stats["samples"], stats["calls"], stats["max_chunk"], stats["pending"]) == (60, 3, 30, 60)
        assert 0 <= stats["age"] < 5 and stats["busy"] > 0
        time.sleep(0.05)
        assert dev.rx_stats()["age"] >= 0.05
        dev.reset_rx_stats()
        stats = dev.rx_stats()
        assert stats["samples"] == stats["calls"] == 0 and stats["age"] is None
        assert stats["pending"] == 60   # the samples themselves are kept

    def test_an_empty_transfer_still_counts_as_activity(self, dev):
        dev.set_rx_buffer(10)
        dev._dispatch_rx(b"")
        stats = dev.rx_stats()
        assert stats["calls"] == 1 and stats["samples"] == 0 and stats["age"] is not None

    def test_concurrent_producers_and_a_reader_lose_nothing(self, dev, fmt):
        dev.set_rx_buffer(2000)
        raw = self.raw(fmt, 100)
        pushed, got, done = [0], [0], threading.Event()
        def produce():
            for _ in range(200):
                dev._simulate_rx(raw.tobytes(), raw.nbytes, raw.nbytes)
        def consume():
            while not done.is_set():
                got[0] += len(dev.read_rx())
        threads = [threading.Thread(target=produce) for _ in range(3)]
        reader = threading.Thread(target=consume)
        reader.start()
        for t in threads: t.start()
        for t in threads: t.join()
        done.set()
        reader.join()
        got[0] += len(dev.read_rx())
        assert got[0] + dev.rx_stats()["dropped"] == 3 * 200 * 100   # every sample read or counted dropped
        assert dev.rx_stats()["samples"] == 3 * 200 * 100

    def test_the_python_device_reads_it_end_to_end(self, dev, spec):
        """NativeDevice over the real extension's queue: transfers in, complex64 out"""
        sdr = spec.device_class()
        sdr.device, sdr.state["open"] = dev, True
        sdr._rate_changed()
        data, _ = spec.raw()
        assert dev._simulate_rx(data, len(data), len(data)) == 0
        samples = sdr.get_samples()
        assert samples.dtype == np.complex64
        np.testing.assert_allclose(samples, SAMPLES, rtol=0, atol=spec.atol or 1e-7)
        assert len(sdr.get_samples()) == 0


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

    def test_buffered_stream_is_complex64(self, dev, spec):
        dev.set_rx_buffer(int(0.5 * spec.rate))
        dev.start_rx()
        time.sleep(0.4)
        dev.stop_rx()
        samples = dev.read_rx()
        stats = dev.rx_stats()
        assert samples.dtype == np.complex64 and len(samples) > 0 and np.isfinite(samples).all()
        assert stats["samples"] >= len(samples) and stats["calls"] > 0 and stats["dropped"] == 0

    def test_a_python_thread_hogging_the_gil_does_not_lose_samples(self, dev, spec):
        """The reason the conversion is native: the driver's thread must never wait for Python"""
        import random
        data = [random.random() for _ in range(300_000)]
        dev.set_rx_buffer(int(2 * spec.rate))
        dev.start_rx()
        time.sleep(0.3)   # let the stream start
        base, t0 = dev.rx_stats()["samples"], time.perf_counter()
        while time.perf_counter() - t0 < 2:
            sorted(data)   # holds the GIL for ~30 ms at a time, like a redraw
        got, elapsed = dev.rx_stats()["samples"] - base, time.perf_counter() - t0
        dev.stop_rx()
        assert got > 0.95 * spec.rate * elapsed, f"{got / elapsed / 1e6:.2f} MS/s of {spec.rate / 1e6:.2f}"

    def test_python_loop_keeps_the_gil_busy_while_streaming(self, dev):
        count = [0]
        dev.set_rx_callback(lambda *a: count.__setitem__(0, count[0] + 1) or 0)
        dev.start_rx()
        end = time.time() + 0.3
        while time.time() < end:
            sum(range(1000))
        dev.stop_rx()
        assert count[0] > 0
