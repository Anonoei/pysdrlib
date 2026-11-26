"""Tests for the simulated device.

The simulated device exercises pysdrlib's generic layers (Device base class, driver-style
callback, conversion, RotatingBuffer) with no hardware.
"""
import time

import numpy as np
import pytest

import pysdrlib
from pysdrlib import err
from pysdrlib.base import convert
from pysdrlib.sim import Sim

ConfigSim = Sim.CAPS


@pytest.fixture
def sim():
    dev = Sim(realtime=False, block_samples=4096)
    dev.open()
    dev.initialize(cf=100e6, Fs=1e6)
    yield dev
    if dev.state["rx"]:
        dev.stop_rx()
    if dev.state["open"]:
        dev.close()


def tone_hz(samples, fs):
    return np.fft.fftfreq(len(samples), 1 / fs)[np.argmax(np.abs(np.fft.fft(samples)))]


class TestRegistration:
    def test_listed_and_creatable(self):
        assert "sim" in pysdrlib.devices.ls()
        dev = pysdrlib.device("sim")
        assert isinstance(dev, Sim)
        assert dev.NAME == "Simulated" and not dev.CAN_TRANSMIT


class TestLifecycle:
    def test_requires_open(self):
        dev = Sim()
        for call in (dev.start_rx, dev.get_samples, lambda: dev.set_rx_freq(1e6), dev.close):
            with pytest.raises(err.NotOpen):
                call()

    def test_open_close(self):
        dev = Sim()
        dev.open()
        assert dev.state["open"]
        dev.close()
        assert not dev.state["open"]

    def test_context_manager_stops_streaming(self):
        with Sim(realtime=False) as dev:
            dev.start_rx()
            assert dev._thread.is_alive()
            thread = dev._thread
        assert not thread.is_alive()
        assert not dev.state["rx"]

    def test_initialize_without_transmit(self):
        dev = Sim()
        dev.open()
        dev.initialize()  # must not try to set TX gain
        assert dev.CAN_TRANSMIT is False
        assert dev.get_rx_freq() == ConfigSim.default_freq
        assert dev.get_rx_sample_rate() == ConfigSim.default_rate

    def test_start_stop_restart(self, sim):
        for _ in range(3):
            sim.start_rx()
            time.sleep(0.02)
            sim.stop_rx()
            assert not sim._thread
        assert sim.blocks > 0

    def test_double_start_stop_are_harmless(self, sim):
        sim.start_rx()
        sim.start_rx()
        sim.stop_rx()
        sim.stop_rx()
        assert not sim.state["rx"]

    def test_stream_thread_exits_when_stopped(self, sim):
        sim.start_rx()
        thread = sim._thread
        sim.stop_rx()
        assert not thread.is_alive()


class TestSignal:
    def test_samples_are_complex64_in_range(self, sim):
        sim.pump(3)
        x = sim.get_samples()
        assert x.dtype == np.complex64
        assert len(x) == 3 * 4096
        assert np.abs(x.real).max() <= 1 and np.abs(x.imag).max() <= 1

    def test_default_tone_is_fs_over_8(self, sim):
        sim.pump(4)
        assert tone_hz(sim.get_samples(), 1e6) == pytest.approx(125_000, abs=1e6 / (4 * 4096))

    def test_set_tone(self, sim):
        sim.set_tone(-200_000)
        sim.pump(4)
        assert tone_hz(sim.get_samples(), 1e6) == pytest.approx(sim.get_tone(), abs=300)
        assert sim.get_tone() == pytest.approx(-200_000, abs=1e6 / 4096)

    def test_tone_follows_sample_rate(self, sim):
        sim.set_rx_sample_rate(2e6)
        sim.pump(4)
        assert tone_hz(sim.get_samples(), 2e6) == pytest.approx(250_000, abs=600)

    def test_phase_continuous_across_blocks(self, sim):
        """Back-to-back blocks join without a glitch (whole cycles per block)."""
        sim.pump(3)
        x = sim.get_samples()
        step = np.angle(x[1:] / x[:-1])
        assert np.ptp(step) < 0.05  # constant phase advance everywhere, including block seams

    def test_threaded_stream_matches_pump(self, sim):
        sim.pump(2)
        expected = sim.get_samples()[:4096]
        sim.start_rx()
        time.sleep(0.05)
        sim.stop_rx()
        np.testing.assert_array_equal(sim.get_samples()[:4096], expected)


