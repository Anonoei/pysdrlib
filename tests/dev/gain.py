"""What each relative gain does to a real signal: its level, the noise floor, SNR, clipping and spurs

    python tests/dev/gain.py                              # the first device found, an FM station it finds itself
    python tests/dev/gain.py --freq 162.4e6 --steps 21    # any frequency (a band you want to tune the gain for)

Sweeps set_gain_relative from 0 to 1 and, at each step, measures:
    signal   power within +-80 kHz of the centre (dB re full scale)
    floor    median of the spectrum (dB per bin): the noise floor
    SNR      signal over the floor across the same width
    clip     fraction of I/Q values at full scale
    spurs    spectrum bins standing 15 dB above the floor away from the signal, beyond those at the lowest gain:
             "unexpected frequencies that pop up when you increase the gain" (distortion)
and how much each step of gain moved the signal and the floor (1 dB/dB: linear; less: compression). A useful setting
is where SNR stops improving, before spurs or clipping appear.
"""
import argparse

import numpy as np

from rfcheck import add_device_args, centre_power, describe_gain, find_station, open_device, psd

def main():
    ap = add_device_args(argparse.ArgumentParser(description=__doc__.splitlines()[0]))
    ap.add_argument("--freq", type=float, help="measure here instead of at a signal [Hz] (no SNR then)")
    ap.add_argument("--rate", type=float, default=2e6, help="sample rate")
    ap.add_argument("--steps", type=int, default=11, help="relative gains from 0 to 1")
    ap.add_argument("--seconds", type=float, default=0.1, help="samples per measurement")
    ap.add_argument("--nfft", type=int, default=4096)
    a = ap.parse_args()

    sdr = open_device(a)
    sdr.on_drop = "ignore"
    try:
        sdr.initialize(cf=a.freq or a.station or a.band[0], Fs=a.rate)
        Fs = sdr.get_sample_rate()
        if sdr.caps.bandwidths["rx"]:
            sdr.set_bandwidth(0.9 * Fs)
        sdr.set_gain_relative(0.5)
        sdr.start_rx()
        freq = a.freq or a.station
        if freq is None:
            freq, on, off = find_station(sdr, Fs, tuple(a.band))
            print(f"signal at {freq/1e6:.3f} MHz ({on - off:.1f} dB above its surroundings at half gain)")
        sdr.set_freq(freq, flush=True)
        f = np.fft.fftshift(np.fft.fftfreq(a.nfft, 1 / Fs))
        away = np.abs(f) > 120e3                  # outside the signal
        edge = np.abs(f) < 0.4 * Fs               # inside the filter's passband

        print(f"\n{'rel':>5} {'gain':>34} {'signal':>8} {'floor':>8} {'SNR':>7} {'clip %':>8} {'spurs':>6} "
              f"{'dSig/dG':>8} {'dFlr/dG':>8}")
        prev, base_spurs, rows = None, None, []
        for rel in np.linspace(0, 1, a.steps):
            sdr.set_gain_relative(float(rel), flush=True)
            x = sdr.read(int(a.seconds * Fs), timeout=3)
            p = psd(x, a.nfft)
            floor = float(np.median(p[away & edge]))
            signal = float(np.median(centre_power(x, Fs, len(x))))
            bins = int(np.sum(np.abs(f) <= 80e3))
            snr = (signal - floor - 10 * np.log10(bins)) if not a.freq else float("nan")
            clip = float(np.mean(np.maximum(np.abs(x.real), np.abs(x.imag)) >= 0.99)) * 100
            spurs = set(np.nonzero(away & edge & (p > floor + 15))[0])
            base_spurs = spurs if base_spurs is None else base_spurs
            new = len(spurs - base_spurs)
            gain = sdr.get_gain()
            slope = ("", "")
            if prev is not None and gain != prev[0]:
                slope = (f"{(signal - prev[1]) / (gain - prev[0]):8.2f}", f"{(floor - prev[2]) / (gain - prev[0]):8.2f}")
            prev = (gain, signal, floor)
            rows.append((rel, snr, clip, new))
            print(f"{rel:5.2f} {describe_gain(sdr):>34} {signal:8.1f} {floor:8.1f} {snr:7.1f} {clip:8.3f} {new:6d} "
                  f"{slope[0]:>8} {slope[1]:>8}")
        clean = [r for r in rows if r[2] < 0.01 and r[3] == 0 and not np.isnan(r[1])]
        if clean:
            best = max(clean, key=lambda r: r[1])
            print(f"\nbest SNR without clipping or new spurs: relative gain {best[0]:.2f} ({best[1]:.1f} dB)")
    finally:
        if sdr.state.get("rx"):
            sdr.stop_rx()
        sdr.close()

if __name__ == "__main__":
    main()
