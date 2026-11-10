"""Channels on a real multi-channel device (a B210): per-channel controls, two-channel streams, coherence

    python tests/dev/channels.py                    # the first device pysdrlib.find() lists (needs 2 RX channels)
    python tests/dev/channels.py --no-tx            # skip the transmit check
    python tests/dev/channels.py --station 92.3e6   # the coherence check's signal (default: search the FM band)

1. Controls per channel and direction, read back from the hardware (the driver's own getters)
2. Both RX channels streamed together at a low, middle and high rate: aligned blocks, nothing lost
3. Coherence: both channels on one FM station. Sampled together from one LO, the phase between them is steady
   from block to block (it only depends on the antennas' positions); |coherence| near 1 when both antennas hear it
4. TX on both channels: silence at the least gain (only LO leakage gets out), both queues drained in step
"""
import argparse
import time

import numpy as np

from rfcheck import add_device_args, find_station, open_device

def section(name):
    print(f"\n== {name}", flush=True)

def main():
    ap = add_device_args(argparse.ArgumentParser(description=__doc__.splitlines()[0]))
    ap.add_argument("--rates", type=float, nargs="+", default=[2e6, 10e6, 30.72e6, 20e6], help="two-channel rates to stream at")
    ap.add_argument("--seconds", type=float, default=2.0)
    ap.add_argument("--no-tx", action="store_true", help="don't run the transmit check")
    a = ap.parse_args()

    sdr = open_device(a)
    raw = sdr.device
    try:
        c = sdr.caps
        section("caps")
        print("channels", c.channels, "| shared", c.shared, "| antennas", c.antennas)
        if c.channels.get("rx", 0) < 2:
            raise SystemExit("this device has one RX channel")

        section("per-channel controls (read back from the hardware)")
        sdr.initialize(cf=100e6, Fs=2e6)
        sdr.set_rx_freq(433e6, channel=1)
        print("set_freq(433 MHz, channel=1) -> RX LO ch0/ch1:", raw.get_rx_freq(0), raw.get_rx_freq(1),
              "| generic get_freq(0):", sdr.get_rx_freq(0))
        sdr.set_tx_freq(915e6)
        print("set_freq(915 MHz, direction='tx') -> TX ch0/ch1:", raw.get_tx_freq(0), raw.get_tx_freq(1),
              "| RX still", raw.get_rx_freq(0))
        sdr.set_tx_sample_rate(4e6)
        print("set_sample_rate(4 MS/s, direction='tx') -> TX rates", raw.get_tx_rate(0), raw.get_tx_rate(1),
              "| RX", raw.get_rx_rate(0), raw.get_rx_rate(1))
        sdr.set_gain(10, channel=0); sdr.set_gain(50, channel=1)
        print("set_gain(10, ch0), (50, ch1) -> RX gains", raw.get_rx_gain(0), raw.get_rx_gain(1))
        sdr.set_antenna("RX2", channel=0); sdr.set_antenna("TX/RX", channel=1)
        print("antennas RX2 / TX/RX ->", raw.get_rx_antenna(0), raw.get_rx_antenna(1))
        sdr.set_bandwidth(1e6, channel=0); sdr.set_bandwidth(20e6, channel=1)
        print("bandwidth 1 / 20 MHz ->", raw.get_rx_bandwidth(0), raw.get_rx_bandwidth(1))
        for ch in (0, 1):
            sdr.set_antenna("RX2", channel=ch)
            sdr.set_gain_relative(0.6, channel=ch)

        for rate in a.rates:
            section(f"both RX channels at {rate/1e6:g} MS/s for {a.seconds:g} s")
            sdr.set_rx_sample_rate(rate, direction="rx")
            block = int(min(1 << 18, rate * 0.05))
            with sdr.stream(block, timeout=2, meta=True, channel=(0, 1)) as s:
                n, lost, misaligned, t0 = 0, 0, 0, time.monotonic()
                for x, metas in s:
                    n += x.shape[1]
                    lost += sum(m.dropped for m in metas)
                    misaligned += len({m.index for m in metas}) > 1
                    if n >= a.seconds * rate:
                        break
                dt = time.monotonic() - t0
            print(f"{n} samples per channel in {dt:.2f} s ({n/dt/1e6:.2f} MS/s each), lost {lost} (meta), blocks not "
                  f"aligned {misaligned}; driver: {raw.rx_overflows()} overflows, {raw.rx_lost()} samples lost")

        section("coherence: both channels on one signal")
        rate = 2e6
        sdr.set_rx_sample_rate(rate, direction="rx")
        sdr.start_rx(channels=(0, 1))
        if a.station:
            station = a.station
        else:
            station, on, off = find_station(sdr, rate, tuple(a.band))
            print(f"signal at {station/1e6:.1f} MHz ({on - off:.1f} dB above its surroundings, channel 0)")
        sdr.set_rx_freq(station, flush=True)
        phases, mags = [], []
        for _ in range(20):
            x = sdr.read(65536, timeout=2, channel=(0, 1))
            cross = np.vdot(x[1], x[0])
            mags.append(abs(cross) / np.sqrt(np.vdot(x[0], x[0]).real * np.vdot(x[1], x[1]).real))
            phases.append(np.angle(cross))
        sdr.stop_rx()
        spread = np.degrees(np.std(np.unwrap(phases)))
        print(f"phase ch0 - ch1: {np.degrees(np.mean(np.unwrap(phases))):.1f} deg, spread {spread:.1f} deg over 20 blocks; "
              f"|coherence| {np.mean(mags):.2f}")
        print("coherent" if spread < 10 and np.mean(mags) > 0.5 else
              "not coherent here: is an antenna on each RX2 port? (the phase only holds when both hear the signal)")

        if not a.no_tx and c.channels.get("tx", 0) >= 2:
            section("TX on both channels: silence at the least gain")
            sdr.set_tx_sample_rate(1e6)
            for ch in (0, 1):
                sdr.set_gain(0, channel=ch, direction="tx")
            sdr.start_tx(channels=(0, 1))
            try:
                for _ in range(10):
                    sdr.write(np.zeros((2, 100_000), np.complex64))
                time.sleep(0.3)
                s0, s1 = sdr.get_tx_stats(0), sdr.get_tx_stats(1)
            finally:
                sdr.stop_tx()
            print("channel 0:", s0)
            print("channel 1:", s1)
            print("in step" if s0["sent"] == s1["sent"] else "NOT in step")
    finally:
        for flag, stop in (("rx", sdr.stop_rx), ("tx", sdr.stop_tx)):
            if sdr.state.get(flag):
                stop()
        sdr.close()
    print("\ndone")

if __name__ == "__main__":
    main()
