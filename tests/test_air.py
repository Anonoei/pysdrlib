"""The air (pysdrlib.air): heard at the receiver's frequency and rate, for as long as it was
sent, in one process exactly (sims that aren't realtime) and across processes in time (realtime ones)"""
import subprocess
import sys
import textwrap

import numpy as np
import pytest

import pysdrlib
from pysdrlib.sim import Sim
from pysdrlib.air import Air, airs


@pytest.fixture
def air(tmp_path):
    return Air("test", directory=str(tmp_path))


def pair(air, rx_cf=100e6, rx_fs=2e6, block=8192, **rx_kw):
    tx = Sim(realtime=False, transmit=True, air=air)
    rx = Sim(realtime=False, air=air, block_samples=block, **rx_kw)
    tx.open()
    rx.open()
    rx.initialize(cf=rx_cf, Fs=rx_fs)
    return tx, rx


def heard(rx, blocks):
    rx.pump(blocks)
    return rx.get_samples()


def tone(n, fs, f, level=0.5):
    return (level * np.exp(2j * np.pi * f * np.arange(n) / fs)).astype(np.complex64)


def peak(x, fs):
    return np.fft.fftfreq(len(x), 1 / fs)[np.argmax(np.abs(np.fft.fft(x)))]


def test_heard_shifted_and_resampled(air):
    tx, rx = pair(air)
    tx.set_tx_freq(100.01e6)
    tx.set_tx_sample_rate(1e6)
    tx.start_tx()
    tx.write(tone(20000, 1e6, 5e3))   # 20 ms
    y = heard(rx, 6)
    on = np.abs(y) > 0.25
    assert on[:40000].all() and not on[40002:].any()   # 20 ms at 2 MS/s, then nothing
    assert abs(peak(y[:32768], 2e6) - 15e3) < 100   # +10 kHz of tuning, +5 kHz of tone
    assert abs(np.abs(y[:40000]).mean() - 0.5) < 0.01
    assert tx.get_tx_stats()["written"] == 20000 and tx.transmitted[0] == []   # (sent into the air, not kept)
    tx.close()
    rx.close()


def test_the_same_rate_is_sample_exact(air):
    tx, rx = pair(air, rx_cf=50e6, rx_fs=1e6, block=4096)
    tx.set_tx_freq(50e6)
    tx.set_tx_sample_rate(1e6)
    tx.start_tx()
    x = (np.random.default_rng(1).standard_normal(5000) * 0.2).astype(np.complex64)
    tx.write(x)
    y = heard(rx, 2)
    assert np.allclose(y[:5000], x, atol=1 / 64)   # (int8 samples on the sim's wire)


def test_out_of_band_and_loss(tmp_path):
    a = Air("loss", directory=str(tmp_path), loss_db=20)
    tx, rx = pair(a, rx_cf=100e6, rx_fs=1e6)
    tx.set_tx_freq(100.2e6)
    tx.set_tx_sample_rate(1e6)
    tx.start_tx()
    tx.write(tone(8192, 1e6, 0.0, level=0.9))
    assert abs(np.abs(heard(rx, 1)[:4096]).mean() - 0.09) < 0.01   # 20 dB down
    far = Sim(realtime=False, air=a, block_samples=8192)
    far.open()
    far.initialize(cf=101e6, Fs=1e6)   # 0.8 MHz away: beyond +-Fs/2
    assert np.abs(heard(far, 1)).max() == 0
    for d in (tx, rx, far):
        d.close()


def test_a_transmission_outlives_stop_tx(air):
    tx, rx = pair(air, rx_cf=100e6, rx_fs=1e6, block=4096)
    tx.set_tx_freq(100e6)
    tx.set_tx_sample_rate(1e6)
    tx.start_tx()
    tx.write(tone(3000, 1e6, 1e3))
    tx.stop_tx()   # still heard by a receiver that hasn't got there yet
    assert (np.abs(heard(rx, 1)[:3000]) > 0.4).all()
    tx.close()


