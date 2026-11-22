"""Command line for the remote server, relay and client

    pysdrlib server --bind tcp://0.0.0.0:5555 --port-range 6000-6100 --devices <driver1>,<driver2>
    pysdrlib client tcp://host:5555 list
    pysdrlib client tcp://host:5555 info <driver>-0
    pysdrlib client tcp://host:5555 rx <driver> --freq 100e6 --rate 10e6 -o out.cfile -t 5
    pysdrlib client tcp://host:5555 rx --serial <serial> --program myapp -o out.cfile -t 5
    pysdrlib client tcp://host:5555 rx --need freq=433e6 --need rate=2e6 --freq 433.92e6 --rate 10e6 -o out.cfile -t 5
    pysdrlib client tcp://host:5555 watch <driver>-0 -o out.cfile -t 5
    pysdrlib client tcp://host:5555 rx <driver> --freq 433.92e6 --rate 2e6 -f ci8 -o capture.sigmf -t 10
    pysdrlib client tcp://host:5555 tx <driver> -i out.cfile --freq 915e6 --rate 2e6 --tx-gain 0 --tx-wire int8
    pysdrlib client tcp://host:5555 set <driver>-0 rx_freq=101e6 rx_lna_gain=16
    pysdrlib relay tcp://host:5555 <driver>-0 --bind tcp://127.0.0.1:5556      # programs here share one connection to it

Recordings are raw complex64 (GNU Radio `.cfile`) by default; `-f` picks another format (ci16, ci8, cu8...), and an
output ending in `.sigmf` (or `--sigmf`) records SigMF, with the rate, frequency, retunes and lost samples in its
metadata. Transmit files are raw complex64.
--rx-wire / --tx-wire choose the format samples cross the network in (native = the device's own layout).
"""
import argparse
import json
import sys
import time

def _number(text):
    """'100e6' -> 100000000.0, '16' -> 16, anything else stays a string"""
    for kind in (int, float):
        try:
            return kind(text)
        except ValueError:
            pass
    return text

def _port_range(text):
    low, _, high = text.partition("-")
    if not high or int(low) > int(high):
        raise argparse.ArgumentTypeError("expected LOW-HIGH, e.g. 6000-6100")
    return int(low), int(high)

