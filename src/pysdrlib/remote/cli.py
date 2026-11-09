"""Command line for the remote server and client.

    pysdrlib server --bind tcp://0.0.0.0:5555 --port-range 6000-6100 --devices hackrf,rtl_sdr
    pysdrlib client tcp://host:5555 list
    pysdrlib client tcp://host:5555 info hackrf-0
    pysdrlib client tcp://host:5555 rx hackrf --freq 100e6 --rate 10e6 -o out.cfile -t 5
    pysdrlib client tcp://host:5555 watch hackrf-0 -o out.cfile -t 5
    pysdrlib client tcp://host:5555 tx hackrf -i out.cfile --freq 915e6 --rate 2e6 --tx-gain 0 --tx-wire int8
    pysdrlib client tcp://host:5555 set hackrf-0 freq=101e6 rx_lna_gain=16

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

def build_parser():
    p = argparse.ArgumentParser(prog="pysdrlib", description="pysdrlib remote server and client")
    sub = p.add_subparsers(dest="mode", required=True)

    s = sub.add_parser("server", help="host the SDRs connected to this machine")
    s.add_argument("--bind", default="tcp://127.0.0.1:5555", help="C2 endpoint (default %(default)s; use tcp://0.0.0.0:5555 to serve the network)")
    s.add_argument("--port-range", type=_port_range, help="LOW-HIGH ports for per-device sockets (default: any free port)")
    s.add_argument("--devices", help="comma separated device types clients may open (default: all)")
    s.add_argument("--open", action="append", default=[], metavar="TYPE", help="open a device at startup, uncontrolled until a client claims it (repeatable)")
    s.add_argument("--wire", "--rx-wire", dest="wire", default="native", help="default RX wire format: native (device's own sample layout, default), complex64, int8, int16, ...")
    s.add_argument("--lease", type=float, default=15.0, help="seconds a silent controller keeps control, 0 = forever (default %(default)s)")
    s.add_argument("-v", "--verbose", action="store_true")

    c = sub.add_parser("client", help="talk to a pysdrlib server")
    c.add_argument("address", help="server C2 endpoint, e.g. tcp://host:5555")
    c.add_argument("--timeout", type=float, default=5.0)
    cmd = c.add_subparsers(dest="cmd", required=True)

    cmd.add_parser("list", help="device types and hosted devices")

    i = cmd.add_parser("info", help="capabilities of a hosted device")
    i.add_argument("device")

    r = cmd.add_parser("rx", help="open a new device, configure it and record")
    r.add_argument("type", help="device type, e.g. hackrf")
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
    st.add_argument("settings", nargs="+", metavar="KEY=VALUE", help="calls set_<key>(value): freq, sample_rate, rx_gain, rx_lna_gain, ...")

    t = cmd.add_parser("tx", help="open a new device, configure it and transmit a file")
    t.add_argument("type", help="device type, e.g. hackrf")
    t.add_argument("-i", "--input", required=True, help="file of raw complex64 to transmit")
    t.add_argument("--tx-wire", default="native", help="TX wire format: native (device's own sample layout, default), complex64, int8, int16, ...")
    t.add_argument("--freq", type=_number)
    t.add_argument("--rate", type=_number)
    t.add_argument("--tx-gain", type=_number, help="total TX gain in dB (default: the device's)")
    t.add_argument("--loop", action="store_true", help="repeat the file until Ctrl+C")
    t.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="device specific setter, e.g. tx_if_gain=0 (repeatable)")
    t.add_argument("--init", action="append", default=[], metavar="KEY=VALUE", help="device constructor argument (repeatable)")
    return p

def _record_args(p):
    p.add_argument("-o", "--output", required=True, help="file to write raw complex64 to")
    p.add_argument("-n", "--samples", type=int, help="stop after this many samples")
    p.add_argument("-t", "--time", type=float, help="stop after this many seconds")

def _setting(item):
    key, eq, value = item.partition("=")
    if not eq:
        raise SystemExit(f"expected KEY=VALUE, got {item!r}")
    return key.strip(), _number(value)

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
    from .server import Server
    if args.verbose:
        import logging
        from .. import logger
        logger.LOGGING = True
        logging.basicConfig(level=logging.DEBUG)
    devices = args.devices.split(",") if args.devices else None
    server = Server(args.bind, port_range=args.port_range, devices=devices, lease=args.lease, wire=args.wire)
    try:
        for dtype in args.open:
            worker, _ = server.open_device(dtype)
            print(f"hosting {worker.id}")
    except Exception as e:
        server.stop()
        raise SystemExit(f"failed to open device: {e}")
    print(f"pysdrlib server on {server.endpoint} (devices: {', '.join(server.types())}); Ctrl+C to stop")
    server.serve_forever()

def run_client(args):
    import pysdrlib
    from .client import Client, RemoteDevice
    if args.cmd == "list":
        with Client(args.address, args.timeout) as c:
            print("types:", ", ".join(c.types()) or "(none)")
            devs = c.list()
            for d in devs:
                flags = [k for k, v in d["state"].items() if v and k != "open"]
                print(f"{d['id']:<14} {d['name']:<20} cf={d['cf']:,.0f} Fs={d['Fs']:,.0f} "
                      f"{d['wire']:<9} {'controlled' if d['controlled'] else 'free'} {' '.join(flags)}")
            if not devs:
                print("no devices hosted")
    elif args.cmd == "info":
        with RemoteDevice(args.address, id=args.device, timeout=args.timeout) as dev:
            print(json.dumps(dev.capabilities, indent=2))
    elif args.cmd == "rx":
        with RemoteDevice(args.address, args.type, timeout=args.timeout, wire=args.rx_wire,
                          init_kwargs=dict(_setting(i) for i in args.init)) as dev:
            dev.initialize(cf=args.freq, Fs=args.rate, rx_gain=args.rx_gain)
            _apply(dev, [_setting(s) for s in args.set])
            print(f"{dev.id}: cf={dev.CF:,.0f} Fs={dev.Fs:,.0f} wire={dev.wire}")
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
                          init_kwargs=dict(_setting(i) for i in args.init)) as dev:
            dev.initialize(cf=args.freq, Fs=args.rate, tx_gain=args.tx_gain)
            _apply(dev, [_setting(x) for x in args.set])
            print(f"{dev.id}: cf={dev.CF:,.0f} Fs={dev.Fs:,.0f} tx_wire={dev.tx_wire_name}")
            _transmit(dev, args)

def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        import zmq  # noqa: F401
    except ImportError:
        raise SystemExit("pysdrlib's remote support needs pyzmq: pip install pysdrlib[remote]")
    try:
        (run_server if args.mode == "server" else run_client)(args)
    except (TimeoutError, OSError) as e:
        raise SystemExit(f"error: {e}")
    except Exception as e:
        from .. import err
        if isinstance(e, err._Error):
            raise SystemExit(f"error: {e}")
        raise

if __name__ == "__main__":
    sys.exit(main())
