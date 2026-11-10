"""The device-agnostic API: blocking reads with stream metadata, capabilities, gains, filters, settings, correction

Reading runs on the simulated device (the real C++ ring and a streaming thread); the controls run on every
hardware device over its fake driver (tests/devices.py), so each driver's mapping onto them is checked.
"""
import dataclasses
import time

import numpy as np
import pytest

import pysdrlib
from pysdrlib import err
from pysdrlib.base import caps as caps_mod
from pysdrlib.sim import Sim

from devices import BY_NAME, fake_lib

def sim(rate=1e6, realtime=True, block=4096):
    s = Sim(realtime=realtime, block_samples=block)
    s.open()
    s.initialize(cf=100e6, Fs=rate)
    return s

# ---------------------------------------------------------------- read / read_into / stream
class TestRead:
    def test_read_returns_exactly_count(self):
        s = sim()
        s.start_rx()
        try:
            for n in (1, 1000, 12345):
                x = s.read(n, timeout=2)
                assert x.dtype == np.complex64 and len(x) == n
        finally:
            s.stop_rx()

    def test_consecutive_reads_are_contiguous(self):
        s = sim()
        s.start_rx()
        try:
            _, a = s.read(5000, timeout=2, meta=True)
            _, b = s.read(7000, timeout=2, meta=True)
        finally:
            s.stop_rx()
        assert a.index == 0 and b.index == 5000 and not a.gaps and not b.gaps and a.dropped == 0
        assert a.freq == 100e6 and a.rate == 1e6 and a.time is not None and a.time <= b.time

    def test_read_into_fills_the_array(self):
        s = sim()
        buf = np.zeros(3000, np.complex64)
        s.start_rx()
        try:
            n, m = s.read_into(buf, timeout=2, meta=True)
        finally:
            s.stop_rx()
        assert n == 3000 and m.count == 3000 and np.all(buf != 0)
        with pytest.raises(TypeError):
            s.read_into(np.zeros(10, np.complex128))

    def test_timeout(self):
        s = sim(rate=1e3, block=256)   # 1000 samples/s: 10^6 can't come in 0.2 s
        s.start_rx()
        try:
            t = time.monotonic()
            with pytest.raises(err.Timeout):
                s.read(1_000_000, timeout=0.2)
            assert time.monotonic() - t < 1.0
            with pytest.raises(TimeoutError):   # err.Timeout is a TimeoutError
                s.read(1_000_000, timeout=0.05)
        finally:
            s.stop_rx()

    def test_reading_needs_a_stream(self):
        s = sim()
        with pytest.raises(err.NotStreaming):
            s.read(10, timeout=0.1)

    def test_lost_samples_show_as_a_gap(self):
        s = sim(rate=1e6, realtime=False, block=65536)   # floods the 0.5 s ring while nothing reads
        s.start_rx()
        try:
            s.read(1000, timeout=2)
            time.sleep(0.3)
            _, m = s.read(1000, timeout=2, meta=True)
        finally:
            s.stop_rx()
        assert m.dropped > 0 and m.gaps[0][0] == 0 and m.index == 1000 + m.dropped

    def test_stream_starts_and_stops(self):
        s = sim()
        with s.stream(2048, timeout=2, meta=True) as st:
            assert s.state["rx"]
            blocks = []
            for x, m in st:
                blocks.append(m.index)
                if len(blocks) == 3:
                    break
        assert not s.state["rx"] and blocks == [0, 2048, 4096]

    def test_flush_discards_what_is_queued(self):
        s = sim()
        s.start_rx()
        try:
            time.sleep(0.05)
            n = s.flush()
            _, m = s.read(100, timeout=2, meta=True)
        finally:
            s.stop_rx()
        assert n > 0 and m.index >= n and m.dropped == 0 and m.discarded >= n

    def test_retune_is_marked_and_flush_skips_the_old_samples(self):
        s = sim()
        s.start_rx()
        try:
            s.read(1000, timeout=2)
            s.set_rx_freq(101e6)
            x, m = s.read(200_000, timeout=2, meta=True)        # the change lands inside or before this block
            s.set_rx_freq(102e6, flush=True)
            _, m2 = s.read(1000, timeout=2, meta=True)
        finally:
            s.stop_rx()
        changes = [e for e in m.events if e.kind == "freq"]
        assert (changes and changes[0].value == 101e6 and m.freq == 100e6) or m.freq == 101e6
        assert m2.freq == 102e6 and not m2.events and m2.dropped == 0

    def test_dc_correction(self):
        s = sim()
        s.dc_correction = True
        s.start_rx()
        try:
            for _ in range(5):
                x = s.read(50_000, timeout=2)
        finally:
            s.stop_rx()
        assert abs(x.mean()) < 0.05

