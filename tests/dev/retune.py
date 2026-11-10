"""How long after a retune the old frequency's samples keep coming, and whether set_freq(flush=True) skips them

    python tests/dev/retune.py                      # the first device found, an FM station it finds itself
    python tests/dev/retune.py --driver rtl_sdr --rates 1.024e6 2.4e6 --station 92.3e6

Retunes from `away` Hz off a strong signal onto it at random moments (so the USB transfers are caught at every point),
and finds the sample where the signal appears relative to where read(meta=True) marks the retune. Then checks that
with flush=True the first block after the retune is all on the signal. The device's skip is
caps.retune_samples + caps.retune_settle * rate; the report suggests values for the driver's RETUNE_SAMPLES/SETTLE.

Measured on a HackRF One (2024.02.1) at 2/10/20 MS/s: at most 131072 + 12352 samples after the mark (one USB
transfer + ~0.6 ms), so RETUNE_SAMPLES = 131072, RETUNE_SETTLE = 0.005.
"""
import argparse
import random
import time

import numpy as np

from pysdrlib.base.caps import nearest
from rfcheck import add_device_args, centre_power, describe_gain, find_station, open_device

def main():
    ap = add_device_args(argparse.ArgumentParser(description=__doc__.splitlines()[0]))
    ap.add_argument("--rates", type=float, nargs="+", help="sample rates to test (default: low, middle, high of caps.rates)")
    ap.add_argument("--trials", type=int, default=25, help="retunes per rate")
    ap.add_argument("--away", type=float, default=5e6, help="tune this far from the signal first [Hz]")
    ap.add_argument("--gain", type=float, default=0.55, help="relative gain (0-1, set_gain_relative)")
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    random.seed(a.seed)

    sdr = open_device(a)
    caps = sdr.caps
    lo, hi = caps.rates[0].lo, caps.rates[-1].hi
    rates = a.rates or sorted({lo, nearest(caps.rates, (lo * hi) ** 0.5), hi})
    sdr.on_drop = "ignore"
    try:
        sdr.initialize(cf=a.station or a.band[0], Fs=rates[0])
        sdr.set_gain_relative(a.gain)
        print(f"{caps.driver}: gain {describe_gain(sdr)}; skip now {caps.retune_samples} samples + {caps.retune_settle} s")
        sdr.start_rx()
        if a.station:
            station = a.station
        else:
            station, on, off = find_station(sdr, rates[0], tuple(a.band), away=a.away)
            print(f"signal at {station/1e6:.3f} MHz: {on:.1f} dB on it, {off:.1f} dB {a.away/1e6:g} MHz away")
        sdr.stop_rx()

        rows = []
        for Fs in rates:
            sdr.set_rx_sample_rate(Fs)
            if caps.bandwidths["rx"]:
                sdr.set_bandwidth(min(Fs, 2e6))
            win = max(64, int(Fs / 1e4))    # 0.1 ms
            sdr.start_rx()
            levels = []
            for f in (station + a.away, station):
                sdr.set_rx_freq(f, flush=True)
                levels.append(np.median(centre_power(sdr.read(int(0.05 * Fs), timeout=3), Fs, win)))
            off, on = levels
            thr = (on + off) / 2
            late, dirty = [], 0
            for _ in range(a.trials):   # without flush: where does the signal appear?
                sdr.set_rx_freq(station + a.away, flush=True)
                sdr.read(int(0.05 * Fs), timeout=3)
                time.sleep(random.uniform(0, 0.15))
                sdr.set_rx_freq(station)
                x, m = sdr.read(int(0.3 * Fs), timeout=5, meta=True)
                mark = next((e.offset for e in m.events if e.kind == "freq"), 0)
                late.append(int(np.argmax(centre_power(x, Fs, win) > thr)) * win - mark)
            for _ in range(a.trials):   # with flush: is the first block all on it?
                sdr.set_rx_freq(station + a.away, flush=True)
                sdr.read(int(0.05 * Fs), timeout=3)
                time.sleep(random.uniform(0, 0.15))
                sdr.set_rx_freq(station, flush=True)
                dirty += not np.all(centre_power(sdr.read(int(0.02 * Fs), timeout=3), Fs, win) > thr)
            sdr.stop_rx()
            late = np.array(late)
            skip = caps.retune_samples + int(caps.retune_settle * Fs)
            rows.append((Fs, int(late.max())))
            print(f"{Fs/1e6:7.3f} MS/s  signal {on:6.1f} / {off:6.1f} dB | old samples after the mark: median "
                  f"{int(np.median(late))}, max {late.max()} ({late.max()/Fs*1e3:.1f} ms) | flush skips {skip} "
                  f"({'enough' if skip >= late.max() else 'TOO FEW'}), first block dirty {dirty}/{a.trials}")

        # worst = samples + settle * rate: a fixed part (in-flight transfers) and a time part (settling)
        if len(rows) > 1:
            r = np.array(rows, dtype=float)
            settle, samples = np.polyfit(r[:, 0], r[:, 1], 1)
            settle, samples = max(settle, 0.0), max(samples, 0.0)
            print(f"\nworst ~ {samples:.0f} samples + {settle*1e3:.2f} ms x rate; with margin: "
                  f"RETUNE_SAMPLES = {int(np.ceil(samples / 4096) * 4096)}, RETUNE_SETTLE = {max(0.001, 2 * settle + 0.002):.3f}")
    finally:
        if sdr.state.get("rx"):
            sdr.stop_rx()
        sdr.close()

if __name__ == "__main__":
    main()
