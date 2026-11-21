"""pysdrlib.open("file", ...): a recording played back as a receiving device"""
import time

import numpy as np
import pytest

import pysdrlib
from pysdrlib import FileSink, testing
from pysdrlib.file import Replay

N = 30_000

@pytest.fixture
def rec(tmp_path):
    """A SigMF recording: N samples of 1 at 100 MHz, then N of 2 at 101 MHz (as ci16)"""
    path = tmp_path / "rec.sigmf"
    with FileSink(path, "ci16", rate=1e6, freq=100e6) as out:
        out.write(np.full(N, 1000, np.complex64))
        out.retune(101e6)
        out.write(np.full(N, 2000, np.complex64))
    return path

def ramp(tmp_path, n=N):
    """A raw .cfile whose sample k is k (so positions can be checked)"""
    path = tmp_path / "ramp.cfile"
    np.arange(n, dtype=np.complex64).tofile(path)
    return path

def test_conforms():
    assert not testing.errors(testing.check_package("file"))

def test_the_recording_says_what_it_is(rec):
    with pysdrlib.open("file", path=rec) as sdr:
        sdr.initialize()
        assert sdr.get_rx_freq() == 100e6 and sdr.get_rx_sample_rate() == 1e6 and sdr.length == 2 * N
        assert sdr.caps.rates[0].lo == sdr.caps.rates[0].hi == 1e6

def test_samples_are_the_recordings_and_captures_are_retunes(rec):
    with pysdrlib.open("file", path=rec, realtime=False) as sdr:
        sdr.initialize()
        sdr.start_rx()
        x, m = sdr.read(N + 10_000, timeout=2, meta=True)
        assert m.index == 0 and m.freq == 100e6 and m.gaps == ()
        assert m.events == (pysdrlib.base.stream.Event(N, "freq", 101e6, 0),)
        assert np.allclose(x[:N], 1000 / 32768) and np.allclose(x[N:], 2000 / 32768)
        y, m = sdr.read(1000, timeout=2, meta=True)
        assert m.freq == 101e6 and sdr.get_rx_freq() in (100e6, 101e6)
        sdr.stop_rx()

def test_a_raw_file_needs_its_rate(tmp_path):
    path = ramp(tmp_path)
    with pytest.raises(ValueError, match="rate"):
        pysdrlib.open("file", path=path, fmt="cf32")
    with pytest.raises(ValueError, match="fmt"):
        pysdrlib.open("file", path=path, rate=1e6)
    with pysdrlib.open("file", path=path, fmt="cf32", rate=1e6, freq=50e6) as sdr:
        sdr.initialize()
        assert sdr.get_rx_freq() == 50e6

def test_loop_keeps_the_stream_going(tmp_path):
    path = ramp(tmp_path, 1000)
    with pysdrlib.open("file", path=path, fmt="cf32", rate=1e6, loop=True, realtime=False, block_samples=300) as sdr:
        sdr.initialize()
        sdr.start_rx()
        x, m = sdr.read(2500, timeout=2, meta=True)
        assert m.gaps == () and np.array_equal(x.real, np.arange(2500) % 1000)
        assert not sdr.eof
        sdr.stop_rx()

def test_as_fast_as_it_is_read_drops_nothing(tmp_path):
    path = ramp(tmp_path, 300_000)
    with pysdrlib.open("file", path=path, fmt="cf32", rate=100e3, realtime=False) as sdr:   # a 50k queue
        sdr.initialize()
        sdr.start_rx()
        time.sleep(0.1)   # the queue fills; playback waits for room
        x = sdr.read(300_000, timeout=5)
        assert np.array_equal(x.real, np.arange(300_000)) and sdr.stats.dropped == 0
        sdr.stop_rx()

def test_the_end(tmp_path):
    path = ramp(tmp_path, 1000)
    with pysdrlib.open("file", path=path, fmt="cf32", rate=1e6) as sdr:
        sdr.initialize()
        sdr.start_rx()
        assert len(sdr.read(1000, timeout=2)) == 1000
        with pytest.raises(pysdrlib.err.Timeout):
            sdr.read(1, timeout=0.2)
        assert sdr.eof
        sdr.stop_rx()

def test_realtime_is_paced(tmp_path):
    path = ramp(tmp_path, 200_000)
    with pysdrlib.open("file", path=path, fmt="cf32", rate=1e6, block_samples=10_000) as sdr:
        sdr.initialize()
        sdr.start_rx()
        t = time.perf_counter()
        sdr.read(100_000, timeout=2)
        assert 0.07 < time.perf_counter() - t < 0.5
        sdr.stop_rx()

def test_seek(tmp_path, rec):
    path = ramp(tmp_path, 10_000)
    with pysdrlib.open("file", path=path, fmt="cf32", rate=1e6, realtime=False) as sdr:
        sdr.initialize()
        sdr.seek(4000)
        assert sdr.position == 4000
        sdr.start_rx()
        assert sdr.read(1, timeout=2)[0] == 4000
        with pytest.raises(ValueError):
            sdr.seek(10_001)
        sdr.stop_rx()
    with pysdrlib.open("file", path=rec, realtime=False) as sdr:   # into the second capture: at its frequency
        sdr.initialize()
        sdr.seek(N + 5)
        sdr.start_rx()
        x, m = sdr.read(10, timeout=2, meta=True)
        assert m.freq == 101e6 and np.allclose(x, 2000 / 32768)
        sdr.stop_rx()

def test_only_iq_recordings(tmp_path):
    (tmp_path / "r.f32").write_bytes(b"\0" * 64)
    with pytest.raises(ValueError, match="real format"):
        pysdrlib.open("file", path=tmp_path / "r.f32", fmt="f32", rate=1e6)
    with pytest.raises(ValueError, match="path"):
        pysdrlib.open("file")

def test_sweep_reads_each_step_after_its_retune(rec):
    """sweep on any device: here a replay, whose frequency only labels the stream"""
    with pysdrlib.open("file", path=rec, freq=1e6, loop=True, realtime=False) as sdr:
        sdr.initialize()
        steps = list(sdr.sweep(1e6, 3e6, 1e6, 100, timeout=2, meta=True))
        assert [f for f, _, _ in steps] == [1e6, 2e6, 3e6]
        assert [m.freq for _, _, m in steps] == [1e6, 2e6, 3e6]
        assert not sdr.state["rx"]

def test_is_a_replay():
    assert pysdrlib.devices.get("file").Device is Replay
    assert "file" not in pysdrlib.devices.ls()