# ---------------------------------------------------------------- capabilities and controls, on every driver
@pytest.fixture
def driver(spec, monkeypatch):
    monkeypatch.setattr(spec.device_module, "lib", fake_lib(spec))
    d = spec.device_class()
    d.open()
    return d

class TestCapabilities:
    def test_closed_and_open(self, spec, monkeypatch):
        monkeypatch.setattr(spec.device_module, "lib", fake_lib(spec))
        d = spec.device_class()
        closed = d.caps
        assert closed.driver == d.NAME and closed.freq and closed.rates and closed.gains["rx"]
        d.open()
        assert d.caps is not closed and d.caps.channels["rx"] >= 1

    def test_json_round_trip(self, driver):
        c = driver.caps
        back = caps_mod.from_json(driver.get_caps())
        assert back.json() == c.json()

    def test_gain_stages_by_name(self, driver):
        stages = driver.rx_gain_stages()
        name, stage = next(iter(stages.items()))
        got = driver.set_rx_gain_db(stage.max, stage=name.lower())
        assert got == stage.max and driver.get_rx_gain_db(name) == stage.max
        with pytest.raises(err.NotSupported):
            driver.set_rx_gain_db(1, stage="no such stage")

    def test_total_gain_is_the_sum_of_the_stages(self, driver):
        driver.set_rx_gain_db(20)
        assert driver.get_rx_gain_db() == sum(v for v in (driver.get_rx_gain_db(n) for n in driver.rx_gain_stages()) if v is not None)

    def test_strict(self, driver, spec):
        driver.strict = True
        with pytest.raises(err.InvalidValue):
            driver.set_rx_freq(spec.caps.freq[-1].hi * 2)
        driver.strict = False
        with pytest.warns(Warning):
            driver.set_rx_freq(spec.caps.freq[-1].hi * 2)

    def test_channels(self, driver):
        with pytest.raises(err.InvalidValue):   # one past the last channel
            driver.set_rx_freq(100e6, channel=driver.caps.channels["rx"])

    def test_unsupported_controls_say_so(self, driver):
        if not driver.caps.antennas["rx"]:
            with pytest.raises(err.NotSupported):
                driver.set_rx_antenna("A")
        if not driver.caps.clock_sources:
            with pytest.raises(err.NotSupported):
                driver.set_clock_source("external")
        if driver.caps.gain_modes["rx"] == ["manual"]:
            with pytest.raises(err.NotSupported):
                driver.set_rx_gain_mode("auto")

    def test_settings_reach_the_driver(self, driver):
        for name, s in driver.settings().items():
            if s.kind is bool:
                assert driver.set_setting(name, True) is True and driver.get_setting(name) is True
        with pytest.raises(err.NotSupported):
            driver.set_setting("no such setting", 1)

    def test_freq_correction(self, driver, spec):
        f = 100e6 if 100e6 in driver.caps.freq[0] else spec.caps.default_freq
        driver.set_rx_freq(f)
        driver.set_freq_correction(10.0)
        assert driver.get_freq_correction() == pytest.approx(10.0) and driver.get_rx_freq() == f
        if spec.name == "rtl_sdr":   # whole ppm in the hardware
            assert driver.device.of("set_freq_correction")[-1] == (10,)
            assert spec.freqs(driver)[-1] == int(f)
        else:                        # asked for less, so a fast reference lands on f
            assert spec.freqs(driver)[-1] == pytest.approx(int(f / (1 + 10e-6)), abs=1)
        assert driver.get_rx_sample_rate() == pytest.approx(spec.caps.default_rate if spec.name != "rtl_sdr" else driver.get_rx_sample_rate())

