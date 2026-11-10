"""The generic API on a real device: find, caps, controls, blocking reads at full rate, gap reporting, drop policy

    python tests/dev/api_check.py                 # the first device pysdrlib.find() lists
    python tests/dev/api_check.py --driver rtl_sdr

Prints what it sees; read it against what the device should do (see test_generic_api.py for the same on fakes).
"""
import argparse
import time

import pysdrlib
from pysdrlib import err

from rfcheck import add_device_args, describe_gain, open_device

def section(name):
    print(f"\n== {name}", flush=True)

def main():
    ap = add_device_args(argparse.ArgumentParser(description=__doc__.splitlines()[0]))
    ap.add_argument("--seconds", type=float, default=3.0, help="how long to stream at each rate")
    ap.add_argument("--rates", type=float, nargs="+", help="sample rates to stream at (default: the device's lowest and highest)")
    a = ap.parse_args()

    section("find")
    for info in pysdrlib.find():
        print(info)
    sdr = open_device(a)
    try:
        section("caps")
        c = sdr.caps
        print("driver", c.driver, "| channels", c.channels, "| freq", c.freq, "| rates", c.rates)
        print("gains", {n: (s.min, s.max, s.step) for n, s in c.gains["rx"].items()}, "| modes", c.gain_modes["rx"])
        print("bandwidths", [r.lo for r in c.bandwidths["rx"]], "| settings", list(c.settings), "| info", c.info)
        print("retune skip", c.retune_samples, "samples +", c.retune_settle, "s")

        section("controls")
        lo, hi = c.rates[0].lo, c.rates[-1].hi
        sdr.initialize(cf=c.freq[0].clip(100e6), Fs=lo)
        print("relative gain 0.5 ->", sdr.set_rx_gain(0.5), describe_gain(sdr))
        if c.bandwidths["rx"]:
            print("bandwidth for", lo, "->", sdr.set_rx_bandwidth(lo))
        if len(c.gain_modes["rx"]) > 1:
            print("gain mode ->", sdr.set_rx_gain_mode("auto"), sdr.set_rx_gain_mode("manual"))
        print("freq correction 15 ppm ->", sdr.set_freq_correction(15.0), "| freq", sdr.get_rx_freq(), "| rate", sdr.get_rx_sample_rate())
        sdr.set_freq_correction(0)

        rates = sorted(a.rates or {lo, hi})
        for rate in rates:
            section(f"{rate/1e6:g} MS/s for {a.seconds:g} s")
            sdr.set_rx_sample_rate(rate)
            block = int(min(1 << 18, max(1024, rate * 0.05)))
            with sdr.stream(block, timeout=max(2.0, 4 * block / rate), meta=True) as s:
                idx, lost, n, t0 = [], 0, 0, time.monotonic()
                for x, m in s:
                    idx.append(m.index); lost += m.dropped; n += len(x)
                    if n >= a.seconds * rate:
                        break
                dt = time.monotonic() - t0
            gapless = all(b - a_ == block for a_, b in zip(idx, idx[1:]))
            print(f"{n} samples in {dt:.2f} s ({n/dt/1e6:.2f} MS/s), lost {lost}, indices contiguous {gapless}, {getattr(sdr, 'stats', '')}")

        if not hasattr(sdr, "stats"):
            print("\n(no driver statistics on this device: skipping the drop checks)")
            return
        section("a reader that falls behind: the gap matches the device's own count")
        sdr.set_rx_sample_rate(rates[-1])
        sdr.on_drop = "ignore"
        sdr.start_rx()
        sdr.read(100_000, timeout=2)
        before = sdr.stats.dropped
        time.sleep(1.0)
        _, m = sdr.read(100_000, timeout=2, meta=True)
        print(f"meta: {m.dropped} lost at {m.gaps}; device dropped {sdr.stats.dropped - before}")
        sdr.on_drop = "raise"
        time.sleep(1.0)
        try:
            sdr.read(100_000, timeout=2)
            print("on_drop='raise': nothing raised (the queue may be longer than 1 s at this rate)")
        except err.Dropped as e:
            print("on_drop='raise' raised:", e)
        sdr.stop_rx()
    finally:
        if sdr.state.get("rx"):
            sdr.stop_rx()
        sdr.close()
    print("\ndone")

if __name__ == "__main__":
    main()
