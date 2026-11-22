import time

import numpy as np

from pysdrlib import rtl_sdr

cf = 100_000_000
Fs = 2_400_000

def oneshot():
    with rtl_sdr.RTLSDR() as sdr:
        sdr.initialize(cf, Fs)
        print(sdr.device.tuner, sdr.device.usb_strings())
        sdr.start_rx()
        time.sleep(0.4) # read within BUFFER_SECONDS (0.5s) or the oldest samples are dropped
        samples = sdr.get_samples()
        sdr.stop_rx()
        print(len(samples), samples.dtype, f"mean power {np.mean(np.abs(samples)**2):.3g}")

if __name__ == "__main__":
    oneshot()
