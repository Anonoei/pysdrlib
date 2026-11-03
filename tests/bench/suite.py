"""Benchmarks for the overhead pysdrlib adds on top of raw numpy / the driver.

Time benchmarks report best-of-N seconds per call (min is the most stable statistic on a busy
machine), and, where a bare-numpy equivalent exists, a "floor" so you get an overhead ratio.
Gauge benchmarks report a value from a longer scenario (e.g. dropped samples in a 20 MS/s stream).

Everything except the `hardware` group uses the simulated device (pysdrlib.sim), no hardware.
"""
import gc
import io
import os
import platform
import statistics
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

import numpy as np

N = 131072            # complex samples in one HackRF USB transfer (256 KiB)
MAX_RATE = 20e6       # HackRF's maximum sample rate; the headline "can we keep up" yardstick
SCHEMA = 1


@dataclass
class Case:
    fn: Callable
    floor: Optional[Callable] = None   # bare-numpy equivalent of fn
    items: Optional[int] = None        # samples processed per call -> throughput


@dataclass
class Spec:
    name: str
    group: str
    factory: Callable
    kind: str = "time"                 # "time" -> Case, "gauges" -> {suffix: Gauge}
    slow: bool = False                 # skipped by --quick
    hardware: bool = False


@dataclass
class Gauge:
    value: float
    unit: str = ""
    lower_is_better: bool = True
    abs_tol: float = 0.0               # ignore changes smaller than this when comparing


REGISTRY: dict = {}


def bench(name, group, **kw):
    def deco(factory):
        REGISTRY[name] = Spec(name, group, factory, **kw)
        return factory
    return deco


# ------------------------------------------------------------------ harness
def _time(fn, number):
    gc.collect()
    gc.disable()
    try:
        t0 = time.perf_counter()
        for _ in range(number):
            fn()
        return time.perf_counter() - t0
    finally:
        gc.enable()


def measure(fn, repeat=7, min_time=0.1):
    """Seconds per call: (best, median, loops per timing)"""
    fn()
    number = 1
    while _time(fn, number) < min_time and number < (1 << 22):
        number *= 2
    times = [_time(fn, number) / number for _ in range(repeat)]
    return min(times), statistics.median(times), number


def _entry_time(case: Case, repeat, min_time):
    best, median, number = measure(case.fn, repeat, min_time)
    entry = {"kind": "time", "best": best, "median": median, "loops": number}
    if case.floor is not None:
        floor, _, _ = measure(case.floor, repeat, min_time)
        entry.update(floor=floor, overhead_x=best / floor, overhead_us=(best - floor) * 1e6)
    if case.items:
        entry.update(items=case.items, msps=case.items / best / 1e6, realtime_x=case.items / best / MAX_RATE)
    return entry


def run(names=None, groups=None, quick=False, hardware=False, log=lambda *_: None):
    """Run benchmarks, return the results dict (see `meta()` for the header)."""
    repeat, min_time = (3, 0.02) if quick else (7, 0.1)
    results = {}
    for spec in REGISTRY.values():
        if names and spec.name not in names:
            continue
        if groups and spec.group not in groups:
            continue
        if (quick and spec.slow) or (spec.hardware and not hardware):
            continue
        try:
            made = spec.factory(quick)
        except _Skip as exc:
            log(f"  skip  {spec.name}: {exc}")
            continue
        if spec.kind == "time":
            results[spec.name] = _entry_time(made, repeat, min_time)
            results[spec.name]["group"] = spec.group
            log(format_row(spec.name, results[spec.name]))
        else:
            for suffix, g in made.items():
                name = f"{spec.name}.{suffix}"
                results[name] = {"kind": "gauge", "group": spec.group, "value": g.value, "unit": g.unit,
                                 "lower_is_better": g.lower_is_better, "abs_tol": g.abs_tol}
                log(format_row(name, results[name]))
    return {"meta": meta(quick, hardware), "results": results}


class _Skip(Exception):
    pass


