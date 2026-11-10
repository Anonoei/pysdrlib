"""Shared pieces of the hardware check scripts in tests/dev (retune.py, gain.py, api_check.py)

They work on any device pysdrlib.find() lists, or a remote one (--remote tcp://host:5555 --driver <type>).
"""
import argparse

import numpy as np

import pysdrlib

FM_BAND = (88.1e6, 108e6)

def add_device_args(parser: argparse.ArgumentParser):
    parser.add_argument("--driver", help="driver name (default: the first device pysdrlib.find() lists)")
    parser.add_argument("--serial", help="serial number of the unit")
    parser.add_argument("--remote", metavar="ADDRESS", help="use a pysdrlib server, e.g. tcp://host:5555 (needs --driver)")
    parser.add_argument("--station", type=float, help="frequency of a strong, steady signal [Hz] (default: search the FM band)")
    parser.add_argument("--band", type=float, nargs=2, default=FM_BAND, metavar=("LO", "HI"), help="where to search for one [Hz]")
    return parser

def open_device(args):
    """The device the arguments name, opened"""
    if args.remote:
        if not args.driver:
            raise SystemExit("--remote needs --driver (the device type the server hosts)")
        sdr = pysdrlib.device("remote", args.remote, args.driver)
        sdr.open()
        return sdr
    if args.driver:
        return pysdrlib.open(args.driver, serial=args.serial)
    found = [i for i in pysdrlib.find() if args.serial is None or i.serial == args.serial]
    if not found:
        raise SystemExit("no device found (pysdrlib.find() is empty); give --driver")
    print(f"using {found[0].label} ({found[0].driver}, serial {found[0].serial})")
    return found[0].open()

def lowpass(Fs, width=80e3, taps=129):
    """Taps of a low pass keeping +-`width` Hz around the centre"""
    h = np.sinc((np.arange(taps) - taps // 2) * (2 * width / Fs)) * np.hamming(taps)
    return h / h.sum()

def centre_power(x, Fs, window, width=80e3):
    """dB power within +-`width` Hz of the centre, for each `window` samples"""
    y = np.convolve(x, lowpass(Fs, width), mode="same")
    n = len(y) // window
    return 10 * np.log10(np.mean(np.abs(y[:n * window].reshape(n, window))**2, axis=1) + 1e-20)

def psd(x, nfft=4096):
    """Averaged power spectrum in dB (fftshifted), bins of Fs/nfft"""
    n = len(x) // nfft
    frames = x[:n * nfft].reshape(n, nfft) * np.hanning(nfft)
    p = np.mean(np.abs(np.fft.fft(frames, axis=1))**2, axis=0) / (nfft * np.sum(np.hanning(nfft)**2) / nfft)
    return 10 * np.log10(np.fft.fftshift(p) + 1e-20)

def level(sdr, freq, Fs, seconds=0.05):
    """Median centre power at `freq` (retuned with flush, so all of it is from there)"""
    sdr.set_rx_freq(freq, flush=True)
    x = sdr.read(int(seconds * Fs), timeout=3)
    return float(np.median(centre_power(x, Fs, max(64, int(Fs / 1e4)))))

def find_station(sdr, Fs, band=FM_BAND, step=0.2e6, away=5e6, contrast=15.0):
    """A signal that is only there when tuned to it: (freq, dB on it, dB `away` Hz from it). The device must be
    receiving. Raises SystemExit if nothing stands `contrast` dB above its surroundings"""
    best = None
    for f in np.arange(band[0], band[1] + step / 2, step):
        c = level(sdr, f, Fs) - level(sdr, f + away, Fs)
        if best is None or c > best[0]:
            best = (c, f)
    c, f = best
    if c < contrast:
        raise SystemExit(f"nothing in {band[0]/1e6:.1f}-{band[1]/1e6:.1f} MHz stands {contrast} dB out "
                         f"(best {f/1e6:.1f} MHz, {c:.1f} dB): give --station or raise the gain")
    return f, level(sdr, f, Fs), level(sdr, f + away, Fs)

def describe_gain(sdr):
    stages = {n: sdr.get_rx_gain_db(n) for n in sdr.rx_gain_stages()}
    return f"{sdr.get_rx_gain_db()} dB {stages}"