def add_parsers(sub):
    """Add the `server`, `relay` and `client` commands to the subparsers of the `pysdrlib` command"""
    s = sub.add_parser("server", help="host the SDRs connected to this machine")
    s.add_argument("--bind", default="tcp://127.0.0.1:5555", help="C2 endpoint (default %(default)s; use tcp://0.0.0.0:5555 to serve the network)")
    s.add_argument("--port-range", type=_port_range, help="LOW-HIGH ports for per-device sockets (default: any free port)")
    s.add_argument("--devices", help="comma separated device types clients may open (default: all)")
    s.add_argument("--open", action="append", default=[], metavar="TYPE[:KEY=VALUE,...]",
                   help="open a device at startup, uncontrolled until a client claims it; KEY=VALUE pairs go to its open() "
                        "(e.g. file:path=cap.sigmf,loop=true or tcpiq:host=10.0.0.5,port=1234) (repeatable)")
    s.add_argument("--frontends", metavar="FILE", help="JSON (or TOML) file saying what sits between each radio and the air (LNB or converter LO, tx chain, antenna), so clients ask for over-the-air frequencies; see pysdrlib.remote.frontends")
    s.add_argument("--wire", "--rx-wire", dest="wire", default="native", help="default RX wire format: native (device's own sample layout, default), complex64, int8, int16, ...")
    s.add_argument("--lease", type=float, default=15.0, help="seconds a silent controller keeps control, 0 = forever (default %(default)s)")
    s.add_argument("--no-shm", dest="shm", action="store_false", help="don't offer clients on this machine the samples in shared memory")
    s.add_argument("--advertise", action="store_true", help="announce this server on the local network (mDNS) so `pysdrlib discover` finds it; needs a --bind others can reach")
    s.add_argument("--advertise-name", metavar="NAME", help="the name to announce (default: this machine's host name)")
    s.add_argument("--scan-interval", type=float, default=10.0, help="seconds between looks at which radios are attached, logging ones that appear or vanish, 0 = only at startup (default %(default)s)")
    s.add_argument("--uhd-log", metavar="LEVEL", help="how much of UHD's own console log to show: trace, debug, info, warning, error, fatal or off "
                   "(default: warning, or UHD_LOG_CONSOLE_LEVEL if set)")
    s.add_argument("--log-timestamps", action="store_true", help="prefix log lines with a time (default off: journald adds one)")
    s.add_argument("--stats-interval", type=float, default=60.0, metavar="S",
                   help="seconds between log lines per streaming device: rate published, subscribers, drops; 0 = none (default %(default)s)")
    s.add_argument("-v", "--verbose", action="store_true", help="also log debug detail")

    y = sub.add_parser("relay", help="host a device of another server here, so programs on this machine share one connection to it")
    y.add_argument("address", help="the far server's C2 endpoint, e.g. tcp://host:5555")
    y.add_argument("device", nargs="?", help="id of a device it hosts, e.g. hackrf-0 (or use --open)")
    y.add_argument("--open", metavar="TYPE", help="open a new device of this type on the far server instead (the relay controls it)")
    y.add_argument("--control", choices=("auto", "yes", "no"), default="auto",
                   help="take control of the far device: if it is free (auto, default), insist (yes), or only watch it (no)")
    y.add_argument("--name", help="the id the device has here (default: the far device's)")
    y.add_argument("--bind", default="tcp://127.0.0.1:5556", help="where local programs connect (default %(default)s)")
    y.add_argument("--timeout", type=float, default=5.0, help="seconds the far server may take to answer")
    y.add_argument("--stats-interval", type=float, default=60.0, metavar="S", help="seconds between stats log lines; 0 = none (default %(default)s)")
    y.add_argument("--lease", type=float, default=15.0, help="seconds a silent local controller keeps control, 0 = forever (default %(default)s)")
    y.add_argument("--no-shm", dest="shm", action="store_false", help="don't offer programs on this machine the samples in shared memory")
    y.add_argument("-v", "--verbose", action="store_true")
    y.add_argument("--advertise", action="store_true", help="announce this relay on the local network (mDNS) so `pysdrlib discover` finds it; needs a --bind others can reach")
    y.add_argument("--advertise-name", metavar="NAME", help="the name to announce (default: this machine's host name)")

    d = sub.add_parser("discover", help="list the pysdrlib servers announcing themselves on the local network")
    d.add_argument("--timeout", type=float, default=2.0, help="seconds to listen (default %(default)s)")
    d.add_argument("--list", action="store_true", help="also ask each server what it has (as `client ADDRESS list`)")
    d.add_argument("--all-versions", action="store_true", help="also show servers speaking another protocol version")

    c = sub.add_parser("client", help="talk to a pysdrlib server")
    c.add_argument("address", help="server C2 endpoint, e.g. tcp://host:5555")
    c.add_argument("--timeout", type=float, default=5.0)
    cmd = c.add_subparsers(dest="cmd", required=True)

    cmd.add_parser("list", help="device types and hosted devices")

    m = cmd.add_parser("stats", help="what each hosted device has published, to whom, and what was lost")
    m.add_argument("device", nargs="?", help="just this hosted device")
    m.add_argument("--json", action="store_true", help="all of it, as JSON")

    i = cmd.add_parser("info", help="capabilities of a hosted device")
    i.add_argument("device")

    r = cmd.add_parser("rx", help="open a new device, configure it and record")
    r.add_argument("type", nargs="?", help="device type, e.g. hackrf (optional with --serial, --label or --need: any radio that fits)")
    _choose_args(r)
    _record_args(r)
    r.add_argument("--rx-wire", "--wire", dest="rx_wire", help="RX wire format for this device (default: the server's)")
    r.add_argument("--init", action="append", default=[], metavar="KEY=VALUE", help="device constructor argument (repeatable)")
    r.add_argument("--arg", action="append", default=[], metavar="KEY=VALUE",
                   help="argument to the device's open(), e.g. path=cap.sigmf for `file`, host=... for `tcpiq` (repeatable)")
    r.add_argument("--freq", type=_number)
    r.add_argument("--rate", type=_number)
    r.add_argument("--rx-gain", type=_number)
    r.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="device specific setter, e.g. rx_lna_gain=16 (repeatable)")

    w = cmd.add_parser("watch", help="record from an already hosted device without controlling it")
    w.add_argument("device")
    _record_args(w)

    st = cmd.add_parser("set", help="change settings on a hosted device (takes control briefly)")
    st.add_argument("device")
    st.add_argument("settings", nargs="+", metavar="KEY=VALUE", help="calls set_<key>(value): rx_freq, tx_freq, rx_sample_rate, rx_gain, rx_lna_gain, ...")

    t = cmd.add_parser("tx", help="open a new device, configure it and transmit a file")
    t.add_argument("type", nargs="?", help="device type, e.g. hackrf (optional with --serial, --label or --need)")
    _choose_args(t)
    t.add_argument("-i", "--input", required=True, help="file of raw complex64 to transmit")
    t.add_argument("--tx-wire", default="native", help="TX wire format: native (device's own sample layout, default), complex64, int8, int16, ...")
    t.add_argument("--freq", type=_number)
    t.add_argument("--rate", type=_number)
    t.add_argument("--tx-gain", type=_number, help="total TX gain in dB (default: the device's)")
    t.add_argument("--loop", action="store_true", help="repeat the file until Ctrl+C")
    t.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="device specific setter, e.g. tx_if_gain=0 (repeatable)")
    t.add_argument("--init", action="append", default=[], metavar="KEY=VALUE", help="device constructor argument (repeatable)")
    t.add_argument("--arg", action="append", default=[], metavar="KEY=VALUE", help="argument to the device's open() (repeatable)")

