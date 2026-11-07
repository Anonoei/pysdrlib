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
from pysdrlib.sim.config import ConfigSim


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
        for call in (dev.start_rx, dev.get_samples, lambda: dev.set_freq(1e6), dev.close):
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
        assert dev.get_freq() == ConfigSim.DEFAULT_FREQ
        assert dev.get_sample_rate() == ConfigSim.DEFAULT_SAMPLE_RATE

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
        sim.set_sample_rate(2e6)
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


class TestGain:
    def test_generic_gain_splits_across_stages(self, sim):
        assert sim.set_rx_gain(60) == {"if": 40, "bb": 20}
        assert sim._gain == {"if": 40, "bb": 20}

    def test_generic_default(self, sim):
        assert sim.set_rx_gain() == {"if": ConfigSim.DEFAULT_GAIN_RX_IF, "bb": ConfigSim.DEFAULT_GAIN_RX_BB}

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
            assert sim.set_rx_if_gain(100) == ConfigSim.GAIN_RX_IF_MAX

    def test_freq_and_rate_clamped(self, sim):
        with pytest.warns(Warning):
            sim.set_sample_rate(1e12)
        assert sim.get_sample_rate() == ConfigSim.SAMPLE_RATE_MAX


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
