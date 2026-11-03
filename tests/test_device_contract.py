"""What every hardware device must do, run against each one in tests/devices.py over a fake driver.

No hardware or driver library is needed. Device-specific behavior (gain stages, rates) is tested in
test_<device>_api.py.
"""
import numpy as np
import pytest

from pysdrlib import err
from pysdrlib.base import convert
from pysdrlib.base.native import NativeDevice

from devices import SAMPLES


def test_is_a_native_device(spec):
    cls = spec.device_class
    assert issubclass(cls, NativeDevice)
    assert isinstance(cls.SAMPLE_FORMAT, convert.SampleFormat)
    assert cls.NAME and cls.CONFIG is not None


class TestOpenClose:
    def test_open(self, sdr):
        assert sdr.state["open"]

    def test_not_found_maps_to_nodevice(self, make_sdr, spec):
        sdr = make_sdr(open_error=spec.lib.err.NOT_FOUND())
        with pytest.raises(err.NoDevice):
            sdr.open()
        assert not sdr.state["open"]

    def test_close(self, sdr):
        sdr.close()
        assert not sdr.state["open"] and sdr.device.names()[-1] == "close"

    def test_context_manager(self, make_sdr):
        with make_sdr() as sdr:
            assert sdr.state["open"]
        assert not sdr.state["open"]

    def test_everything_requires_open(self, make_sdr):
        sdr = make_sdr()
        for call in (sdr.set_freq, sdr.set_sample_rate, sdr.set_rx_gain, sdr.start_rx, sdr.get_samples):
            with pytest.raises(err.NotOpen):
                call()