def build_parser():
    """A parser of just the remote commands, for `main` here"""
    p = argparse.ArgumentParser(prog="pysdrlib", description="pysdrlib remote server, relay and client")
    add_parsers(p.add_subparsers(dest="mode", required=True))
    return p

def _choose_args(p):
    p.add_argument("--program", default="pysdrlib client", help="the program name the server shows for who has the radio (default %(default)r)")
    p.add_argument("--serial", help="open the unit with this serial number")
    p.add_argument("--label", help="open a unit whose label contains this (see `list`)")
    p.add_argument("--need", action="append", default=[], metavar="KEY=VALUE",
                   help="a radio must meet this or it is not used: freq (Hz, or LOW:HIGH to tune across), rate, bandwidth, channels, tx=true (repeatable; --freq/--rate are what you want)")

def _choice(args, **want):
    """RemoteDevice arguments from --program/--serial/--label/--need; --freq and --rate are what the program wants"""
    need = dict(_need(n) for n in args.need)
    want = {k: v for k, v in want.items() if v is not None}
    if args.type is not None and not (args.serial or args.label or need):
        want = {}       # a plain `rx <type>` opens a new device of it as always: --freq and --rate are only wanted when choosing
    if args.type is None and not (args.serial or args.label or need or want):
        raise SystemExit("give a device type, or --serial, --label, --need, --freq or --rate to choose a radio")
    return dict(program=args.program, serial=args.serial, label=args.label, need=need or None, want=want or None)

