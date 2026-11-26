"""`pysdrlib play`: look at a recording, play it in real time, or serve it like a radio

    pysdrlib play capture.sigmf                         what it is, then its power second by second as it plays
    pysdrlib play capture.cfile --fmt cf32 --rate 2e6 --freq 100e6 --loop
    pysdrlib play capture.sigmf --info                  just what it is
    pysdrlib play capture.sigmf --serve tcp://0.0.0.0:5555 --loop    host it: clients open device file-0

Serving needs pyzmq (`pip install pysdrlib[remote]`); the rest needs nothing more.
"""
import time

import numpy as np

def add_arguments(sub):
    """Add the `play` command to the `pysdrlib` command's subparsers"""
    p = sub.add_parser("play", help="look at a recording, play it in real time, or serve it like a radio",
                       description=__doc__.split("\n\n")[0])
    p.add_argument("path", help="the recording: a SigMF one (NAME.sigmf, or either of its files), or a raw file")
    p.add_argument("--fmt", help="its sample format, for a raw file whose extension doesn't name one (see pysdrlib.Formats: cf32, ci16t, cui8...)")
    p.add_argument("--rate", type=float, help="its sample rate, for a raw file (Hz)")
    p.add_argument("--freq", type=float, help="its centre frequency (Hz; for SigMF: overrides the recording's)")
    p.add_argument("--loop", action="store_true", help="start again at the end")
    p.add_argument("--info", action="store_true", help="say what the recording is, and don't play it")
    p.add_argument("--interval", type=float, default=1.0, metavar="S", help="seconds between lines while playing (default %(default)s)")
    p.add_argument("--fast", action="store_true", help="as fast as it can be read, not at the sample rate")
    p.add_argument("--serve", metavar="ENDPOINT", help="host it like a radio at this C2 endpoint (e.g. tcp://0.0.0.0:5555) until Ctrl+C")
    return p

def _hz(f):
    if f is None:
        return "unknown"
    for unit, scale in (("GHz", 1e9), ("MHz", 1e6), ("kHz", 1e3)):
        if abs(f) >= scale:
            return f"{f / scale:g} {unit}"
    return f"{f:g} Hz"

def describe(f, rate):
    """Lines saying what a `File` is"""
    lines = [f"{f.path}", f"  format {f.fmt.name}" + (f" (SigMF {f.fmt.sigmf})" if f.fmt.sigmf else "") +
             f", {f.max_samp:,} samples" + (f", {f.max_samp / rate:.3f} s at {_hz(rate)}" if rate else ", rate unknown")]
    meta = f.meta
    if meta is not None:
        glob = meta.raw.get("global", {})
        if meta.datetime:
            lines.append(f"  recorded {meta.datetime}" + (f" on {glob['core:hw']}" if glob.get("core:hw") else ""))
        lines.append(f"  {len(meta.captures)} capture(s): " + ", ".join(
            f"{_hz(fr)} from {start:,}" for start, fr in meta.captures[:8]) + (" ..." if len(meta.captures) > 8 else ""))
        if meta.context is not None:
            lines.append("  received " + _received(meta.context))
        if meta.annotations:
            lines.append(f"  {len(meta.annotations)} annotation(s): " + "; ".join(
                f"{a.get('core:sample_start', '?'):,}: {a.get('core:comment') or a.get('core:label') or ''}".strip()
                for a in meta.annotations[:4]) + (" ..." if len(meta.annotations) > 4 else ""))
    return lines