class TestTuning:
    def test_set_freq_reaches_the_driver_as_int(self, sdr, spec):
        freq = spec.config.DEFAULT_FREQ
        assert sdr.set_freq(freq) == freq
        assert sdr.device.of("set_freq") == [(int(freq),)]
        assert isinstance(sdr.device.of("set_freq")[0][0], int)

    def test_freq_is_clamped_with_a_warning(self, sdr, spec):
        with pytest.warns(Warning):
            sdr.set_freq(spec.config.FREQ_MAX * 2)
        assert sdr.device.of("set_freq")[-1] == (int(spec.config.FREQ_MAX),)
        with pytest.warns(Warning):
            sdr.set_freq(spec.config.FREQ_MIN // 2)
        assert sdr.device.of("set_freq")[-1] == (int(spec.config.FREQ_MIN),)

    def test_defaults_when_none(self, sdr, spec):
        sdr.set_freq()
        sdr.set_sample_rate()
        assert sdr.device.of("set_freq")[-1] == (int(spec.config.DEFAULT_FREQ),)
        assert sdr.get_freq() == spec.config.DEFAULT_FREQ and sdr.get_sample_rate() == spec.config.DEFAULT_SAMPLE_RATE

    def test_sample_rate_reaches_the_driver(self, sdr, spec):
        assert sdr.set_sample_rate(spec.rate) == spec.rate
        assert sdr.Fs == spec.rate
        assert sdr.device.of("set_sample_rate")[-1][0] == spec.rate

    def test_sample_rate_is_clamped_with_a_warning(self, sdr, spec):
        with pytest.warns(Warning):
            sdr.set_sample_rate(1)
        assert sdr.Fs == spec.config.SAMPLE_RATE_MIN
        with pytest.warns(Warning):
            sdr.set_sample_rate(spec.config.SAMPLE_RATE_MAX * 2)
        assert sdr.Fs == spec.config.SAMPLE_RATE_MAX

    def test_sample_rate_resizes_the_buffer(self, sdr, spec):
        sdr.set_sample_rate(spec.rate)
        assert sdr.device.of("set_rx_buffer")[-1] == (int(type(sdr).BUFFER_SECONDS * spec.rate),)


class TestConfig:
    def test_defaults_are_within_limits(self, spec):
        c = spec.config
        assert c.FREQ_MIN <= c.DEFAULT_FREQ <= c.FREQ_MAX
        assert c.SAMPLE_RATE_MIN <= c.DEFAULT_SAMPLE_RATE <= c.SAMPLE_RATE_MAX
        assert c.SAMPLE_RATE_MIN <= spec.rate <= c.SAMPLE_RATE_MAX

    def test_rx_stages_are_declared(self, spec):
        assert spec.config.stages["RX"], "a receiver has at least one gain stage"

    def test_every_stage_has_a_setter_taking_its_default(self, sdr, spec):
        for direction in ("RX", "TX"):
            for name, stage in spec.config.stages[direction].items():
                setter = getattr(sdr, f"set_{direction.lower()}_{name.lower()}_gain")
                default = 0 if stage.default is None else stage.default
                assert setter() == stage.default or stage.default is None
                assert setter(default) == default

    def test_total_gain_covers_the_declared_stages(self, sdr, spec):
        out = sdr.set_rx_gain()
        assert {k.upper() for k in out} == set(spec.config.stages["RX"])

    def test_transmit_matches_the_declaration(self, sdr, spec):
        assert bool(spec.config.stages["TX"]) == type(sdr).CAN_TRANSMIT
        if not type(sdr).CAN_TRANSMIT:
            return
        assert set(sdr.set_tx_gain()) == {k.lower() for k in spec.config.stages["TX"]}


class TestRx:
    def test_start_queues_in_the_driver_before_streaming(self, sdr):
        """Samples are converted and queued by the extension's own thread, never by a Python callback"""
        sdr.start_rx()
        names = sdr.device.names()
        assert sdr.state["rx"] and names.index("set_rx_buffer") < names.index("reset_rx_stats") < names.index("start_rx")
        assert sdr.device.rx_callback is None and "set_rx_callback" not in names
        sdr.stop_rx()
        assert not sdr.state["rx"] and "stop_rx" in sdr.device.names()

    def test_start_sizes_the_buffer_for_the_rate(self, sdr, spec):
        sdr.set_sample_rate(spec.rate)
        sdr.start_rx()
        assert sdr.device.of("set_rx_buffer")[-1] == (int(type(sdr).BUFFER_SECONDS * spec.rate),)

    def test_samples_arrive_as_complex64(self, sdr, spec):
        sdr.set_sample_rate(spec.rate)
        sdr.start_rx()
        _, raw = spec.raw()
        sdr.device.deliver(raw)
        samples = sdr.get_samples()
        assert samples.dtype == np.complex64   # every device returns complex64, no complex128 intermediate
        np.testing.assert_allclose(samples, SAMPLES, rtol=0, atol=spec.atol or 1e-7)

    def test_only_the_valid_bytes_are_used(self, sdr, spec):
        sdr.set_sample_rate(spec.rate)
        sdr.start_rx()
        raw = np.concatenate([spec.raw()[1], spec.raw()[1]])
        sdr.device.deliver(raw, raw.nbytes // 2)
        np.testing.assert_allclose(sdr.get_samples(), SAMPLES, rtol=0, atol=spec.atol or 1e-7)

    def test_samples_are_consumed_by_reading(self, sdr, spec):
        sdr.set_sample_rate(spec.rate)
        sdr.start_rx()
        sdr.device.deliver(spec.raw()[1])
        assert len(sdr.get_samples()) == len(SAMPLES)
        assert len(sdr.get_samples()) == 0

    def test_instances_do_not_share_a_queue(self, make_sdr, spec):
        a, b = make_sdr(), make_sdr()
        a.open(); b.open()
        a.set_sample_rate(spec.rate); a.start_rx()
        a.device.deliver(spec.raw()[1])
        assert len(b.device.read_rx()) == 0 and len(a.get_samples()) == len(SAMPLES)


class FakeClock:
    def __init__(self):
        self.now = 1000.0
    def __call__(self):
        return self.now


@pytest.fixture
def rx(sdr, spec):
    """A receiving device whose watchdog runs on a fake clock: rx.clock.now += 2 is two seconds passing.
    rx.deliver(n) is the driver delivering n samples."""
    sdr.clock = FakeClock()
    sdr._watchdog._clock = sdr.clock
    sdr.device.clock = sdr.clock   # the fake driver's transfers are timed on it too
    sdr.set_sample_rate(spec.rate)
    sdr.start_rx()
    def deliver(n):
        raw = np.tile(spec.raw()[1], -(-n // len(SAMPLES)))[: n * 2]
        sdr.device.deliver(raw)
    sdr.deliver = deliver
    return sdr


class TestWatchdog:
    """Stalls and dropped samples are reported, not silently turned into empty reads"""

    def test_a_stalled_device_raises(self, rx):
        rx.deliver(100)
        rx.clock.now += 2
        assert len(rx.get_samples()) == 100   # what had arrived is returned first
        with pytest.raises(err.StreamStalled, match=type(rx).NAME):
            rx.get_samples()
        assert rx.stats.stalls == 1

    def test_keeps_raising_until_data_resumes(self, rx):
        rx.clock.now += 2
        for _ in range(3):
            with pytest.raises(err.StreamStalled):
                rx.get_samples()
        assert rx.stats.stalls == 1   # one stall, however often it is observed
        rx.deliver(100)
        assert len(rx.get_samples()) == 100

    def test_a_device_that_never_starts_stalls(self, rx):
        rx.clock.now += 2
        with pytest.raises(err.StreamStalled):
            rx.get_samples()

    def test_a_flowing_stream_does_not_raise(self, rx):
        for _ in range(10):
            rx.clock.now += 0.5
            rx.deliver(int(0.5 * rx.Fs))
            assert len(rx.get_samples()) == int(0.5 * rx.Fs)

    def test_timeout_is_configurable_and_can_be_disabled(self, rx):
        assert rx.stall_timeout == type(rx).STALL_TIMEOUT
        rx.stall_timeout = 5
        rx.clock.now += 2
        assert len(rx.get_samples()) == 0
        rx.stall_timeout = None
        rx.clock.now += 1000
        assert len(rx.get_samples()) == 0

    def test_not_watched_when_not_receiving(self, rx):
        rx.stop_rx()
        rx.clock.now += 1000
        assert len(rx.get_samples()) == 0 and rx.stats.age is None

    def test_start_rx_starts_counting_afresh(self, rx):
        rx.clock.now += 2
        with pytest.raises(err.StreamStalled):
            rx.get_samples()
        rx.stop_rx()
        rx.start_rx()
        assert rx.stats.samples == rx.stats.stalls == rx.stats.missing == rx.stats.dropped == 0
        rx.get_samples()

    def test_samples_not_read_in_time_are_reported_once(self, rx):
        rx.device.set_rx_buffer(8)
        rx.deliver(100)   # far more than the buffer holds
        with pytest.warns(Warning, match="dropped") as caught:
            rx.get_samples()
        assert len(caught) == 1 and rx.stats.dropped == 92
        rx.deliver(4)
        with warnings_as_errors():
            rx.get_samples()   # nothing new was lost

    def test_samples_the_device_failed_to_deliver_are_reported(self, rx):
        rx.deliver(1000)
        rx.clock.now += 0.5   # half a second at the sample rate, and only 1000 samples arrived
        with pytest.warns(Warning, match="fewer samples"):
            rx.get_samples()
        assert rx.stats.missing > 0.5 * rx.Fs * 0.5 * 0.8 and rx.stats.stalls == 0

    def test_a_continuing_shortfall_warns_once(self, rx):
        rx.deliver(1000)
        with pytest.warns(Warning, match="fewer samples") as caught:
            for _ in range(4):
                rx.clock.now += 0.25   # four short windows, still inside the stall timeout
                rx.get_samples()
        assert len(caught) == 1 and rx.stats.missing > 0

    def test_a_healthy_stream_warns_about_nothing(self, rx):
        with warnings_as_errors():
            for _ in range(20):
                rx.clock.now += 0.25
                rx.deliver(int(0.25 * rx.Fs))
                rx.get_samples()
        assert rx.stats.missing == rx.stats.dropped == rx.stats.stalls == 0

    def test_stats(self, rx):
        rx.deliver(1000)
        rx.clock.now += 0.1
        assert rx.stats.samples == 1000 and rx.stats.age == pytest.approx(0.1)


class warnings_as_errors:
    def __enter__(self):
        import warnings
        self._ctx = warnings.catch_warnings()
        self._ctx.__enter__()
        warnings.simplefilter("error")
    def __exit__(self, *exc):
        return self._ctx.__exit__(*exc)


@pytest.mark.hardware
def test_hardware_watchdog_never_hides_a_stall(spec, hardware):
    """However the real device behaves, a stream either keeps up with its rate or says it stalled"""
    import time
    stalled, received = False, 0
    with spec.device_class() as sdr:
        sdr.initialize(spec.config.DEFAULT_FREQ, spec.rate)
        start = time.perf_counter()
        sdr.start_rx()
        while time.perf_counter() - start < 3 and not stalled:
            time.sleep(0.05)
            try:
                received += len(sdr.get_samples())
            except err.StreamStalled:
                stalled = True
        elapsed = time.perf_counter() - start
        stats = sdr.stats
        sdr.stop_rx()
    if stalled:
        assert stats.stalls >= 1 and stats.age > sdr.stall_timeout
    else:
        assert received > 0.8 * spec.rate * (elapsed - 0.5), f"{received:,} samples in {elapsed:.1f}s without a stall report"


def test_double_start_and_stop_only_warn(sdr):
    sdr.start_rx()
    sdr.start_rx()
    sdr.stop_rx()
    sdr.stop_rx()
    assert sdr.device.names().count("start_rx") == 1


@pytest.mark.hardware
def test_hardware_rx_roundtrip(spec, hardware):
    """The real device through the whole Python stack: tune, stream, read complex64"""
    import time
    with spec.device_class() as sdr:
        sdr.initialize(spec.config.DEFAULT_FREQ, spec.rate)
        sdr.start_rx()
        time.sleep(0.4)
        samples = sdr.get_samples()
        sdr.stop_rx()
    assert samples.dtype == np.complex64 and len(samples) > 0
    assert np.isfinite(samples).all()
    assert max(np.abs(samples.real).max(), np.abs(samples.imag).max()) <= 1.0   # I and Q each, not the magnitude