def _record_args(p):
    p.add_argument("-o", "--output", required=True,
                   help="file to record to; a name ending in .sigmf records SigMF (NAME.sigmf-data and NAME.sigmf-meta)")
    p.add_argument("-f", "--format", default="cf32",
                   help="sample format of the file (default %(default)s: two float32, a GNU Radio .cfile): see pysdrlib.Formats, "
                        "e.g. ci16, ci8, cu8; integer formats saturate at full scale")
    p.add_argument("--sigmf", action="store_true", help="record SigMF whatever the output's name")
    p.add_argument("-n", "--samples", type=int, help="stop after this many samples")
    p.add_argument("-t", "--time", type=float, help="stop after this many seconds")

def _value(text):
    """A command line value: a number, true/false, none, or the text"""
    lower = text.strip().lower()
    if lower in ("true", "false"):
        return lower == "true"
    if lower == "none":
        return None
    return _number(text)

def _setting(item):
    key, eq, value = item.partition("=")
    if not eq:
        raise SystemExit(f"expected KEY=VALUE, got {item!r}")
    return key.strip(), _value(value)

def _open_spec(spec):
    """`TYPE[:KEY=VALUE,...]` -> (type, {key: value})"""
    dtype, _, rest = spec.partition(":")
    return dtype.strip(), dict(_setting(item) for item in rest.split(",") if item.strip())

def _need(item):
    """`--need KEY=VALUE`: freq may be a span `LOW:HIGH` (Hz); tx is true or false (checked by `choose.normalize`)"""
    key, value = _setting(item)
    if isinstance(value, str) and ":" in value:
        low, _, high = value.partition(":")
        value = [_number(low), _number(high)]
    return key, value

def _apply(dev, settings):
    for key, value in settings:
        name = key if key.startswith("set_") else f"set_{key}"
        print(f"{name}({value}) -> {getattr(dev, name)(value)}")

def _record(dev, args):
    """Record the stream to `args.output` until -n samples or -t seconds (or Ctrl-C). The RxMeta of every block goes to
    the sink: a SigMF recording gets the rate, frequency, retunes and lost samples"""
    from ..file import FileSink
    from ..base import Formats
    if args.samples is None and args.time is None:
        raise SystemExit("give a stop condition: -n SAMPLES and/or -t SECONDS")
    try:
        fmt = Formats.get(args.format)
    except KeyError as e:
        raise SystemExit(f"error: {e.args[0]}")
    rate = dev.get_rx_sample_rate() or 1e6
    total = 0
    start = time.monotonic()
    sink = FileSink(args.output, fmt.name, clip=True, scale=True, sigmf=True if args.sigmf else None, hw=dev.id)
    with sink:
        try:
            while args.samples is None or total < args.samples:
                if args.time is not None and time.monotonic() - start >= args.time:
                    break
                count = CHUNK_SAMPLES if args.samples is None else min(CHUNK_SAMPLES, args.samples - total)
                x, m = dev.read(count, timeout=args.timeout + count / rate, meta=True)
                sink.write(x, meta=m)
                total += len(x)
        except KeyboardInterrupt:
            pass
    lost = f" ({dev.dropped_blocks} blocks dropped)" if dev.dropped_blocks else ""
    clipped = f", {sink.clipped:,} clipped" if sink.clipped else ""
    print(f"wrote {total:,} samples to {sink.path}{' (SigMF)' if sink.sigmf else ''}{clipped}{lost}")

CHUNK_SAMPLES = 65536

def _transmit(dev, args):
    import numpy as np
    total = 0
    try:
        with open(args.input, "rb") as f:
            if not f.seek(0, 2):
                raise SystemExit(f"{args.input} is empty")
            f.seek(0)
            dev.start_tx()
            try:
                while True:
                    samples = np.fromfile(f, dtype=np.complex64, count=CHUNK_SAMPLES)
                    if not samples.size:
                        if not args.loop:
                            break
                        f.seek(0)
                        continue
                    total += dev.write(samples)
                # write() returns once queued: let the device send what is still pending before stopping
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    stats = dev.get_tx_stats()
                    if stats["written"] >= total and not stats["pending"]:
                        break
                    time.sleep(0.05)
            finally:
                dev.stop_tx()
    except KeyboardInterrupt:
        pass
    except FileNotFoundError:
        raise SystemExit(f"no such file: {args.input}")
    print(f"transmitted {total:,} samples from {args.input}")