def _received(c):
    """How a recording was received (its `RxContext`), in a line"""
    parts = []
    if c.chain is not None and (c.chain.lo or c.chain.polarization):
        about = []
        if c.chain.lo:
            about.append(f"LO {_hz(c.chain.lo)}" + (f" +-{_hz(c.chain.lo_accuracy)}" if c.chain.lo_accuracy is not None else ""))
        if c.chain.polarization:
            about.append(c.chain.polarization)
        if c.chain.invert:
            about.append("inverted" if c.inverted else "inversion undone")
        what = c.chain.name or ("a converter" if c.chain.lo else "a feed")
        parts.append(f"through {what} ({', '.join(about)})")
    if c.port:
        parts.append(f"on port {c.port}")
    if c.gains:
        parts.append("gains " + ", ".join(f"{k} {v:g} dB" for k, v in c.gains.items()))
    if c.level_offset is not None:
        parts.append(f"level dBm = dBFS {c.level_offset:+g}" + (f" +-{c.level_accuracy:g}" if c.level_accuracy is not None else "")
                     + " dB")
    if c.ppm or c.ppm_accuracy is not None:
        parts.append(f"{c.ppm:+g}" + (f" +-{c.ppm_accuracy:g}" if c.ppm_accuracy is not None else "") + " ppm")
    if c.clock_source:
        parts.append(f"clock {c.clock_source}")
    return ", ".join(parts) or "directly"

def _db(power):
    return 10 * np.log10(power) if power > 0 else -np.inf

def run(args) -> int:
    """`pysdrlib play`: returns the exit status"""
    import pysdrlib
    from .file import File
    try:
        f = File(args.path, args.fmt)
    except (OSError, ValueError, KeyError) as e:
        raise SystemExit(f"error: {e}")
    rate = args.rate or f.rate
    for line in describe(f, rate):
        print(line)
    if args.info:
        return 0
    if not f.fmt.is_complex:
        raise SystemExit(f"error: {f.fmt.name} is a real format: only I/Q recordings can be played")
    if rate is None:
        raise SystemExit("error: the recording doesn't say its sample rate: give --rate")
    open_args = {"path": str(args.path), "fmt": args.fmt, "rate": args.rate, "freq": args.freq, "loop": args.loop,
                 "realtime": not args.fast}
    if args.serve:
        return _serve(args, open_args)
    sdr = pysdrlib.open("file", **open_args)
    sdr.on_drop = "ignore"   # a terminal that can't keep up only skips lines' worth
    try:
        sdr.initialize()
        block = max(1, int(rate * min(args.interval, 1.0) / 4))
        per_line = max(1, int(round(args.interval * rate / block)))
        print(f"playing{' (looping)' if args.loop else ''}: Ctrl+C to stop")
        with sdr.stream(block, meta=True, timeout=1.0) as s:
            acc = peak = 0.0
            n = 0
            for k, (x, m) in enumerate(s, 1):
                p = np.abs(x) ** 2
                acc, peak, n = acc + float(p.sum()), max(peak, float(p.max())), n + len(x)
                if k % per_line == 0:
                    print(f"  {(m.index + len(x)) / rate:8.2f} s  {_hz(m.freq):>12}  power {_db(acc / n):6.1f} dBFS  "
                          f"peak {_db(peak):6.1f} dBFS" + (f"  [{100 * sdr.position / max(1, sdr.length):3.0f}%]"))
                    acc = peak = 0.0
                    n = 0
    except pysdrlib.err.Timeout:
        if not sdr.eof:
            raise
        print("end of the recording")
    except KeyboardInterrupt:
        pass
    finally:
        sdr.close()
    return 0

def _serve(args, open_args):
    try:
        from ..remote import Server
    except ImportError as e:
        raise SystemExit(f"error: --serve needs pyzmq: pip install pysdrlib[remote] ({e})")
    from .. import logger
    import logging
    logger.configure(logging.INFO)
    with Server(args.serve, devices=["file"], lease=15.0) as server:
        worker, _ = server.open_device("file", kwargs={k: v for k, v in open_args.items() if v is not None})
        with worker.lock:   # playing from the start, for viewers; a client that claims it controls it
            worker.device.initialize()
            worker.device.start_rx()
        worker.streaming.set()
        print(f"serving it as {worker.id} on {server.endpoint}: `pysdrlib client {server.endpoint} watch {worker.id} ...`, "
              f"or pysdrlib.device('remote', '{server.endpoint}', id='{worker.id}'); Ctrl+C to stop", flush=True)
        try:
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            pass
    return 0