def test_noise_and_gain_still_apply(air):
    tx, rx = pair(air, rx_cf=100e6, rx_fs=1e6, block=4096, gain_ref=10)
    rx.set_rx_gain_db(16)   # 6 dB over the reference: twice the amplitude, clipped at full scale
    tx.set_tx_freq(100e6)
    tx.set_tx_sample_rate(1e6)
    tx.start_tx()
    tx.write(np.full(4096, 0.4 + 0j, np.complex64))
    y = heard(rx, 1)
    assert abs(np.abs(y[:4096]).mean() - 0.8) < 0.02


def test_names():
    with pytest.raises(ValueError):
        Air("no.dots")
    assert Sim(air="lab").air.name == "lab" and Sim().air is None


def test_across_processes_in_time(tmp_path):
    """Realtime sims in two processes: a 0.6 s transmission heard for 0.6 s, at the receiver's offset"""
    code = textwrap.dedent(f"""
        import numpy as np
        from pysdrlib.sim import Sim
        from pysdrlib.air import Air, airs
        tx = Sim(transmit=True, air=Air("x", directory={str(tmp_path)!r}))
        tx.open(); tx.set_tx_freq(433.92e6); tx.set_tx_sample_rate(250e3); tx.start_tx()
        t = np.arange(25000) / 250e3
        for k in range(6):
            tx.write((0.5 * np.exp(2j * np.pi * 20e3 * (t + 0.1 * k))).astype(np.complex64))
        import time; time.sleep(0.3); tx.close()
        """)
    rx = Sim(air=Air("x", directory=str(tmp_path)), block_samples=16384)
    rx.open()
    rx.initialize(cf=433.9e6, Fs=500e3)
    rx.start_rx()
    p = subprocess.Popen([sys.executable, "-c", code])
    y = rx.read(int(1.6 * 500e3))
    assert p.wait(timeout=30) == 0
    rx.stop_rx()
    rx.close()
    on = np.abs(y) > 0.25
    assert abs(on.sum() / 500e3 - 0.6) < 0.1   # (a block's start-up either side; more on a loaded machine)
    assert abs(peak(y[on][:65536], 500e3) - 40e3) < 100


def test_a_transmission_starting_mid_block_is_heard_whole(air):
    """Realtime, in one process: a block is delivered as its last sample is taken, so a transmission that starts while
    it is being taken is in it, not lost"""
    import threading
    import time
    rx = Sim(air=air, block_samples=12000)   # 50 ms blocks
    rx.open()
    rx.initialize(cf=100e6, Fs=240e3)
    rx.start_rx()
    def send():
        time.sleep(0.12)   # (inside a block)
        tx = Sim(transmit=True, air=air)
        tx.open()
        tx.set_tx_freq(100e6)
        tx.set_tx_sample_rate(240e3)
        tx.start_tx()
        tx.write(np.full(24000, 0.5 + 0j, np.complex64))   # 100 ms
        time.sleep(0.2)
        tx.close()
    t = threading.Thread(target=send)
    t.start()
    y = rx.read(int(0.6 * 240e3))
    t.join()
    rx.stop_rx()
    rx.close()
    assert abs((np.abs(y) > 0.25).sum() - 24000) <= 2


def test_a_transmission_never_seen_is_not_heard(air):
    tx, rx = pair(air, rx_cf=100e6, rx_fs=1e6, block=4096)
    tx.set_tx_freq(100e6)
    tx.set_tx_sample_rate(1e6)
    tx.start_tx()
    tx.write(np.full(4096, 0.5 + 0j, np.complex64))
    tx.close()   # (closed before the receiver saw it: gone with it, and the receiver goes on)
    assert np.abs(heard(rx, 1)).max() == 0
    tx2 = Sim(realtime=False, transmit=True, air=air)
    tx2.open()
    tx2.set_tx_freq(100e6)
    tx2.set_tx_sample_rate(1e6)
    tx2.start_tx()
    tx2.write(np.full(8192, 0.5 + 0j, np.complex64))
    assert (np.abs(heard(rx, 1)) > 0.4).all()   # the next one is heard
    tx2.close()
    rx.close()