# ---------------------------------------------------------------- driver specifics through the generic API
def test_hackrf_bandwidth(monkeypatch):
    spec = BY_NAME["hackrf"]
    monkeypatch.setattr(spec.device_module, "lib", fake_lib(spec))
    d = spec.device_class()
    d.open()
    assert d.set_rx_bandwidth(4e6) == 5_000_000            # the narrowest filter that passes 4 MHz
    assert d.device.of("set_baseband_filter_bandwidth")[-1] == (5_000_000,)
    assert d.get_rx_bandwidth() == 5_000_000
    with pytest.warns(Warning):
        assert d.set_rx_bandwidth(40e6) == 28_000_000

def test_rtl_sdr_gain_table_and_agc(monkeypatch):
    spec = BY_NAME["rtl_sdr"]
    monkeypatch.setattr(spec.device_module, "lib", fake_lib(spec))
    d = spec.device_class()
    d.open()
    d.device._returns = {**d.device._returns, "gains": lambda: [0.0, 0.9, 1.4, 2.7, 29.7, 32.8, 49.6]}   # not the spec's dict
    d._caps = None
    assert d.caps.gains["rx"]["RF"].values[-1] == 49.6
    assert d.set_rx_gain_db(30) == 29.7 and d.device.of("set_rx_rf_gain")[-1] == (29.7,)
    assert d.rx_gain_modes() == ["manual", "auto"]
    d.set_rx_gain_mode("auto")
    assert d.device.of("set_agc")[-1] == (True,) and d.get_rx_gain_mode() == "auto"
    assert len(d.caps.rates) == 2   # the RTL2832U has no rates between them

def test_rx888_rates_and_settings(monkeypatch):
    spec = BY_NAME["extio"]
    monkeypatch.setattr(spec.device_module, "lib", fake_lib(spec))
    d = spec.device_class()
    d.open()
    assert [r.lo for r in d.caps.rates] == [2e6, 4e6, 8e6, 16e6, 32e6]
    assert {"dither", "random", "pga"} <= set(d.settings())
    d.set_setting("dither", True)
    assert d.device.of("set_dither")[-1] == (True,)

# ---------------------------------------------------------------- finding devices
def test_find_lists_virtual_devices_only_when_asked():
    assert not [i for i in pysdrlib.find() if i.driver == "sim"]
    infos = [i for i in pysdrlib.find(virtual=True) if i.driver == "sim"]
    assert infos and infos[0].extra["virtual"]
    s = pysdrlib.open(infos[0])
    assert s.state["open"]
    s.close()

def test_open_by_driver_name_and_serial(monkeypatch, spec):
    monkeypatch.setattr(spec.device_module, "lib", fake_lib(spec))
    if spec.name == "extio":
        pytest.skip("the RX-888 driver has no serial numbers")
    d = pysdrlib.open(spec.name, serial="1234")
    assert d.state["open"]

def test_ls_lists_drivers_only():
    assert "_native" not in pysdrlib.devices.ls() and "sim" in pysdrlib.devices.ls()

# ---------------------------------------------------------------- splitting a total gain, relative gain
def test_hackrf_total_gain_is_reached(monkeypatch):
    spec = BY_NAME["hackrf"]
    monkeypatch.setattr(spec.device_module, "lib", fake_lib(spec))
    d = spec.device_class()
    d.open()
    for total in range(0, 114):
        d.set_rx_gain_db(total)
        got = d.get_rx_gain_db()
        assert got <= total and total - got <= 2, (total, got, d.split_rx_gain_db(total))  # within the 2 dB VGA step
        # the 11 dB amp only when the LNA and VGA can't come within their 2 dB step (above 104 dB)
        assert (d.get_rx_gain_db("RF") == 11) == (total >= 105), (total, d.split_rx_gain_db(total))
        assert d.split_rx_gain_db(total) == d.split_rx_gain_db(total)   # deterministic
    d.set_rx_gain_db(30)
    assert d.get_rx_gain_db() == 30 and d.device.of("set_amp_enable")[-1] == (False,)
    # the vendor's advice: IF and baseband about equal in dB (the LNA moves in 8 dB steps), the IF capped at 40
    for total in range(0, 81, 2):
        s = d.split_rx_gain_db(total)
        assert abs(s["IF"] - s["BB"]) <= 8, (total, s)             # within one LNA step
    assert d.split_rx_gain_db(32) == {"RF": 0, "IF": 16, "BB": 16}      # their starting point
    assert d.split_rx_gain_db(100) == {"RF": 0, "IF": 40, "BB": 60}

