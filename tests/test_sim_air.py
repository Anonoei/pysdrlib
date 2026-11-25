"""The air simulated radios share (pysdrlib.sim.air): heard at the receiver's frequency and rate, for as long as it was
sent, in one process exactly (sims that aren't realtime) and across processes in time (realtime ones)"""
import subprocess
import sys
import textwrap

import numpy as np
import pytest

import pysdrlib
from pysdrlib.sim import Sim
from pysdrlib.sim.air import Air


@pytest.fixture
def air(tmp_path):
    a = Air("test", directory=str(tmp_path))
    yield a
    a.close()


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
    a.close()


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
        from pysdrlib.sim.air import Air
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


def test_an_air_given_is_left_open(air):
    tx, rx = pair(air, rx_cf=100e6, rx_fs=1e6, block=4096)
    tx.set_tx_freq(100e6)
    tx.set_tx_sample_rate(1e6)
    tx.start_tx()
    tx.write(np.full(4096, 0.5 + 0j, np.complex64))
    tx.close()   # (the transmitter gone: what it sent is gone with it, and the receiver goes on)
    assert np.abs(heard(rx, 1)).max() == 0
    tx2 = Sim(realtime=False, transmit=True, air=air)
    tx2.open()
    tx2.set_tx_freq(100e6)
    tx2.set_tx_sample_rate(1e6)
    tx2.start_tx()
    tx2.write(np.full(8192, 0.5 + 0j, np.complex64))
    assert (np.abs(heard(rx, 1)) > 0.4).all()   # the air the receiver shares still works
    tx2.close()
    rx.close()
