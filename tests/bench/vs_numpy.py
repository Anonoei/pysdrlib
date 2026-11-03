"""Native C++ vs the original pure-numpy implementation, on identical data.

    python tests/bench/vs_numpy.py            # one 131,072-sample transfer (a HackRF USB transfer)
    python tests/bench/vs_numpy.py --samples 1000000

The numpy versions are the oracles in tests/reference/. Times are best-of-N seconds per call (the minimum is
the most stable statistic on a busy machine); `speedup` is numpy time / native time.
"""
import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/, for `reference`
from reference import buffer_numpy, convert_numpy  # noqa: E402

from pysdrlib.base import buffer, convert  # noqa: E402

FORMATS = {
    "int8 (HackRF)": convert.INT8, "uint8 (RTL-SDR)": convert.UINT8, "int16": convert.INT16,
    "int12 in 16": convert.INT12, "int12 packed": convert.INT12_PACKED, "float32 (RX888)": convert.FLOAT32,
}


def best(fn, repeat=9, min_time=0.1):
    """Smallest seconds per call over `repeat` runs, each long enough to time"""
    fn()
    loops = 1
    while True:
        t = time.perf_counter()
        for _ in range(loops):
            fn()
        if time.perf_counter() - t >= min_time / 4:
            break
        loops *= 2
    times = []
    for _ in range(repeat):
        t = time.perf_counter()
        for _ in range(loops):
            fn()
        times.append((time.perf_counter() - t) / loops)
    return min(times)


def ref_format(f):
    return convert_numpy.SampleFormat(f.kind, f.bits, f.storage, f.packed, f.byteorder)


def row(name, numpy_s, native_s, n):
    print(f"  {name:<34}{numpy_s * 1e6:>10.1f} us{native_s * 1e6:>10.1f} us{numpy_s / native_s:>8.2f}x"
          f"{n / native_s / 1e6:>10.0f} MS/s")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--samples", type=int, default=131072, help="complex samples per call")
    n = ap.parse_args(argv).samples
    rng = np.random.default_rng(0)
    iq = (rng.uniform(-1, 1, 2 * n).astype(np.float32)).view(np.complex64)

    print(f"{n:,} complex samples per call; numpy {np.__version__}\n")
    head = f"  {'':<34}{'numpy':>13}{'native C++':>13}{'speedup':>9}{'native rate':>14}"

    print("raw -> complex64 (every received transfer)\n" + head)
    for name, f in FORMATS.items():
        raw = f.from_complex64(iq)
        raw = raw if f.packed else raw.view(np.uint8)
        r = ref_format(f)
        row(name, best(lambda: r.to_complex64(raw)), best(lambda: f.to_complex64(raw)), n)

    print("\ncomplex64 -> raw (remote server wire formats)\n" + head)
    for name in ("int8 (HackRF)", "uint8 (RTL-SDR)", "int16", "int12 packed"):
        f = FORMATS[name]
        r = ref_format(f)
        row(name, best(lambda: r.from_complex64(iq)), best(lambda: f.from_complex64(iq)), n)

    print("\nsample ring (Sim, remote client, and the device queue)\n" + head)
    cap = 4 * n
    old, new = buffer_numpy.RotatingBuffer(), buffer.RotatingBuffer()
    old.reset(cap)
    new.reset(cap)
    row("update (steady, ring full)", best(lambda: old.update(iq)), best(lambda: new.update(iq)), n)
    def cycle(b):
        b.update(iq)
        return b.read()
    row("update + read all", best(lambda: cycle(old)), best(lambda: cycle(new)), n)
    big = np.zeros(8 * n, dtype=np.complex64)
    def drain(b):
        b.reset(8 * n)
        b.update(big)
        while len(b.read(n)):
            pass
    row("drain 8 transfers in chunks", best(lambda: drain(old), repeat=5), best(lambda: drain(new), repeat=5), 8 * n)

    print("\nwhole receive path for one transfer (convert + queue)\n" + head)
    f = convert.INT8
    raw = f.from_complex64(iq)
    r, ring = ref_format(f), buffer_numpy.RotatingBuffer()
    ring.reset(cap)
    def python_path():           # what a driver callback did before: numpy convert, then the numpy ring
        ring.update(r.to_complex64(raw))
    try:
        from pysdrlib.hackrf import lib
        dev = lib.hackrf.device()
        dev.set_rx_buffer(cap)
        row("int8: old Python callback vs native", best(python_path), best(lambda: dev._dispatch_rx(raw)), n)
    except ImportError as exc:
        print(f"  (hackrf extension unavailable: {exc})")


if __name__ == "__main__":
    main()
