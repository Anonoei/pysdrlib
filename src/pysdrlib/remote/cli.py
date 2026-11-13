"""Command line for the remote server, relay and client

    pysdrlib server --bind tcp://0.0.0.0:5555 --port-range 6000-6100 --devices <driver1>,<driver2>
    pysdrlib client tcp://host:5555 list
    pysdrlib client tcp://host:5555 info <driver>-0
    pysdrlib client tcp://host:5555 rx <driver> --freq 100e6 --rate 10e6 -o out.cfile -t 5
    pysdrlib client tcp://host:5555 rx --serial <serial> --program myapp -o out.cfile -t 5
    pysdrlib client tcp://host:5555 rx --need freq=433e6 --need rate=2e6 --freq 433.92e6 --rate 10e6 -o out.cfile -t 5
    pysdrlib client tcp://host:5555 watch <driver>-0 -o out.cfile -t 5
    pysdrlib client tcp://host:5555 tx <driver> -i out.cfile --freq 915e6 --rate 2e6 --tx-gain 0 --tx-wire int8
    pysdrlib client tcp://host:5555 set <driver>-0 rx_freq=101e6 rx_lna_gain=16
    pysdrlib relay tcp://host:5555 <driver>-0 --bind tcp://127.0.0.1:5556      # programs here share one connection to it

Recordings and transmit files are raw complex64 (GNU Radio `.cfile`).
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
    s.add_argument("--open", action="append", default=[], metavar="TYPE", help="open a device at startup, uncontrolled until a client claims it (repeatable)")
    s.add_argument("--wire", "--rx-wire", dest="wire", default="native", help="default RX wire format: native (device's own sample layout, default), complex64, int8, int16, ...")
    s.add_argument("--lease", type=float, default=15.0, help="seconds a silent controller keeps control, 0 = forever (default %(default)s)")
    s.add_argument("--no-shm", dest="shm", action="store_false", help="don't offer clients on this machine the samples in shared memory")
    s.add_argument("--scan-interval", type=float, default=10.0, help="seconds between looks at which radios are attached, logging ones that appear or vanish, 0 = only at startup (default %(default)s)")
    s.add_argument("--uhd-log", metavar="LEVEL", help="how much of UHD's own console log to show: trace, debug, info, warning, error, fatal or off "
                   "(default: warning, or UHD_LOG_CONSOLE_LEVEL if set)")
    s.add_argument("--log-timestamps", action="store_true", help="prefix log lines with a time (default off: journald adds one)")
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
    y.add_argument("--lease", type=float, default=15.0, help="seconds a silent local controller keeps control, 0 = forever (default %(default)s)")
    y.add_argument("--no-shm", dest="shm", action="store_false", help="don't offer programs on this machine the samples in shared memory")
    y.add_argument("-v", "--verbose", action="store_true")

    c = sub.add_parser("client", help="talk to a pysdrlib server")
    c.add_argument("address", help="server C2 endpoint, e.g. tcp://host:5555")
    c.add_argument("--timeout", type=float, default=5.0)
    cmd = c.add_subparsers(dest="cmd", required=True)

    cmd.add_parser("list", help="device types and hosted devices")

    i = cmd.add_parser("info", help="capabilities of a hosted device")
    i.add_argument("device")

    r = cmd.add_parser("rx", help="open a new device, configure it and record")
    r.add_argument("type", nargs="?", help="device type, e.g. hackrf (optional with --serial, --label or --need: any radio that fits)")
    _choose_args(r)
    _record_args(r)
    r.add_argument("--rx-wire", "--wire", dest="rx_wire", help="RX wire format for this device (default: the server's)")
    r.add_argument("--init", action="append", default=[], metavar="KEY=VALUE", help="device constructor argument (repeatable)")
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
    p.add_argument("-o", "--output", required=True, help="file to write raw complex64 to")
    p.add_argument("-n", "--samples", type=int, help="stop after this many samples")
    p.add_argument("-t", "--time", type=float, help="stop after this many seconds")

def _setting(item):
    key, eq, value = item.partition("=")
    if not eq:
        raise SystemExit(f"expected KEY=VALUE, got {item!r}")
    return key.strip(), _number(value)

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
    if args.samples is None and args.time is None:
        raise SystemExit("give a stop condition: -n SAMPLES and/or -t SECONDS")
    total = 0
    start = time.monotonic()
    with open(args.output, "wb") as f:
        try:
            while True:
                if args.samples is not None and total >= args.samples:
                    break
                if args.time is not None and time.monotonic() - start >= args.time:
                    break
                samples = dev.get_samples(timeout=0.25)
                if args.samples is not None:
                    samples = samples[:args.samples - total]
                samples.tofile(f)
                total += samples.size
        except KeyboardInterrupt:
            pass
    print(f"wrote {total:,} samples to {args.output}" + (f" ({dev.dropped_blocks} blocks dropped)" if dev.dropped_blocks else ""))

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
    import signal
    import os
    from .. import logger
    if args.uhd_log:   # UHD reads this when its library loads (the first scan), so set it before then
        os.environ["UHD_LOG_CONSOLE_LEVEL"] = args.uhd_log
    logger.configure(logging.DEBUG if args.verbose else logging.INFO, timestamps=args.log_timestamps)
    log = logger.new("remote.cli")
    from .server import Server
    devices = args.devices.split(",") if args.devices else None
    server = Server(args.bind, port_range=args.port_range, devices=devices, lease=args.lease, wire=args.wire, shm=args.shm,
                    scan_interval=args.scan_interval)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))   # systemd stop: shut down cleanly
    log.info("pysdrlib server on %s (device types: %s; lease %gs; shm %s)", server.endpoint, ", ".join(server.types()),
             args.lease, "on" if args.shm else "off")
    try:
        server.scan()
    except Exception:
        log.exception("looking for radios failed")
    try:
        for dtype in args.open:
            server.open_device(dtype)
    except Exception as e:
        server.stop()
        raise SystemExit(f"failed to open device: {e}")
    try:
        server.serve_forever()
    finally:
        log.info("server stopped")

def run_relay(args):
    """`pysdrlib relay`: host a far device here until Ctrl+C, or until the far server stops answering"""
    from .server import Server
    if args.verbose:
        import logging
        from .. import logger
        logger.LOGGING = True
        logging.basicConfig(level=logging.DEBUG)
    if (args.device is None) == (args.open is None):
        raise SystemExit("error: give the id of a device on the far server, or --open TYPE")
    server = Server(args.bind, lease=args.lease, shm=args.shm)
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

def run_client(args):
    """`pysdrlib client`: list, info, rx, watch, set and tx"""
    import pysdrlib
    from .client import Client, RemoteDevice
    if args.cmd == "list":
        with Client(args.address, args.timeout) as c:
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
            if not devs:
                print("no devices hosted")
            free = [a for a in res.get("available", []) if not a["hosted"]]
            if free:
                print("attached, not open:")
                for a in free:
                    print(f"{'':<14} {a['driver']}: {a['label']}" + (f"  serial {a['serial']}" if a["serial"] else ""))
    elif args.cmd == "info":
        with RemoteDevice(args.address, id=args.device, timeout=args.timeout) as dev:
            print(json.dumps(dev.capabilities, indent=2))
    elif args.cmd == "rx":
        with RemoteDevice(args.address, args.type, timeout=args.timeout, wire=args.rx_wire,
                          init_kwargs=dict(_setting(i) for i in args.init),
                          **_choice(args, freq=args.freq, rate=args.rate)) as dev:
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
        with RemoteDevice(args.address, args.type, timeout=args.timeout, tx_wire=args.tx_wire,
                          init_kwargs=dict(_setting(i) for i in args.init),
                          **_choice(args, freq=args.freq, rate=args.rate)) as dev:
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
        {"server": run_server, "relay": run_relay, "client": run_client}[args.mode](args)
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