def test_two_receivers_one_behind(air):
    """Each receive channel keeps its own streams: one that has heard a stream to its end and let it go doesn't take it
    from another on the same Air that is still behind"""
    tx, ahead = pair(air, rx_cf=100e6, rx_fs=1e6, block=4096)
    behind = Sim(realtime=False, air=air, block_samples=4096)
    behind.open()
    behind.initialize(cf=100e6, Fs=1e6)
    tx.set_tx_freq(100e6)
    tx.set_tx_sample_rate(1e6)
    tx.start_tx()
    tx.write(np.full(6000, 0.5 + 0j, np.complex64))
    first = heard(behind, 1)              # both have seen it
    a = heard(ahead, 1)
    tx.close()
    a = np.concatenate((a, heard(ahead, 3)))   # to its end, and past: ahead lets it go
    assert (np.abs(a) > 0.4).sum() == 6000
    y = np.concatenate((first, heard(behind, 2)))
    assert (np.abs(y) > 0.4).sum() == 6000
    ahead.close()
    behind.close()


def test_channels_without_a_sim(air):
    tx = air.tx_channel(1e6, 100e6, clock="samples")
    rx = air.rx_channel()
    tx.write(tone(1000, 1e6, 0.0), scale=0.5)
    y = rx.receive(0, 2000, 100e6, 1e6, "samples")
    assert np.allclose(y[:1000], 0.25, atol=1e-6) and not y[1000:].any()
    assert [m["name"] for m in air.channels()] == [tx.stream.name]
    assert list(airs(air.directory)) == ["test"]
    tx.close()
    rx.close()
    assert air.channels() == [] and airs(air.directory) == {}


def test_wall_clock_blocks_are_continuous(air):
    """On the wall clock (epoch seconds, where float64 resolves only ~0.24 us), consecutive blocks are sample-exact and
    phase-continuous: hearing two blocks is hearing one twice as long"""
    import time
    fs_tx, fs_rx, df = 10e6, 2e6, 0.6e6
    tx = air.tx_channel(fs_tx, 100e6 + df, clock="wall")
    x = (np.random.default_rng(3).standard_normal(200_000) * 0.1).astype(np.complex64)
    tx.write(x)
    t_written = tx.stream.manifest["wall_time"]
    rx = air.rx_channel()
    t0 = t_written + 1234.5678e-6   # (an epoch time: a receiver anchored where a sim would be)
    n = 8192
    one = air.rx_channel()
    rx.anchor(t0)
    one.anchor(t0)
    parts = [rx.receive(k * n, n, 100e6, fs_rx, "wall") for k in range(4)]
    whole = one.receive(0, 4 * n, 100e6, fs_rx, "wall")
    assert np.abs(whole).max() > 0.05
    assert np.allclose(np.concatenate(parts), whole, atol=1e-5)
    for c in (tx, rx, one):
        c.close()


def test_a_rate_change_keeps_the_time(air):
    """Changing the receiver's rate doesn't move it in time: a 30 ms transmission is heard for 30 ms"""
    tx, rx = pair(air, rx_cf=100e6, rx_fs=1e6, block=4096)
    tx.set_tx_freq(100e6)
    tx.set_tx_sample_rate(1e6)
    tx.start_tx()
    tx.write(np.full(30_000, 0.5 + 0j, np.complex64))
    a = heard(rx, 4)                      # 16 ms at 1 MS/s
    rx.set_rx_sample_rate(2e6)
    b = heard(rx, 8)                      # then 16 ms at 2 MS/s, ...
    on = (np.abs(a) > 0.4).sum() / 1e6 + (np.abs(b) > 0.4).sum() / 2e6
    assert abs(on - 0.030) < 1e-5


def test_a_wall_retune_does_not_overlap(air):
    """A transmitter that retunes on the wall clock starts its new frequency after what it queued at the old one"""
    import time
    tx = air.tx_channel(1e6, 100e6, clock="wall")
    rx, at_b = air.rx_channel(), air.rx_channel()
    tx.write(np.full(40_000, 0.5 + 0j, np.complex64))           # 40 ms, ahead of now
    t = tx.stream.manifest["wall_time"]
    air.channels()
    for c in (rx, at_b):   # (seen before the retune ends the ring it is on)
        c.anchor(t - 0.1)
        c.receive(0, 1, 100e6, 1e6, "wall")
    tx.write(np.full(40_000, 0.5 + 0j, np.complex64), cf=100.8e6)   # (each out of the other's receiver)
    air.channels()
    at_a = rx.receive(0, 200_000, 100e6, 1e6, "wall")
    b = at_b.receive(0, 200_000, 100.8e6, 1e6, "wall")
    on_a, on_b = np.abs(at_a) > 0.4, np.abs(b) > 0.4
    assert on_a.sum() == 40_000 and on_b.sum() == 40_000 and not (on_a & on_b).any()
    for c in (tx, rx, at_b):
        c.close()