class TestBuffering:
    def test_keeping_up_loses_nothing(self):
        # Paced to the sample rate, so "keeping up" doesn't depend on the host's sleep granularity (15 ms on Windows)
        sim = Sim(realtime=True, block_samples=4096)
        sim.open()
        sim.initialize(cf=100e6, Fs=1e6)
        got = 0
        sim.start_rx()
        deadline = time.time() + 0.3
        while time.time() < deadline:
            got += len(sim.get_samples())
            time.sleep(0.002)
        sim.stop_rx()
        got += len(sim.get_samples())
        assert sim.dropped == 0
        assert got == sim.blocks * 4096
        sim.close()

    def test_falling_behind_drops_oldest_and_counts_it(self):
        dev = Sim(realtime=False, block_samples=4096)
        dev.open()
        dev.initialize(Fs=8192)  # buffer holds 0.5 s = 4096 samples, i.e. one block
        dev.pump(5)
        assert dev.dropped == 4 * 4096
        assert len(dev.get_samples()) == 4096
        dev.close()

    def test_pump_while_streaming_rejected(self, sim):
        sim.start_rx()
        with pytest.raises(RuntimeError):
            sim.pump()
        sim.stop_rx()

    def test_reset_stats(self, sim):
        sim.pump(2)
        sim.reset_stats()
        assert sim.blocks == 0 and sim.dropped == 0


class TestRealtime:
    def test_paced_to_sample_rate(self):
        dev = Sim(realtime=True, block_samples=10_000)
        dev.open()
        dev.initialize(Fs=1e6)   # 100 blocks/s
        dev.start_rx()
        time.sleep(0.5)
        dev.stop_rx()
        assert 35 <= dev.blocks <= 65, dev.blocks  # ~50, generous for scheduler jitter
        dev.close()

    def test_unpaced_runs_faster_than_real_time(self):
        dev = Sim(realtime=False, block_samples=10_000)
        dev.open()
        dev.initialize(Fs=1e6)
        dev.start_rx()
        time.sleep(0.2)
        dev.stop_rx()
        assert dev.blocks > 100  # >> the 20 a real-time 1 MS/s stream would deliver
        dev.close()

    def test_open_takes_realtime(self):
        dev = pysdrlib.open("sim", realtime=False)
        try:
            assert dev.realtime is False
        finally:
            dev.close()


class TestGain:
    def test_generic_gain_splits_across_stages(self, sim):
        assert sim.set_rx_gain_db(60) == 60 and sim.get_rx_gains_db() == {"IF": 40, "BB": 20}   # the total back; the stages by name
        assert sim._gain == {"if": 40, "bb": 20}

    def test_generic_default(self, sim):
        want = {"IF": ConfigSim.stage("rx", "IF").default, "BB": ConfigSim.stage("rx", "BB").default}
        assert sim.set_rx_gain_db() == sum(want.values()) and sim.get_rx_gains_db() == want

    def test_device_specific_stages(self, sim):
        assert sim.set_rx_if_gain(12) == 12
        assert sim.set_rx_bb_gain(30) == 30
        assert sim._gain == {"if": 12, "bb": 30}

    def test_stage_not_on_device(self, sim):
        assert not hasattr(sim, "set_rx_rf_gain")          # no such control on this device
        with pytest.raises(NotImplementedError):
            sim._set_gain_stage("RX", "RF", 1, lambda g: None)

    def test_out_of_range_is_clamped(self, sim):
        with pytest.warns(Warning):
            assert sim.set_rx_if_gain(100) == ConfigSim.stage("rx", "IF").max

    def test_a_fraction_rounds_to_a_step_quietly(self, sim):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            sim.set_rx_gain(0.5)   # 51 dB: IF 40 and BB 11, which rounds to BB's 2 dB step
        assert sim.get_rx_gains_db() == {"IF": 40, "BB": 10}
        assert not sim._quiet_gain_rounding
        sim.strict = True
        sim.set_rx_gain(0.5)       # (rounding a fraction is never an invalid value)
        sim.strict = False
        with pytest.warns(Warning, match="multiple of 2"):
            sim.set_rx_gain_db(51)   # decibels asked for that can't be set: still said

    def test_freq_and_rate_clamped(self, sim):
        with pytest.warns(Warning):
            sim.set_rx_sample_rate(1e12)
        assert sim.get_rx_sample_rate() == ConfigSim.rates[0].hi