def meta(quick, hardware):
    def git(*args):
        try:
            return subprocess.check_output(["git", *args], text=True, stderr=subprocess.DEVNULL,
                                           cwd=os.path.dirname(__file__)).strip()
        except (OSError, subprocess.CalledProcessError):
            return None
    cpu = platform.processor()
    try:
        with open("/proc/cpuinfo") as f:
            cpu = next(l.split(":", 1)[1].strip() for l in f if l.startswith("model name"))
    except (OSError, StopIteration):
        pass
    import pysdrlib
    return {
        "schema": SCHEMA, "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "commit": git("rev-parse", "--short", "HEAD"), "dirty": bool(git("status", "--porcelain", "--untracked-files=no")),
        "pysdrlib": pysdrlib.__version__, "python": platform.python_version(), "numpy": np.__version__,
        "machine": platform.machine(), "cpu": cpu, "quick": quick, "hardware": hardware,
    }


# ------------------------------------------------------------------ reporting / comparing
def _fmt_time(s):
    return f"{s * 1e6:9.1f} us" if s < 1e-3 else f"{s * 1e3:9.2f} ms"


def format_row(name, e):
    if e["kind"] == "gauge":
        return f"  {name:<34}{e['value']:>12.4g} {e['unit']}"
    extra = ""
    if "overhead_x" in e:
        extra += f"  floor {_fmt_time(e['floor'])}  x{e['overhead_x']:.2f}"
    if "msps" in e:
        extra += f"  {e['msps']:8.1f} MS/s ({e['realtime_x']:.1f}x 20 MS/s)"
    return f"  {name:<34}{_fmt_time(e['best'])}{extra}"


def compare(base, cur, threshold=0.25, min_abs_us=5.0):
    """Rows (name, status, base, cur, change) for benchmarks present in both.
    status: "regression" | "improvement" | "ok". A time must also move by > min_abs_us to count."""
    rows = []
    for name, c in cur["results"].items():
        b = base["results"].get(name)
        if b is None or b["kind"] != c["kind"]:
            continue
        if c["kind"] == "time":
            bv, cv, worse = b["best"], c["best"], c["best"] > b["best"]
            noise = abs(cv - bv) * 1e6 <= min_abs_us
        else:
            bv, cv = b["value"], c["value"]
            lower = c["lower_is_better"]
            worse = cv > bv if lower else cv < bv
            noise = abs(cv - bv) <= c.get("abs_tol", 0.0)
        change = (cv - bv) / bv if bv else (0.0 if cv == bv else float("inf"))
        if noise or abs(change) <= threshold:
            status = "ok"
        else:
            status = "regression" if worse else "improvement"
        rows.append((name, status, bv, cv, change))
    return rows


# ------------------------------------------------------------------ benchmarks: buffer
def _block(n=N, seed=0):
    rng = np.random.default_rng(seed)
    return rng.standard_normal(2 * n).astype(np.float32).view(np.complex64)


@bench("buffer.update", "buffer")
def _(quick):
    from pysdrlib.base.buffer import RotatingBuffer
    block, buf = _block(), RotatingBuffer()
    buf.reset(4 * N)   # fills within a few calls: this measures the steady, full-ring state
    dst = np.empty(N, np.complex64)
    return Case(lambda: buf.update(block), lambda: np.copyto(dst, block), items=N)


@bench("buffer.update_read", "buffer")
def _(quick):
    from pysdrlib.base.buffer import RotatingBuffer
    block, buf = _block(), RotatingBuffer()
    buf.reset(4 * N)
    dst = np.empty(N, np.complex64)
    def floor():
        np.copyto(dst, block)
        return dst.copy()
    return Case(lambda: (buf.update(block), buf.read()), floor, items=N)


@bench("buffer.backlog_5M", "buffer", slow=True)
def _(quick):
    """Consumer finds a 0.5 s backlog (5M samples) and drains it"""
    from pysdrlib.base.buffer import RotatingBuffer
    big, buf = _block(5_000_000), RotatingBuffer()
    buf.reset(5_000_000)
    dst = np.empty_like(big)
    def floor():
        np.copyto(dst, big)
        return dst.copy()
    return Case(lambda: (buf.update(big), buf.read()), floor, items=5_000_000)


@bench("buffer.read_chunks", "buffer")
def _(quick):
    """Drain a 16-block backlog in 8192-sample chunks"""
    from pysdrlib.base.buffer import RotatingBuffer
    block, buf = _block(16 * N), RotatingBuffer()
    buf.reset(16 * N)
    def fn():
        buf.update(block)
        while buf.available:
            buf.read(8192)
    return Case(fn, items=16 * N)


