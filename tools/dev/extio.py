import time

import numpy as np

from pysdrlib import extio

cf = 7_100_000
Fs = 8_000_000

def oneshot():
    with extio.RX888() as sdr:
        sdr.initialize(cf, Fs)
        print(sdr.device.name, f"firmware {sdr.device.firmware:#x}")
        sdr.start_rx()
        time.sleep(1)
        samples = sdr.get_samples()
        sdr.stop_rx()
        print(len(samples), samples.dtype, f"mean power {np.mean(np.abs(samples)**2):.3g}")

if __name__ == "__main__":
    oneshot()