class TestSampleFormats:
    """The same generic pipeline works for any raw layout a device might deliver"""
    FORMATS = [convert.INT8, convert.UINT8, convert.INT16, convert.INT12, convert.INT12_PACKED, convert.FLOAT32]

    @pytest.mark.parametrize("fmt", FORMATS, ids=str)
    def test_tone_survives_every_layout(self, fmt):
        dev = Sim(realtime=False, block_samples=4096, sample_format=fmt)
        dev.open()
        dev.initialize(Fs=1e6)
        dev.pump(4)
        x = dev.get_samples()
        assert x.dtype == np.complex64 and len(x) == 4 * 4096
        assert tone_hz(x, 1e6) == pytest.approx(125_000, abs=300)
        assert np.abs(x).max() == pytest.approx(0.5, abs=0.01)   # amplitude 0.5 in every format
        dev.close()

    @pytest.mark.parametrize("fmt", FORMATS, ids=str)
    def test_raw_block_size_matches_format(self, fmt):
        dev = Sim(realtime=False, block_samples=1000, sample_format=fmt)
        assert dev._raw.nbytes == 1000 * fmt.bytes_per_sample
        assert dev.SAMPLE_FORMAT is fmt

    @pytest.mark.parametrize("fmt", FORMATS, ids=str)
    def test_threaded_stream(self, fmt):
        dev = Sim(realtime=True, block_samples=4096, sample_format=fmt)   # paced: no ring overflow
        dev.open()
        dev.initialize(Fs=1e6)
        dev.start_rx()
        time.sleep(0.05)
        dev.stop_rx()
        assert dev.blocks > 0 and dev.dropped == 0
        assert len(dev.get_samples()) == dev.blocks * 4096
        dev.close()

    def test_default_is_int8(self):
        assert Sim().SAMPLE_FORMAT == convert.INT8


def _peak_db(x, fs, offset):
    """dBFS of the spectrum's bin at `offset` Hz (a tone of amplitude a reads 20*log10(a))"""
    spec = np.fft.fft(x) / len(x)
    k = int(round(offset / fs * len(x))) % len(x)
    return 20 * np.log10(np.abs(spec[k]) + 1e-12)