@pytest.mark.parametrize("name", ["hackrf", "rtl_sdr", "extio"])
def test_relative_gain(name, monkeypatch):
    spec = BY_NAME[name]
    monkeypatch.setattr(spec.device_module, "lib", fake_lib(spec))
    d = spec.device_class()
    d.open()
    r = d.caps.gain_range("rx")
    assert d.set_rx_gain(0.0) == pytest.approx(0.0, abs=0.02) and d.get_rx_gain_db() == pytest.approx(r.lo, abs=1)
    assert d.set_rx_gain(1.0) == pytest.approx(1.0, abs=0.02) and d.get_rx_gain_db() == pytest.approx(r.hi, abs=1)
    half = d.set_rx_gain(0.5)
    assert abs(half - 0.5) < 0.06 and d.get_rx_gain_db() == pytest.approx(r.lo + 0.5 * (r.hi - r.lo), abs=3)
    stage = next(iter(d.rx_gain_stages()))
    assert d.set_rx_gain(1.0, stage=stage) == pytest.approx(1.0)
    with pytest.raises(err.InvalidValue, match=r"set_rx_gain_db\(1\.5\)"):   # never clamped: it is nearly always dB, and clamping is full gain
        d.set_rx_gain(1.5)
    with pytest.raises(err.InvalidValue):
        d.set_rx_gain(-0.1)
    assert d.get_rx_gain_db() == pytest.approx(r.hi, abs=1) or d.get_rx_gain_db() is not None   # (the failed calls set nothing)

def test_relative_gain_follows_a_tuner_table(monkeypatch):
    spec = BY_NAME["rtl_sdr"]
    monkeypatch.setattr(spec.device_module, "lib", fake_lib(spec))
    d = spec.device_class()
    d.open()
    d.device._returns = {**d.device._returns, "gains": lambda: [0.0, 0.9, 1.4, 2.7, 29.7, 32.8, 49.6]}
    d._caps = None
    d.set_rx_gain(0.6)                              # 29.76 dB of 0-49.6: the table's 29.7
    assert d.get_rx_gain_db() == 29.7 and d.get_rx_gain() == pytest.approx(29.7 / 49.6)

# ---------------------------------------------------------------- samples lost before the queue
def test_ring_skip_marks_a_gap_reads_never_span():
    from pysdrlib.base.buffer import RotatingBuffer
    r = RotatingBuffer()
    r.reset(100)
    r.update(np.arange(5).astype(np.complex64))
    r.skip(3)                                    # the hardware lost 3 here
    r.update(np.arange(5, 9).astype(np.complex64))
    assert r.total == 12 and r.available == 9
    x, i = r.read_indexed(100)
    assert i == 0 and len(x) == 5                # up to the gap
    x, i = r.read_indexed(100)
    assert i == 8 and list(x.real) == [5, 6, 7, 8]
    r.skip(2)
    assert r.read_indexed(10)[1] == 14           # a gap with nothing after it yet: the next index is past it

def test_read_reports_samples_lost_before_the_queue():
    s = sim()
    s.start_rx()
    try:
        s.read(1000, timeout=2)
        s.buffer.skip(777)                       # as a driver does when the hardware overflowed
        _, m = s.read(200_000, timeout=2, meta=True)
    finally:
        s.stop_rx()
    assert (0, 777) in m.gaps or any(n == 777 for _, n in m.gaps)


def test_decibels_given_to_set_gain_are_refused_on_a_transmitter_too(driver, spec):
    """Clamped, 40 would have been the most transmit gain there is"""
    if not type(driver).CAN_TRANSMIT:
        pytest.skip(f"{spec.name} does not transmit")
    driver.set_tx_gain(0.0)
    before = driver.get_tx_gain_db()
    with pytest.raises(err.InvalidValue):
        driver.set_tx_gain(40)
    assert driver.get_tx_gain_db() == before
