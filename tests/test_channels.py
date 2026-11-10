"""Channels and directions through the generic API: per-channel controls, shared synthesizers, multi-channel streams

On the B210 over its fake driver (two channels each way, one synthesizer per direction), and on single-channel
devices, which must refuse a second channel.
"""
import numpy as np
import pytest

import pysdrlib
from pysdrlib import err

from devices import BY_NAME, SAMPLES, fake_lib

@pytest.fixture
def b210(monkeypatch):
    spec = BY_NAME["uhd"]
    monkeypatch.setattr(spec.device_module, "lib", fake_lib(spec))
    d = spec.device_class()
    d.open()
    d.device._returns = {**d.device._returns}   # tests may add to it: not the spec's dict
    return d

def calls(d, name):
    return d.device.of(name)

class TestTuning:
    def test_a_frequency_moves_its_directions_synthesizer(self, b210):
        assert b210.set_rx_freq(433e6, channel=1) == 433e6
        assert (433e6, 1) in calls(b210, "set_rx_freq") and not calls(b210, "set_tx_freq")   # the receiver only
        for ch in (0, 1):   # one LO per direction: channel 0 moved too
            assert b210.get_rx_freq(ch) == 433e6 and b210.get_tx_freq(ch) is None
        b210.set_tx_freq(433e6, channel=1)
        for ch in (0, 1):
            assert b210.get_tx_freq(ch) == 433e6

    def test_every_channel_is_tuned_alike(self, b210):
        b210.set_rx_freq(100e6)   # channel 1's DSP too: tuned apart by a fraction of a hertz, they drift in phase
        assert (100e6, 0) in calls(b210, "set_rx_freq") and (100e6, 1) in calls(b210, "set_rx_freq")
        b210.set_tx_freq(100e6)
        assert (100e6, 0) in calls(b210, "set_tx_freq") and (100e6, 1) in calls(b210, "set_tx_freq")

    def test_one_direction(self, b210):
        b210.set_rx_freq(100e6)
        b210.device.calls.clear()
        b210.set_tx_freq(915e6)
        assert calls(b210, "set_tx_freq") == [(915e6, 0), (915e6, 1)] and not calls(b210, "set_rx_freq")
        assert b210.get_rx_freq() == 100e6 and b210.get_rx_freq() == 100e6
        assert b210.get_tx_freq(1) == 915e6

    def test_rates_per_direction(self, b210):
        b210.set_rx_sample_rate(10e6)
        assert b210.set_tx_sample_rate(4e6) == 4e6
        assert calls(b210, "set_tx_rate")[-2:] == [(4e6, 0), (4e6, 1)]   # every channel of the stream
        assert b210.get_rx_sample_rate() == 10e6 and b210.get_tx_sample_rate(channel=1) == 4e6

    def test_freq_correction_retunes_each_synthesizer(self, b210):
        b210.set_rx_freq(100e6)
        b210.set_tx_freq(915e6)
        b210.device.calls.clear()
        b210.set_freq_correction(10.0)
        for name, f in (("set_rx_freq", 100e6), ("set_tx_freq", 915e6)):   # every channel of each synthesizer
            assert [c for c, in [(ch,) for _, ch in calls(b210, name)]] == [0, 1]
            assert all(v == pytest.approx(f / (1 + 10e-6)) for v, _ in calls(b210, name))
        assert b210.get_rx_freq() == 100e6

    def test_single_lo_devices_tune_both_directions(self, monkeypatch):
        spec = BY_NAME["hackrf"]
        monkeypatch.setattr(spec.device_module, "lib", fake_lib(spec))
        d = spec.device_class()
        d.open()
        d.set_tx_freq(915e6)
        assert d.caps.shared["freq"] == "device" and d.get_rx_freq() == 915e6 and d.device.of("set_freq")[-1] == (915_000_000,)