class TestBuiltSignal:
    """The signal built from parts (tones, noise, bursts), and the stream misbehaving on demand"""

    def make(self, **kwargs):
        dev = Sim(realtime=False, block_samples=4096, **kwargs)
        dev.open()
        dev.initialize(cf=100e6, Fs=1e6)
        return dev

    def test_tones_and_noise(self):
        dev = self.make(tones=[(125_000, -20), (-250_000, -10)], noise=-30, seed=1)   # above int8's ~-50 dBFS quantization floor
        dev.pump(4)
        x = dev.get_samples()
        assert _peak_db(x, 1e6, 125_000) == pytest.approx(-20, abs=0.5)
        assert _peak_db(x, 1e6, -250_000) == pytest.approx(-10, abs=0.5)
        rest = np.delete(np.abs(np.fft.fft(x) / len(x)) ** 2, [round(0.125 * len(x)), round(0.75 * len(x))])
        assert 10 * np.log10(rest.sum()) == pytest.approx(-30, abs=1)   # the noise floor's total power
        dev.close()

    def test_rf_tone_moves_with_retune(self):
        dev = self.make(tones=[{"freq": 100.2e6, "level": -6}])
        dev.pump(2)
        assert tone_hz(dev.get_samples(), 1e6) == pytest.approx(200_000, abs=300)
        dev.set_rx_freq(100.1e6)
        dev.pump(2)
        assert tone_hz(dev.get_samples(), 1e6) == pytest.approx(100_000, abs=300)
        dev.close()

    def test_retune_delay_keeps_old_samples(self):
        dev = self.make(tones=[{"freq": 100.2e6, "level": -6}], retune_delay=0.01)   # 10,000 samples at 1 MS/s
        dev.pump(1)
        dev.get_samples()
        dev.set_rx_freq(100.1e6)
        dev.pump(4)
        x = dev.get_samples()
        assert tone_hz(x[:8192], 1e6) == pytest.approx(200_000, abs=300)        # still the old frequency
        assert tone_hz(x[-4096:], 1e6) == pytest.approx(100_000, abs=300)       # then the new one
        dev.close()

    def test_gain_ref_scales_and_clips(self):
        dev = self.make(tones=[(125_000, -20)], gain_ref=32)
        dev.set_rx_gain_db(32)
        dev.pump(1)
        assert np.abs(dev.get_samples()).max() == pytest.approx(0.1, abs=0.01)
        dev.set_rx_gain_db(42)
        dev.pump(1)
        assert np.abs(dev.get_samples()).max() == pytest.approx(0.316, abs=0.02)
        dev.set_rx_gain_db(80)
        dev.pump(1)
        x = dev.get_samples()
        assert np.abs(x.real).max() <= 1.0 and np.mean(np.abs(x.real) > 0.98) > 0.5   # the ADC clips
        dev.close()

    def test_bursts(self):
        dev = self.make(bursts=[{"offset": 0, "level": -6, "period": 0.004, "duration": 0.001}], seed=2)
        dev.pump(2)
        on = np.abs(dev.get_samples()) > 0.25
        assert on.mean() == pytest.approx(0.25, abs=0.02)   # on a quarter of the time
        assert on[:1000].all() and not on[1000:4000].any()
        dev.close()

    def test_out_of_band_parts_are_filtered(self):
        dev = self.make(tones=[{"freq": 101.25e6, "level": -10}], noise=-40, seed=4)   # 1.25 MHz off at 1 MS/s
        dev.pump(2)
        assert np.abs(dev.get_samples()).max() < 0.1   # just the noise: no alias at +250 kHz
        dev.set_rx_freq(101.0e6)
        dev.pump(2)
        x = dev.get_samples()
        assert tone_hz(x, 1e6) == pytest.approx(250_000, abs=300) and _peak_db(x, 1e6, 250_000) == pytest.approx(-10, abs=0.5)
        dev.close()

    def test_open_takes_the_signal(self):
        with pysdrlib.open("sim", tones=[(125_000, -20)], noise=-40, seed=1) as dev:
            assert dev.tones == [{"offset": 125_000, "level": -20}] and dev.noise == -40
        with pytest.raises(TypeError, match="no signal setting tonez"):
            pysdrlib.open("sim", tonez=[(1, -1)])

    def test_configure_while_open(self):
        dev = self.make()
        dev.configure(tones=[(-250_000, -10)])
        dev.pump(2)
        assert tone_hz(dev.get_samples(), 1e6) == pytest.approx(-250_000, abs=300)
        dev.close()

    def test_default_tone_unchanged(self):
        dev = self.make()
        assert not dev._synthetic
        dev.pump(1)
        assert np.abs(dev.get_samples()).max() == pytest.approx(0.5, abs=0.01)
        dev.close()


class TestMisbehaving:
    @pytest.fixture
    def live(self):
        dev = Sim(realtime=True, block_samples=4096)
        dev.open()
        dev.initialize(cf=100e6, Fs=1e6)
        dev.start_rx()
        yield dev
        dev.close()

    def test_drop_makes_a_gap(self, live):
        live.read(4096, timeout=1.0)
        live.drop(5000)
        lost = 0
        for _ in range(10):
            _, m = live.read(4096, timeout=1.0, meta=True)
            lost += sum(n for _, n in m.gaps)
            if lost:
                break
        assert lost == 5000

    def test_stall_loses_what_would_have_come(self, live):
        live.read(4096, timeout=1.0)
        live.stall(0.1)
        assert live.stalled
        _, m = live.read(8192, timeout=2.0, meta=True)
        lost = sum(n for _, n in m.gaps)
        assert 50_000 < lost < 300_000     # ~0.1 s at 1 MS/s, with scheduling slack
        assert not live.stalled

    def test_stall_until_resumed(self, live):
        live.read(4096, timeout=1.0)
        live.stall()
        live.flush()
        with pytest.raises(err.Timeout):
            live.read(4096, timeout=0.1)
        live.resume()
        live.read(4096, timeout=1.0)