def run_server(args):
    """`pysdrlib server`: host devices until Ctrl+C or SIGTERM. Logs to stderr (the journal, under systemd)"""
    import logging
    import os
    from .. import logger
    if args.uhd_log:   # UHD reads this when its library loads (the first scan), so set it before then
        os.environ["UHD_LOG_CONSOLE_LEVEL"] = args.uhd_log
    logger.configure(logging.DEBUG if args.verbose else logging.INFO, timestamps=args.log_timestamps)
    try:
        _serve(args, logger.new("remote.cli"))
    finally:
        logger.unconfigure()

def _serve(args, log):
    import signal
    from .server import Server
    from .discovery import NotAdvertisable
    devices = args.devices.split(",") if args.devices else None
    try:
        server = Server(args.bind, port_range=args.port_range, devices=devices, lease=args.lease, wire=args.wire, shm=args.shm,
                        scan_interval=args.scan_interval, frontends=args.frontends, advertise=args.advertise,
                        name=args.advertise_name, stats_interval=args.stats_interval)
    except NotAdvertisable as e:
        raise SystemExit(f"--advertise: {e}")
    except ValueError as e:     # a frontends file that cannot be read or is wrong
        raise SystemExit(f"--frontends: {e}")
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))   # systemd stop: shut down cleanly
    log.info("pysdrlib server on %s (device types: %s; lease %gs; shm %s)", server.endpoint, ", ".join(server.types()),
             args.lease, "on" if args.shm else "off")
    try:
        server.scan()
    except Exception:
        log.exception("looking for radios failed")
    try:
        for spec in args.open:
            dtype, kwargs = _open_spec(spec)
            server.open_device(dtype, kwargs=kwargs)
    except Exception as e:
        server.stop()
        raise SystemExit(f"failed to open device: {e}")
    try:
        server.serve_forever()
    finally:
        log.info("server stopped")

def run_relay(args):
    """`pysdrlib relay`: host a far device here until Ctrl+C, or until the far server stops answering"""
    import logging
    from .. import logger
    if args.verbose:
        logger.configure(logging.DEBUG)
    try:
        _relay(args)
    finally:
        if args.verbose:
            logger.unconfigure()

def _relay(args):
    from .server import Server
    if (args.device is None) == (args.open is None):
        raise SystemExit("error: give the id of a device on the far server, or --open TYPE")
    try:
        server = Server(args.bind, lease=args.lease, shm=args.shm, advertise=args.advertise, name=args.advertise_name, kind="relay",
                        stats_interval=args.stats_interval)
    except ValueError as e:
        raise SystemExit(f"error: {e}")
    try:
        worker = server.relay(args.address, args.device, open=args.open, name=args.name, timeout=args.timeout,
                              control={"auto": "auto", "yes": True, "no": False}[args.control])
    except BaseException:
        server.stop()
        raise
    mode = "controlling" if worker.upstream.token else "watching"
    print(f"relaying {worker.upstream.id} of {args.address} ({mode}) as {worker.id} on {server.endpoint}; Ctrl+C to stop")
    try:
        server.start()
        while not worker.lost.wait(0.5):
            pass
        raise SystemExit(f"error: the server at {args.address} stopped answering")
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()

def format_antenna(info):
    """One antenna as text, e.g. `ANT500 (Whip / monopole, length 20-88 cm, 50 ohm)`; `info` is `Antenna.describe()`"""
    if not info:
        return "none"
    parts = []
    if info.get("kind_name"):
        parts.append(info["kind_name"])
    size = info.get("size")
    if size:
        lo, hi = (f"{v * 100:g}" for v in size)
        parts.append(f"{info.get('measures') or 'size'} {lo} cm" if lo == hi else f"{info.get('measures') or 'size'} {lo}-{hi} cm")
    if info.get("impedance"):
        parts.append(f"{info['impedance']:g} ohm")
    return info["name"] + (f" ({', '.join(parts)})" if parts else "")