def test_a_long_wall_write_is_heard_whole(tmp_path):
    """A write longer than the ring is published a piece at a time, as it goes out: a receiver keeping up hears it all"""
    a = Air("long", directory=str(tmp_path), capacity=1 << 18)   # 0.26 s at 1 MS/s
    rx = Sim(air=a, block_samples=16384)
    rx.open()
    rx.initialize(cf=100e6, Fs=1e6)
    rx.start_rx()
    tx = Sim(transmit=True, air=a)
    tx.open()
    tx.set_tx_freq(100e6)
    tx.set_tx_sample_rate(1e6)
    tx.start_tx()
    import threading
    t = threading.Thread(target=tx.write, args=(np.full(600_000, 0.5 + 0j, np.complex64),))   # 0.6 s: twice the ring
    t.start()
    y = rx.read(int(0.9e6))
    t.join()
    rx.stop_rx()
    tx.close()
    rx.close()
    assert abs((np.abs(y) > 0.25).sum() - 600_000) <= 16384


def test_open_takes_the_air(air):
    tx = Sim(realtime=False)   # (realtime is the constructor's; air and transmit can wait for open)
    tx.open(air=air, transmit=True)
    rx = pysdrlib.device("sim", realtime=False, block_samples=4096)
    rx.open(air=air)
    rx.initialize(cf=100e6, Fs=1e6)
    assert tx.caps.channels["tx"] == 1 and rx.air is air
    tx.set_tx_freq(100e6)
    tx.set_tx_sample_rate(1e6)
    tx.start_tx()
    tx.write(np.full(4096, 0.5 + 0j, np.complex64))
    assert (np.abs(heard(rx, 1)) > 0.4).all()
    tx.close()
    rx.close()
    s = pysdrlib.open("sim", air="named")
    assert s.air.name == "named"
    s.close()


def test_tx_gain(air):
    tx, rx = pair(air, rx_cf=100e6, rx_fs=1e6, block=4096)
    assert tx.tx_gain_stages() == {"PA": pysdrlib.sim.sim.TX_GAIN}
    tx.set_tx_freq(100e6)
    tx.set_tx_sample_rate(1e6)
    tx.start_tx()
    assert tx.set_tx_gain_db(27) == 27 and tx.set_tx_pa_gain(27) == 27   # 20 dB below full scale
    tx.write(np.full(4096, 0.9 + 0j, np.complex64))
    assert abs(np.abs(heard(rx, 1)).mean() - 0.09) < 0.01
    tx.close()
    rx.close()
    plain = Sim(realtime=False, transmit=True)
    plain.open()
    plain.start_tx()
    plain.set_tx_gain(0.0)
    plain.write(np.full(16, 1 + 0j, np.complex64))
    assert np.allclose(np.abs(plain.transmitted[0][0]), 10 ** (-47 / 20))
    plain.close()


def test_find_lists_the_air(air, monkeypatch):
    monkeypatch.setenv("PYSDRLIB_SHM_DIR", air.directory)
    tx = air.tx_channel(1e6, 100e6)
    tx.write(np.zeros(100, np.complex64))
    try:
        plain = [i for i in pysdrlib.find(driver="shm")]
        assert plain == []
        shown = [i for i in pysdrlib.find(driver="shm", virtual=True)]
        assert [i.serial for i in shown] == [tx.stream.name]
        sims = [i for i in pysdrlib.find(driver="sim", virtual=True) if i.open_args.get("air")]
        assert [i.open_args["air"] for i in sims] == ["test"]
    finally:
        tx.close()