class TestOnDrop:
    """read/stream/sweep(on_drop=...): a block that lost samples inside it raises, or is captured again"""

    @pytest.fixture
    def live(self):
        dev = Sim(realtime=True, block_samples=4096)
        dev.open()
        dev.initialize(cf=100e6, Fs=1e6)
        dev.start_rx()
        yield dev
        dev.close()

    def test_raise_carries_the_block(self, live):
        live.read(4096, timeout=1.0)
        live.flush()   # nothing queued ahead: the drop lands inside the next read, not past its end
        live.drop(5000, after=4096)
        with pytest.raises(err.Dropped) as e:
            live.read(16384, timeout=1.0, on_drop="raise")
        assert e.value.lost == 5000 and len(e.value.samples) == 16384 and e.value.meta.gaps
        live.read(4096, timeout=1.0, on_drop="raise")   # the next block is whole again

    def test_default_returns_the_block(self, live):
        live.read(4096, timeout=1.0)
        live.flush()
        live.drop(5000, after=4096)
        _, m = live.read(16384, timeout=1.0, meta=True)
        assert sum(n for _, n in m.gaps) == 5000

    def test_stream(self, live):
        with live.stream(16384, timeout=1.0, on_drop="raise") as s:
            s.read()
            live.flush()
            live.drop(5000, after=4096)
            with pytest.raises(err.Dropped):
                s.read()

    def test_bad_policy(self, live):
        with pytest.raises(ValueError):
            live.read(10, on_drop="warn")
        with pytest.raises(ValueError):
            next(live.sweep([100e6], on_drop="sometimes"))

    def test_sweep_retries_a_step_that_lost_samples(self, live):
        real, drops = live.set_rx_freq, iter([True])
        def retune(*a, **kw):
            f = real(*a, **kw)
            if next(drops, False):   # only the first step
                live.drop(5000, after=4096)
            return f
        live.set_rx_freq = retune
        steps = list(live.sweep([100e6, 101e6], count=16384, timeout=1.0, meta=True, on_drop="retry"))
        assert [f for f, _, _ in steps] == [100e6, 101e6]
        assert all(not any(at for at, _ in m.gaps) for _, _, m in steps)

    def test_sweep_raise(self, live):
        real = live.set_rx_freq
        def retune(*a, **kw):
            f = real(*a, **kw)
            live.drop(5000, after=4096)
            return f
        live.set_rx_freq = retune
        with pytest.raises(err.Dropped):
            list(live.sweep([100e6], count=16384, timeout=1.0, on_drop="raise"))


class TestLatest:
    """read(latest=True): the newest block at the current settings"""

    @pytest.fixture
    def live(self):
        dev = Sim(realtime=True, block_samples=4096, tones=[{"freq": 100.2e6, "level": -6}])
        dev.open()
        dev.initialize(cf=100e6, Fs=1e6)
        dev.start_rx()
        yield dev
        dev.close()

    def test_skips_the_backlog(self, live):
        live.read(4096, timeout=1.0)
        time.sleep(0.2)
        _, m = live.read(4096, timeout=1.0, meta=True, latest=True)
        assert m.discarded > 150_000 and not m.gaps
        total, _ = live._rx_position()
        assert total - (m.index + 4096) <= 3 * 4096   # what came in while it was read, at most

    def test_skips_what_came_before_a_retune(self, live):
        live.read(4096, timeout=1.0)
        time.sleep(0.05)
        live.set_rx_freq(100.1e6)   # no flush: the queue still holds samples at 100 MHz
        x, m = live.read(8192, timeout=1.0, meta=True, latest=True)
        assert m.freq == 100.1e6 and not m.events
        assert tone_hz(x, 1e6) == pytest.approx(100_000, abs=300)

    def test_stream(self, live):
        with live.stream(4096, timeout=1.0, meta=True, latest=True) as s:
            s.read()
            time.sleep(0.1)
            _, m = s.read()
            assert m.discarded > 50_000


class TestLost:
    @pytest.fixture
    def live(self):
        dev = Sim(realtime=True, block_samples=4096)
        dev.open()
        dev.initialize(cf=100e6, Fs=1e6)
        dev.start_rx()
        yield dev
        dev.close()

    def test_disconnect_raises_lost_once_drained(self, live):
        live.read(4096, timeout=1.0)
        time.sleep(0.05)
        live.disconnect()
        live.read(8192, timeout=1.0)   # what was queued is still read
        t = time.monotonic()
        with pytest.raises(err.Lost):
            live.read(1_000_000, timeout=5.0)
        assert time.monotonic() - t < 1.0   # at once, not after the stall timeout
        live.resume()
        assert live.stalled   # unplugged stays unplugged

    def test_a_stall_is_not_lost(self, live):
        live.stall_timeout = 0.2
        live.read(4096, timeout=1.0)
        live.stall()
        live.flush()
        with pytest.raises(err.StreamStalled) as e:
            live.read(4096, timeout=2.0)
        assert not isinstance(e.value, err.Lost)


