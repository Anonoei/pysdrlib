"""Several channels streamed together: the simulator built with two, as a B210 or a bladeRF has."""
import numpy as np

import pysdrlib

# --8<-- [start:channels]
sdr = pysdrlib.device("sim", channels=2, transmit=True)   # device(): constructor arguments, then open()
sdr.open()
sdr.initialize(cf=100e6, Fs=2e6)
sdr.set_rx_gain_db(30, channel=1)         # every control takes the channel
print(sdr.caps.channels, sdr.caps.shared) # how many per direction; what one setting changes

sdr.start_rx(channels=(0, 1))
x = sdr.read(65536, channel=(0, 1))       # (2, 65536), rows starting on the same sample
x1 = sdr.read(8192, channel=1)            # or one channel at a time
sdr.stop_rx()

sdr.start_tx(channels=(0, 1))
tone = np.exp(2j * np.pi * 0.01 * np.arange(8192)).astype(np.complex64)
sdr.write(np.stack((tone, -tone)))        # rows go out sample for sample together
sdr.stop_tx()
sdr.close()
# --8<-- [end:channels]
assert x.shape == (2, 65536)