# ------------------------------------------------------------------ benchmarks: conversion / drivers
def _raw_int8(n=N):
    return np.random.default_rng(1).integers(-128, 128, 2 * n, dtype=np.int8)


def _make_convert(fmt_name):
    def factory(quick):
        from pysdrlib.base import convert
        fmt = getattr(convert, fmt_name)
        raw = fmt.from_complex64(_block())
        # floor: the cheapest numpy could do for this layout (one scaling pass into a float32 buffer)
        floor = None
        if fmt.dtype is not None and fmt.kind != "float":
            comps = raw.view(fmt.dtype)
            out, scale = np.empty(len(comps), np.float32), np.float32(1 / fmt.full_scale)
            floor = lambda: np.multiply(comps, scale, out=out)
        return Case(lambda: fmt.to_complex64(raw), floor, items=N)
    return factory

for _fmt in ("INT8", "UINT8", "INT16", "INT12", "INT12_PACKED", "FLOAT32"):
    bench(f"convert.{_fmt.lower()}", "convert")(_make_convert(_fmt))


@bench("hackrf.rx_callback", "convert")
def _(quick):
    """The production HackRF callback (convert + buffer), floor = convert alone"""
    try:
        from pysdrlib.hackrf import hackrf as hm
    except ImportError as exc:
        raise _Skip(f"hackrf unavailable: {exc}")
    raw = _raw_int8()
    sdr = hm.HackRF()
    sdr._buffer.reset(4 * N)
    fmt = hm.HackRF.SAMPLE_FORMAT
    return Case(lambda: sdr._rx_callback(raw, len(raw), len(raw)), lambda: fmt.to_complex64(raw), items=N)


# ------------------------------------------------------------------ benchmarks: generic device layer
def _sim(Fs=20e6, realtime=False, **kw):
    from pysdrlib.sim import Sim
    dev = Sim(realtime=realtime, **kw)
    dev.open()
    dev.initialize(cf=100e6, Fs=Fs)
    return dev


@bench("sim.pump_16_blocks", "device")
def _(quick):
    """Device -> callback -> convert -> buffer -> get_samples for 16 transfers (2.1M samples)"""
    dev, raw = _sim(), _raw_int8()
    fmt = dev.SAMPLE_FORMAT
    def floor():
        for _ in range(16):
            fmt.to_complex64(raw)
    return Case(lambda: (dev.pump(16), dev.get_samples()), floor, items=16 * N)


@bench("device.set_freq", "device")
def _(quick):
    dev = _sim()
    return Case(lambda: dev.set_freq(100e6))


@bench("device.set_rx_gain", "device")
def _(quick):
    dev = _sim()
    return Case(lambda: dev.set_rx_gain(60))


@bench("device.get_samples_empty", "device")
def _(quick):
    dev = _sim()
    return Case(dev.get_samples)


@bench("sim.stream_20MS", "stream", kind="gauges")
def _(quick):
    """Paced at 20 MS/s with a polling consumer: can pysdrlib keep real time without dropping?"""
    seconds = 0.5 if quick else 1.5
    dev = _sim(Fs=MAX_RATE, realtime=True)
    dev.reset_stats()
    got, deadline = 0, time.perf_counter() + seconds
    dev.start_rx()
    t0 = time.perf_counter()
    while time.perf_counter() < deadline:
        got += len(dev.get_samples())
        time.sleep(0.002)
    dev.stop_rx()
    elapsed = time.perf_counter() - t0
    got += len(dev.get_samples())
    expected = dev.blocks * dev.block_samples
    return {
        "dropped": Gauge(dev.dropped, "samples", abs_tol=0),
        "max_lag": Gauge(dev.max_lag * 1e3, "ms", abs_tol=2.0),
        "delivered": Gauge(got / expected if expected else 0, "of produced", lower_is_better=False, abs_tol=1e-6),
        "rate": Gauge(dev.blocks * dev.block_samples / elapsed / 1e6, "MS/s", lower_is_better=False, abs_tol=0.5),
    }


