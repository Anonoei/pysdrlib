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


class TestTransmit:
    def test_what_is_written_is_recorded(self, tmp_path):
        x = (0.5 * np.exp(2j * np.pi * np.arange(40_000) / 9)).astype(np.complex64)
        with pysdrlib.open("file", tx_path=tmp_path / "sent.sigmf", tx_format="ci16", realtime=False) as sdr:
            assert sdr.caps.channels == {"rx": 1, "tx": 1}
            sdr.initialize(cf=915e6, Fs=2e6)
            sdr.start_tx()
            assert sdr.write(x[:20_000]) == 20_000
            sdr.set_tx_freq(916e6)
            sdr.write(x[20_000:])
            assert sdr.get_tx_stats() == {"written": 40_000, "sent": 40_000, "underruns": 0, "pending": 0}
            sdr.stop_tx()
            with pytest.raises(pysdrlib.err.NotSupported, match="only transmits"):
                sdr.start_rx()
        f = pysdrlib.File(tmp_path / "sent", scale=True)
        assert f.fmt is pysdrlib.Formats.ci16t and f.rate == 2e6 and f.meta.captures == [(0, 915e6), (20_000, 916e6)]
        with f:
            assert np.allclose(f.next(40_000), x, atol=1e-4)

    def test_realtime_paces_writes_and_counts_underruns(self, tmp_path):
        with pysdrlib.open("file", tx_path=tmp_path / "sent.cfile", rate=1e6) as sdr:
            sdr.TX_BUFFER_SECONDS = 0.01
            sdr.initialize(cf=1e6, Fs=1e6)
            sdr.start_tx()
            t = time.monotonic()
            sdr.write(np.zeros(60_000, np.complex64))   # 60 ms of samples, 10 ms of queue: about 50 ms
            assert 0.03 < time.monotonic() - t < 0.5
            assert sdr.get_tx_stats()["pending"] <= 10_001
            time.sleep(0.05)                             # it all went out, and then nothing came
            sdr.write(np.zeros(100, np.complex64))
            assert sdr.get_tx_stats()["underruns"] == 1
            sdr.stop_tx()
        assert (tmp_path / "sent.cfile").stat().st_size == 60_100 * 8

    def test_play_and_record_at_once(self, rec, tmp_path):
        with pysdrlib.open("file", path=rec, tx_path=tmp_path / "out.sigmf", realtime=False) as sdr:
            sdr.initialize()
            sdr.start_rx()
            sdr.start_tx()
            sdr.write(sdr.read(1000, timeout=2))
            sdr.stop_tx()
            sdr.stop_rx()
        assert pysdrlib.File(tmp_path / "out.sigmf").max_samp == 1000

    def test_a_recording_to_play_or_a_file_to_transmit_to(self):
        with pytest.raises(ValueError, match="tx_path"):
            pysdrlib.open("file")


class TestHardwareTime:
    def test_a_replay_is_stamped_with_the_recordings_time(self, rec):
        t0 = pysdrlib.file.sigmf.read(rec).start_time
        with pysdrlib.open("file", path=rec, loop=True, realtime=False) as sdr:
            sdr.initialize()
            sdr.start_rx()
            stamps = [sdr.read(25_000, timeout=2, meta=True)[1] for _ in range(4)]
            sdr.stop_rx()
        # the recording is 2N = 60000 samples at 1 MS/s, played twice over: each block's first sample's own time
        assert [round(m.hw_time - t0, 6) for m in stamps] == [0.0, 0.025, 0.05 - 0.0, 0.015]

    def test_the_radios_time_reaches_a_recording(self, tmp_path):
        sdr = pysdrlib.open("sim")
        sdr.initialize(cf=1e6, Fs=1e6)
        t = time.time()
        with sdr.stream(10_000, meta=True, timeout=2) as s, FileSink(tmp_path / "r.sigmf", "cf32") as out:
            for _, (x, m) in zip(range(3), s):
                assert abs(m.hw_time - t) < 1.0
                out.write(x, meta=m)
                first = first if _ else m
        sdr.close()
        assert m.hw_time - first.hw_time == pytest.approx(0.02)
        assert pysdrlib.file.sigmf.read(tmp_path / "r").start_time == pytest.approx(first.hw_time, abs=1e-5)

    def test_no_stamp_no_time(self, tmp_path):
        path = ramp(tmp_path, 1000)
        with pysdrlib.open("file", path=path, fmt="cf32", rate=1e6, realtime=False) as sdr:
            sdr.initialize()
            sdr.start_rx()
            assert sdr.read(100, timeout=2, meta=True)[1].hw_time is None
            sdr.stop_rx()


class TestSweep:
    def test_a_list_and_the_default_step(self):
        with pysdrlib.open("sim") as sdr:
            sdr.initialize(cf=100e6, Fs=1e6)
            assert [f for f, _ in sdr.sweep([5e6, 1e6, 3e6], count=100, timeout=2)] == [5e6, 1e6, 3e6]
            assert [f for f, _ in sdr.sweep(10e6, 12e6, count=100, timeout=2)] == [10e6, 10.8e6, 11.6e6]
            with pytest.raises(ValueError):
                next(sdr.sweep(1e6, 2e6, -1e6))

    def test_offset_moves_the_dc_spike_and_keeps_the_signal(self):
        """sim's tone sits Fs/8 above wherever it is tuned: tuned `offset` higher and shifted back, it shows at
        Fs/8 + offset in the samples (where a real signal at the same place in the air would stay put)"""
        with pysdrlib.open("sim") as sdr:
            sdr.initialize(cf=100e6, Fs=1e6)
            sdr.set_tone(125e3)
            (f, x, m), = sdr.sweep([50e6], count=4096, offset=100e3, timeout=2, meta=True)
            assert f == 50e6 and m.freq == 50e6 and sdr.get_rx_freq() == 50.1e6
            peak = np.fft.fftfreq(4096, 1 / 1e6)[np.argmax(np.abs(np.fft.fft(x)))]
            assert abs(peak - 225e3) < 500

    def test_skip(self):
        with pysdrlib.open("sim") as sdr:
            sdr.initialize(cf=100e6, Fs=1e6)
            (_, _, a), (_, _, b) = sdr.sweep([1e6, 2e6], count=1000, skip=5000, timeout=2, meta=True)
            assert b.index - a.index >= 6000