def format_chain(chain):
    """A chain as one line for `list`: its direction, port, the LO it has and the antenna on it"""
    where = f"{chain['direction']}" + (f" {chain['port']}" if chain.get("port") else "")
    lo = f", LO {chain['lo'] / 1e6:g} MHz{' (inverted)' if chain.get('invert') else ''}" if chain.get("lo") else ""
    return f"antenna {where}: {format_antenna(chain.get('antenna'))}{lo}"

def print_list(c):
    """Print what the server `c` (a `Client`) allows, hosts and has attached, as `client ADDRESS list` does"""
    print("types:", ", ".join(c.types()) or "(none)")
    res = c._c2.request("list")
    devs = res["devices"]
    for d in devs:
        flags = [k for k, v in d["state"].items() if v and k != "open"]
        who = f" by {d['program']}" if d.get("program") else ""
        print(f"{d['id']:<14} {d['name']:<20} cf={d['cf']:,.0f} Fs={d['Fs']:,.0f} "
              f"{d['wire']:<9} {'controlled' + who if d['controlled'] else 'free'} {' '.join(flags)}")
        for key in ("need", "want"):
            if d.get(key):
                print(f"{'':<14} {key}: {d[key]}")
        for chain in d.get("chains", []):
            print(f"{'':<14} {format_chain(chain)}")
    if not devs:
        print("no devices hosted")
    free = [a for a in res.get("available", []) if not a["hosted"]]
    if free:
        print("attached, not open:")
        for a in free:
            print(f"{'':<14} {a['driver']}: {a['label']}" + (f"  serial {a['serial']}" if a["serial"] else ""))

def run_discover(args):
    """`pysdrlib discover`: the servers announcing themselves here, and with --list what each has"""
    from .discovery import discover
    from .client import Client
    from . import protocol
    found = discover(args.timeout, all_versions=args.all_versions)
    if not found:
        print("no servers found (a server must run with --advertise, on this network)")
    for i, s in enumerate(found):
        if args.list and i:
            print()
        other = f" protocol {s['proto']}" if s["proto"] != protocol.VERSION else ""
        print(f"{s['address']:<24} {s['name']} ({s['kind']}, pysdrlib {s['version'] or '?'}{other})")
        for extra in s["addresses"][1:]:
            print(f"{'':<24} also {extra}")
        if args.list:
            try:
                with Client(s["address"], timeout=max(args.timeout, 2.0)) as c:
                    print_list(c)
            except Exception as e:
                print(f"  cannot ask it: {e}")

def print_stats(metrics):
    """`client stats`: one paragraph per hosted device"""
    print(f"server up {metrics['uptime']:.0f}s")
    if not metrics["devices"]:
        print("  no hosted devices")
    for dev_id, m in metrics["devices"].items():
        rx, dev, tx = m["rx"], m["device"] or {}, m["tx"]
        who = f"controlled by {m['program'] or 'a client'}" if m["controlled"] else "uncontrolled"
        print(f"  {dev_id} ({m['type']}, {who}, {'streaming' if m['streaming'] else 'idle'}, up {m['uptime']:.0f}s)")
        print(f"    rx: {rx['samples']:,} samples published, {rx['bytes'] / 1e6:,.1f} MB in {rx['blocks']:,} blocks "
              f"({rx['wire']}); subscribers: {rx['socket_subscribers']} socket, {rx['shm_subscribers']} shm")
        if dev:
            print(f"    device: {dev.get('samples', 0):,} delivered, {dev.get('dropped', 0):,} dropped, "
                  f"{dev.get('missing', 0):,} missing, {dev.get('stalls', 0)} stalls" if "error" not in dev
                  else f"    device: {dev['error']}")
        if "upstream" in m:
            print(f"    upstream: {m['upstream']['id']} of {m['upstream']['address']}, "
                  f"{m['upstream']['dropped_blocks']:,} blocks lost on the way")
        if tx["samples"] or tx["refused"] or tx["device"]:
            print(f"    tx: {tx['samples']:,} samples in {tx['blocks']:,} blocks, {tx['refused']:,} refused"
                  + (f", device {tx['device']}" if tx["device"] else ""))
        print(f"    control: {m['control']['calls']:,} calls, {m['control']['errors']:,} failed")