# ------------------------------------------------------------------ benchmarks: file / format IO
_FORMATS_READ = ["i8t", "i16t", "f32", "i4t", "i8o", "i16o", "ci8t", "ci16t", "cf64"]
_FORMATS_WRITE = ["i8t", "ci16t", "cf64", "i4t"]
M = 1 << 20


def _values(fmt):
    rng = np.random.default_rng(2)
    if fmt.is_complex:
        lim = {"ci8t": 7, "ci16t": 127}.get(fmt.name, 1.0)
        return lim * (rng.uniform(-1, 1, M) + 1j * rng.uniform(-1, 1, M))
    if fmt.name.startswith("f"):
        return rng.standard_normal(M)
    lim = {"i4t": 7, "i4o": 7}.get(fmt.name, 100)
    return rng.integers(-lim, lim, M)


for _name in _FORMATS_READ:
    def _make_read(fname):
        def factory(quick):
            from pysdrlib.base import Formats
            fmt = Formats[fname]
            buf = io.BytesIO()
            fmt.write(buf, _values(fmt))
            buf.seek(0)
            def fn():
                buf.seek(0)
                return fmt.read(buf, M)
            return Case(fn, items=M)
        return factory
    bench(f"formats.read.{_name}", "formats")(_make_read(_name))

for _name in _FORMATS_WRITE:
    def _make_write(fname):
        def factory(quick):
            from pysdrlib.base import Formats
            fmt, values, buf = Formats[fname], None, io.BytesIO()
            values = _values(fmt)
            def fn():
                buf.seek(0)
                fmt.write(buf, values)
            return Case(fn, items=M)
        return factory
    bench(f"formats.write.{_name}", "formats")(_make_write(_name))


@bench("file.next_8192", "file")
def _(quick):
    """File.next vs reading the same chunk straight from the format"""
    import tempfile
    from pysdrlib.base import Formats
    from pysdrlib.file import File
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".bin")
    Formats.i16t.write(tmp, np.arange(M) % 30000)
    tmp.close()
    f = File()
    f.set_path(tmp.name)
    f.set_fmt("i16t")
    f.open()
    raw = open(tmp.name, "rb")
    def fn():
        if f.cur_samp + 8192 > f.max_samp:
            f.reset()
        return f.next(8192)
    def floor():
        raw.seek(0)
        return Formats.i16t.read(raw, 8192)
    return Case(fn, floor, items=8192)


# ------------------------------------------------------------------ benchmarks: real hardware
def _hackrf_stream(Fs, seconds):
    """Stream from a real HackRF; measure the Python-side callback cost."""
    from pysdrlib import hackrf
    spent, calls = [0.0], [0]
    with hackrf.Device() as sdr:
        orig = sdr._rx_callback
        def timed(buffer, valid, buflen):
            t = time.perf_counter()
            rtr = orig(buffer, valid, buflen)
            spent[0] += time.perf_counter() - t
            calls[0] += 1
            return rtr
        sdr._rx_callback = timed   # picked up by start_rx
        sdr.initialize(cf=100e6, Fs=Fs)
        sdr._buffer.clear_dropped()
        got, t0 = 0, time.perf_counter()
        sdr.start_rx()
        while time.perf_counter() - t0 < seconds:
            got += len(sdr.get_samples())
            time.sleep(0.005)
        sdr.stop_rx()
        elapsed = time.perf_counter() - t0
        got += len(sdr.get_samples())
        dropped = sdr._buffer.dropped
    return {
        "dropped": Gauge(dropped, "samples", abs_tol=0),
        "rate": Gauge(got / elapsed / 1e6, "MS/s", lower_is_better=False, abs_tol=0.5),
        "callback_us": Gauge(spent[0] / max(calls[0], 1) * 1e6, "us/transfer", abs_tol=20),
        "callback_load": Gauge(spent[0] / elapsed * 100, "% of one core", abs_tol=0.5),
    }


for _rate in (10, 20):
    def _make_hw(rate):
        def factory(quick):
            try:
                return _hackrf_stream(rate * 1e6, 1.0 if quick else 3.0)
            except Exception as exc:  # no device / driver missing
                raise _Skip(f"no usable HackRF: {exc}")
        return factory
    bench(f"hackrf.stream_{_rate}MS", "hardware", kind="gauges", hardware=True)(_make_hw(_rate))