class TestPerChannelControls:
    def test_gain(self, b210):
        assert b210.set_gain(20, channel=1) == 20
        assert calls(b210, "set_rx_gain")[-1] == (20.0, 1)
        assert b210.get_gain(channel=1) == 20 and b210.get_gain(channel=0) != 20
        b210.set_gain_relative(1.0, channel=1, direction="tx")
        assert calls(b210, "set_tx_gain")[-1] == (89.75, 1)

    def test_bandwidth_antenna_agc_settings(self, b210):
        b210.set_bandwidth(1e6, channel=1)
        b210.set_antenna("TX/RX", channel=1)
        b210.set_gain_mode("auto", channel=1)
        b210.set_setting("dc_offset_auto", False, channel=1)
        assert calls(b210, "set_rx_bandwidth")[-1] == (1e6, 1) and b210.get_bandwidth(channel=1) == 1e6
        assert calls(b210, "set_rx_antenna")[-1] == ("TX/RX", 1) and b210.get_antenna(channel=1) == "TX/RX"
        assert calls(b210, "set_rx_agc")[-1] == (True, 1) and b210.get_gain_mode(channel=1) == "auto"
        assert calls(b210, "set_rx_dc_offset")[-1] == (False, 1) and b210.get_setting("dc_offset_auto", channel=1) is False
        assert b210.get_antenna(channel=0) is None and b210.get_gain_mode(channel=0) == "manual"

    def test_no_third_channel(self, b210):
        for call in (lambda: b210.set_gain(10, channel=2), lambda: b210.set_rx_freq(1e9, channel=2),
                     lambda: b210.start_rx(channels=(0, 2))):
            with pytest.raises(err.InvalidValue):
                call()

    @pytest.mark.parametrize("name", ["hackrf", "rtl_sdr", "extio"])
    def test_single_channel_devices_refuse_a_second(self, name, monkeypatch):
        spec = BY_NAME[name]
        monkeypatch.setattr(spec.device_module, "lib", fake_lib(spec))
        d = spec.device_class()
        d.open()
        with pytest.raises(err.InvalidValue):
            d.start_rx(channels=(0, 1))
        with pytest.raises(err.InvalidValue):
            d.set_gain(10, channel=1)

class TestStreams:
    def test_both_channels_together(self, b210, spec=BY_NAME["uhd"]):
        b210.start_rx(channels=(0, 1))
        assert calls(b210, "set_rx_channels")[-1] == ([0, 1],) and b210.rx_channels == (0, 1)
        assert calls(b210, "set_rx_buffer")[-1][1] == 2   # a queue per channel
        raw, _ = spec.raw()
        for _ in range(4):
            b210.device.deliver(raw)
        x, metas = b210.read(6, timeout=1, meta=True, channel=(0, 1))
        assert x.shape == (2, 6) and [m.index for m in metas] == [0, 0] and [m.channel for m in metas] == [0, 1]
        assert np.allclose(x[0], np.tile(SAMPLES, 3), atol=spec.atol) and np.allclose(x[1], x[0])

    def test_rows_start_at_the_same_instant(self, b210, spec=BY_NAME["uhd"]):
        b210.start_rx(channels=(0, 1))
        raw, _ = spec.raw()
        for _ in range(6):
            b210.device.deliver(raw)
        b210.read(3, timeout=1, channel=1)                # channel 1 is ahead of channel 0 now
        x, metas = b210.read(4, timeout=1, meta=True, channel=(0, 1))
        assert [m.index for m in metas] == [3, 3] and np.allclose(x[0], x[1])

    def test_one_channel_of_a_stream(self, b210, spec=BY_NAME["uhd"]):
        b210.start_rx(channels=(1,))
        raw, _ = spec.raw()
        b210.device.deliver(raw)
        assert len(b210.read(2, timeout=1, channel=1)) == 2
        with pytest.raises(err.InvalidValue):
            b210.read(2, timeout=0.1, channel=0)          # not in the stream

    def test_stream_object_takes_channels(self, b210, spec=BY_NAME["uhd"]):
        raw, _ = spec.raw()
        with b210.stream(4, timeout=1, channel=(0, 1)) as s:
            for _ in range(3):
                b210.device.deliver(raw)
            assert s.read().shape == (2, 4) and b210.rx_channels == (0, 1)
        assert not b210.state["rx"]

    def test_two_channels_need_a_rate_usb_carries(self, b210):
        b210.set_rx_sample_rate(40e6)
        with pytest.warns(Warning):
            b210.start_rx(channels=(0, 1))

class TestTransmitChannels:
    def test_rows_go_to_their_channels_in_step(self, b210):
        queued = []
        b210.device._returns["write_tx"] = lambda x, timeout, slot=0: queued.append((slot, len(x))) or len(x)
        b210.device._returns["tx_error"] = lambda: ""
        b210.start_tx(channels=(0, 1))
        assert calls(b210, "set_tx_channels")[-1] == ([0, 1],)
        n = 40000
        assert b210.write(np.zeros((2, n), np.complex64)) == n
        assert [s for s, _ in queued] == [0, 1] * 3            # chunk by chunk, alternating
        assert sum(k for s, k in queued if s == 0) == n == sum(k for s, k in queued if s == 1)

    def test_shape_must_match_the_channels(self, b210):
        b210.device._returns["tx_error"] = lambda: ""
        b210.start_tx(channels=(0, 1))
        with pytest.raises(ValueError):
            b210.write(np.zeros(8, np.complex64))               # one row for two channels
        with pytest.raises(err.InvalidValue):
            b210.write(np.zeros(8, np.complex64), channel=3)

    def test_receive_only_devices(self, monkeypatch):
        spec = BY_NAME["rtl_sdr"]
        monkeypatch.setattr(spec.device_module, "lib", fake_lib(spec))
        d = spec.device_class()
        d.open()
        with pytest.raises(err.NotSupported):
            d.start_tx()