def run_client(args):
    """`pysdrlib client`: list, info, stats, rx, watch, set and tx"""
    from .client import Client, RemoteDevice
    if args.cmd == "list":
        with Client(args.address, args.timeout) as c:
            print_list(c)
    elif args.cmd == "stats":
        with Client(args.address, args.timeout) as c:
            metrics = c.metrics(args.device)
        if args.json:
            print(json.dumps(metrics, indent=2))
        else:
            print_stats(metrics)
    elif args.cmd == "info":
        with RemoteDevice(args.address, id=args.device, timeout=args.timeout) as dev:
            print(json.dumps(dev.capabilities, indent=2))
    elif args.cmd == "rx":
        dev = RemoteDevice(args.address, args.type, timeout=args.timeout, wire=args.rx_wire,
                           init_kwargs=dict(_setting(i) for i in args.init),
                           **_choice(args, freq=args.freq, rate=args.rate))
        dev.open(**dict(_setting(a) for a in args.arg))
        with dev:
            dev.initialize(cf=args.freq, Fs=args.rate, rx_gain=args.rx_gain)
            _apply(dev, [_setting(s) for s in args.set])
            print(f"{dev.id}: cf={dev.get_rx_freq():,.0f} Fs={dev.get_rx_sample_rate():,.0f} wire={dev.wire}")
            dev.start_rx()
            try:
                _record(dev, args)
            finally:
                dev.stop_rx()
    elif args.cmd == "watch":
        with RemoteDevice(args.address, id=args.device, timeout=args.timeout) as dev:
            _record(dev, args)
    elif args.cmd == "set":
        with RemoteDevice(args.address, id=args.device, timeout=args.timeout) as dev:
            dev.claim()
            try:
                _apply(dev, [_setting(s) for s in args.settings])
            finally:
                dev.release()
    elif args.cmd == "tx":
        dev = RemoteDevice(args.address, args.type, timeout=args.timeout, tx_wire=args.tx_wire,
                           init_kwargs=dict(_setting(i) for i in args.init),
                           **_choice(args, freq=args.freq, rate=args.rate))
        dev.open(**dict(_setting(a) for a in args.arg))
        with dev:
            dev.initialize(cf=args.freq, Fs=args.rate, tx_gain=args.tx_gain)
            _apply(dev, [_setting(x) for x in args.set])
            print(f"{dev.id}: cf={dev.get_rx_freq():,.0f} Fs={dev.get_rx_sample_rate():,.0f} tx_wire={dev.tx_wire_name}")
            _transmit(dev, args)

def run(args):
    """Run the command `args` names (`server`, `relay` or `client`); errors exit with a message instead of a traceback"""
    try:
        import zmq  # noqa: F401
    except ImportError:
        raise SystemExit("pysdrlib's remote support needs pyzmq: pip install pysdrlib[remote]")
    try:
        {"server": run_server, "relay": run_relay, "client": run_client, "discover": run_discover}[args.mode](args)
    except (TimeoutError, OSError) as e:
        raise SystemExit(f"error: {e}")
    except Exception as e:
        from .. import err
        if isinstance(e, err._Error):
            raise SystemExit(f"error: {e}")
        raise

def main(argv=None):
    """The remote commands on their own (`pysdrlib.cli.main` is the whole `pysdrlib` command)"""
    run(build_parser().parse_args(argv))

if __name__ == "__main__":
    sys.exit(main())
