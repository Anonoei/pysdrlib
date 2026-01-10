import time
import numpy as np
import matplotlib.pyplot as plt

import pysdrlib

cf = 100_000_000
Fs = 10_000_000
nfft = 4096

freqs = np.linspace(cf - (Fs//2), cf + (Fs//2), nfft, dtype=np.float32)

def _psd(samples):
    psd = samples * np.blackman(nfft)
    psd = np.fft.fft(psd)**2 / (nfft*Fs)
    psd = np.fft.fftshift(np.abs(psd))
    psd = 10*np.log10(psd)
    return psd

def oneshot():
    from pysdrlib import hackrf
    with hackrf.Device() as sdr:
        sdr.set_freq(cf)
        sdr.set_sample_rate(Fs)
        sdr.set_rx_gain()
        sdr.start_rx()
        time.sleep(1)
        samples = sdr.get_samples()
        print(samples)
        sdr.stop_rx()

    psd = _psd(samples[:nfft])

    fig, ax = plt.subplots(layout="constrained")
    ax.plot(freqs, psd, c="y")
    ax.grid(True, alpha=0.5)

    plt.show()

def live():
    from pysdrlib import hackrf
    psd = np.empty(nfft, dtype=np.float32)
    psd_min = np.repeat(np.inf, nfft)
    psd_max = np.repeat(-np.inf, nfft)


    fig, ax = plt.subplots(layout="constrained")
    line_psd, = ax.plot([], [], c="y")
    line_min, = ax.plot([], [], c="b")
    line_max, = ax.plot([], [], c="r")
    ax.set_xlim(freqs[0], freqs[-1])
    ax.grid(True, alpha=0.5)
    fig.show(False)
    fig.canvas.draw()
    plt.ion()
    with hackrf.Device() as sdr:
        sdr.set_freq(cf)
        sdr.set_sample_rate(Fs)
        sdr.set_rx_gain()
        sdr.start_rx()
        try:
            time.sleep(0.5)
            while True:
                samples = sdr.get_samples()
                psd = _psd(samples[:nfft])
                psd_min[psd < psd_min] = psd[psd < psd_min]
                psd_max[psd > psd_max] = psd[psd > psd_max]
                ax.set_ylim(np.min(psd_min)-3, np.max(psd_max)+3)
                line_psd.set_data(freqs, psd)
                line_min.set_data(freqs, psd_min)
                line_max.set_data(freqs, psd_max)
                fig.canvas.draw()
                plt.pause(0.5)
        except KeyboardInterrupt:
            pass
        sdr.stop_rx()
    # plt.close(fig)
    # plt.ioff()

if __name__ == "__main__":
    # oneshot()
    live()
