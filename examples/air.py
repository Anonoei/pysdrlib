"""Simulated radios sharing an air: one transmits, another hears it at its own frequency and rate."""
import shutil
import tempfile

import numpy as np

import pysdrlib
from pysdrlib.air import Air

directory = tempfile.mkdtemp()   # (the example's own: an air's streams go in the shm directory)

# --8<-- [start:exact]
air = Air("example", directory=directory, loss_db=6)   # a name; the settings every device in it shares
tx = pysdrlib.device("sim", realtime=False)            # not realtime: on their own sample clocks, exact and repeatable
rx = pysdrlib.device("sim", realtime=False, noise=-60)
tx.open(air=air, transmit=True)
rx.open(air=air)
rx.initialize(cf=433.90e6, Fs=2e6)

tx.set_tx_freq(433.92e6)                 # 20 kHz above the receiver
tx.set_tx_sample_rate(250e3)             # at a rate of its own
tx.set_tx_gain_db(41)                    # the "PA" stage: 47 dB is full scale, so 6 dB down
tx.start_tx()
t = np.arange(25_000) / 250e3            # 100 ms of a 5 kHz tone
tx.write((0.8 * np.exp(2j * np.pi * 5e3 * t)).astype(np.complex64))

rx.pump(4)                               # deliver 4 blocks (262 ms at 2 MS/s): what was sent, then silence
y = rx.get_samples()
on = np.abs(y) > 0.1
peak = np.fft.fftfreq(65536, 1 / 2e6)[np.argmax(np.abs(np.fft.fft(y[:65536])))]
print(f"heard {on.sum() / 2e6 * 1e3:.1f} ms at {peak / 1e3:+.1f} kHz, {20 * np.log10(np.abs(y[on]).mean()):.1f} dBFS")
tx.close()
rx.close()
# --8<-- [end:exact]
assert abs(on.sum() / 2e6 - 0.1) < 1e-3 and abs(peak - 25e3) < 100
assert abs(20 * np.log10(np.abs(y[on]).mean()) - (20 * np.log10(0.8) - 12)) < 0.5

# --8<-- [start:realtime]
rx = pysdrlib.open("sim", air="example-rt")             # realtime: on the wall clock, as radios are
rx.initialize(cf=915e6, Fs=1e6)
rx.start_rx()
tx = pysdrlib.open("sim", air="example-rt", transmit=True)   # (this could be another process: the air is shared memory)
tx.set_tx_freq(915.1e6)
tx.set_tx_sample_rate(1e6)
tx.start_tx()
tx.write(np.full(200_000, 0.5 + 0j, np.complex64))      # 0.2 s: write waits, as a radio's queue does
print([d.label for d in pysdrlib.find(driver="sim", virtual=True)])
y = rx.read(500_000)                                     # 0.5 s; the air is heard 0.1 s late
tx.close()
rx.close()
print(f"heard {(np.abs(y) > 0.25).sum() / 1e6:.2f} s")
# --8<-- [end:realtime]
assert abs((np.abs(y) > 0.25).sum() / 1e6 - 0.2) < 0.05
shutil.rmtree(directory, ignore_errors=True)