class TestFindGain:
    """find_gain on a sim whose signal scales with gain (gain_ref) and clips at full scale"""

    def make(self, **kwargs):
        dev = Sim(realtime=True, block_samples=4096, tones=[(125_000, -20)], gain_ref=32, **kwargs)
        dev.open()
        dev.initialize(cf=100e6, Fs=1e6)
        return dev

    @staticmethod
    def clip_db(level_db, ref=32):
        """total gain at which a tone at `level_db` dBFS (at `ref` dB of gain) reaches full scale in I or Q (0 dBFS)"""
        return ref - level_db

    def test_finds_the_highest_gain_that_does_not_clip(self):
        dev = self.make()
        span = dev.caps.gain_range("rx")
        f = dev.find_gain(count=8192)
        limit = (self.clip_db(-20) - span.lo) / (span.hi - span.lo)   # 52 dB
        assert f == dev.get_rx_gain() and limit - 0.03 <= f <= limit + 0.005
        assert not dev.state["rx"]   # stopped again
        dev.start_rx()
        x = dev.read(8192, timeout=1.0)
        assert 0.7 < np.abs(x.real).max() < 0.98   # within a gain step or two of full scale
        dev.close()

    def test_headroom(self):
        dev = self.make()
        dev.find_gain(count=8192)
        top = dev.get_rx_gain_db()
        dev.find_gain(count=8192, headroom_db=6)
        assert dev.get_rx_gain_db() == pytest.approx(top - 6, abs=1.5)
        dev.close()

    def test_weak_signal_gets_full_gain(self):
        dev = Sim(realtime=True, block_samples=4096, tones=[(125_000, -110)], gain_ref=32)
        dev.open()
        dev.initialize(cf=100e6, Fs=1e6)
        assert dev.find_gain(count=4096) == 1.0
        dev.close()

    def test_two_channels_each_their_own(self):
        dev = self.make(channels=2, channel_db=[0, -10])   # channel 1 is 10 dB weaker: 10 dB more gain
        found = dev.find_gain(channel=[0, 1], count=8192)
        span = dev.caps.gain_range("rx")
        assert (found[1] - found[0]) * (span.hi - span.lo) == pytest.approx(10, abs=2)
        assert dev.get_rx_gain(channel=1) == found[1]
        dev.close()

    def test_settings_the_device_has(self):
        dev = self.make()
        dev.find_gain(count=4096)
        assert dev.get_rx_gain_db() in dev_settings(dev)

    def test_bursts_a_window_can_miss(self):
        # strong bursts 5 ms of every 100 ms: a 4096-sample window (4 ms) mostly misses them; the confirm read catches them
        dev = Sim(realtime=True, block_samples=4096, tones=[(125_000, -40)], gain_ref=32,
                  bursts=[{"offset": -200_000, "level": -10, "period": 0.1, "duration": 0.005}], seed=3)
        dev.open()
        dev.initialize(cf=100e6, Fs=1e6)
        dev.find_gain(count=4096, confirm=250_000)
        assert dev.get_rx_gain_db() <= 32 + 10 + 1   # where the bursts (not the -40 dBFS tone) reach full scale
        dev.close()

    def test_channels_must_be_receiving(self):
        dev = self.make(channels=2)
        dev.start_rx()   # channel 0 only
        with pytest.raises(err.InvalidValue, match="rx_channels"):
            dev.find_gain(channel=[0, 1])
        dev.close()

    def test_no_rounding_warnings(self, recwarn):
        dev = self.make()
        dev.find_gain(count=4096)
        assert not [w for w in recwarn if "gain must" in str(w.message)]
        dev.close()


def test_sigmf_sink_annotates_a_gap_inside_a_block_once(tmp_path):
    import json
    from pysdrlib.file import FileSink
    dev = Sim(realtime=True, block_samples=4096)
    dev.open()
    dev.initialize(cf=100e6, Fs=1e6)
    dev.start_rx()
    try:
        dev.read(10_000, timeout=1.0)
        dev.drop(5000, after=3000)
        with FileSink(tmp_path / "x.sigmf") as sink:
            for _ in range(4):
                x, m = dev.read(10_000, timeout=1.0, meta=True)
                sink.write(x, meta=m)
    finally:
        dev.close()
    notes = json.loads((tmp_path / "x.sigmf-meta").read_text())["annotations"]
    assert [a["core:comment"] for a in notes] == ["5000 samples lost before"]


def dev_settings(dev):
    from pysdrlib.base.device import _gain_settings
    return _gain_settings(dev.caps.gains["rx"])
