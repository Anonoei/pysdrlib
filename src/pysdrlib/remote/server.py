"""pysdrlib server: hosts hardware SDRs for remote clients.

    server = Server("tcp://0.0.0.0:5555", port_range=(6000, 6100))
    server.serve_forever()          # or server.start() ... server.stop()

The C2 socket (the address given to `Server`) manages lifecycle and ownership. Every hosted device
gets its own control socket and RX stream socket on separate ports, each served by its own
threads, so a slow call on one device never blocks another. See `protocol` for the wire format.

Ownership: a device has at most one controller (the holder of its token) and any number of viewers.
Viewers may subscribe to RX and call `get_*` methods. A controller that stops talking to the server
for `lease` seconds loses control (RX/TX are stopped, the device stays open and can be claimed).

Samples reach clients two ways. A client on another machine subscribes to the device's id and gets blocks of samples over
the socket. One on this machine can take them from shared memory instead (see `shm`), which costs the server one copy
of each block however many clients there are: it keeps a ring per streaming channel and tells subscribers when it has
written. The server only does the work of a way someone is using.
"""
import hmac
import secrets
import socket
import threading
import time

import zmq

from .. import devices
from .. import err
from .. import logger
from . import choose
from . import protocol
from . import shm
from .client import RemoteDevice

POLL_MS = 100
SHM_SECONDS = 0.25      # a shared-memory ring holds this much of the stream at the device's highest rate
IDLE_SLEEP = 0.002   # a pause after an error, so a failing device is not retried in a tight loop
BATCH_SECONDS = 0.002   # the stream is published in blocks of about this long (the latency a client sees, and no less)
CLOSE_WAIT = 5.0        # how long a close request waits for the device before replying anyway
MIN_BATCH = 1024        # ... but not smaller than this many samples
MAX_WAIT = 0.01         # a block that has not filled by then is published as it is

def _program(name):
    """The program name a client gave, tidied for logs and `list` (None if it gave none)"""
    if name is None:
        return None
    return " ".join(str(name).split())[:80] or None

def _parse_bind(bind: str):
    """-> (scheme, host_or_path, port or None)"""
    scheme, sep, rest = bind.partition("://")
    if not sep or scheme not in ("tcp", "ipc"):
        raise ValueError(f"bind must be tcp://host:port or ipc://path, got {bind!r}")
    if scheme == "ipc":
        return scheme, rest, None
    host, _, port = rest.rpartition(":")
    if not host:
        raise ValueError(f"bind must be tcp://host:port, got {bind!r}")
    return scheme, host, (None if port in ("*", "0", "") else int(port))


class DeviceWorker:
    """One hosted device: its control socket, RX stream socket, and ownership state"""
    program = None      # name the controller gave for the program it runs (see `Server.open_device`), None if not told
    need = None         # what that program said it needs ...
    want = None         # ... and would like (see `choose`)
    unit = None         # the `DeviceInfo` of the radio it hosts, when it was picked from the attached ones

    def __init__(self, server: "Server", dev_id: str, dtype: str, device, wire="native", shm=True, shm_samples=None):
        self.id = dev_id
        self.type = dtype
        self.device = device
        self.log = logger.new(f"remote.worker.{dev_id}")
        self.lock = threading.RLock()           # serializes calls into the device
        self.token = None                       # controller token, None if uncontrolled
        self.last_seen = time.monotonic()
        self.streaming = threading.Event()
        self._stop = threading.Event()
        self._seq = 0
        self.wire = protocol.resolve_wire(wire, device)    # format RX blocks are published in

        self.shm_enabled = bool(shm)            # offer clients on this machine the samples in shared memory
        self._shm_samples = shm_samples         # ring capacity in samples (default: SHM_SECONDS at the device's top rate)
        self._rings = {}                        # channel -> shm.ShmWriter of the current stream
        self._stream_gen = 0                    # counts start_rx calls: a new stream gets new rings
        self._ring_gen = -1
        self._ring_failed = False
        self._ends = {}                         # channel -> index after the last sample published
        self._subs = {}                         # topic -> subscribers, from the RX socket's subscription messages
        self._topic = dev_id.encode()
        self._shm_topic = protocol.SHM_PREFIX + self._topic

        self.tx_refused = 0                     # TX blocks dropped: not from the controller, or not transmitting
        self.ctrl = server._socket(zmq.ROUTER)
        self.rx = server._socket(zmq.XPUB)      # a PUB that also says who subscribed to what
        self.rx.setsockopt(zmq.XPUB_VERBOSER, 1)
        self.rx.setsockopt(zmq.SNDHWM, 64)
        self.tx = server._socket(zmq.PULL)
        self.tx.setsockopt(zmq.RCVHWM, 8)       # little queued here: a full device queue holds the sender back
        self.endpoints = {
            "ctrl": server._bind(self.ctrl, f"{dev_id}.ctrl"),
            "rx": server._bind(self.rx, f"{dev_id}.rx"),
            "tx": server._bind(self.tx, f"{dev_id}.tx"),
        }
        self.capabilities = self._capabilities()
        self._threads = [
            threading.Thread(target=self._control_loop, name=f"pysdrlib-{dev_id}-ctrl", daemon=True),
            threading.Thread(target=self._pump_loop, name=f"pysdrlib-{dev_id}-rx", daemon=True),
            threading.Thread(target=self._tx_loop, name=f"pysdrlib-{dev_id}-tx", daemon=True),
        ]
        for t in self._threads:
            t.start()

    # --- ownership --- #
    def claim(self, program=None) -> str:
        """Make a new controller token the owner. Raises `err.Busy` if the device already has a controller"""
        with self.lock:
            if self.token is not None:
                raise err.Busy(f"{self.id} already has a controller" + (f" ({self.program})" if self.program else ""))
            self.program = program
            self.token = secrets.token_hex(16)
            self.last_seen = time.monotonic()
            return self.token

    def is_controller(self, token) -> bool:
        """Whether `token` is the current controller's"""
        return self.token is not None and isinstance(token, str) and hmac.compare_digest(self.token, token)

    def touch(self):
        """Note that the controller is still there (renews its lease)"""
        self.last_seen = time.monotonic()

    def release(self):
        """Drop the controller; stop streaming since nobody is left to manage it"""
        with self.lock:
            self.token = None
            self.program = self.need = self.want = None
            self.streaming.clear()
            for flag, stop in (("rx", self.device.stop_rx), ("tx", self.device.stop_tx)):
                if self.device.state.get(flag):
                    try:
                        stop()
                    except Exception:
                        self.log.exception("stop_%s failed during release", flag)

    def serial(self):
        """The serial number of the radio, when the driver or the scan told us it"""
        if self.unit is not None and self.unit.serial:
            return self.unit.serial
        try:
            return self.device.caps.info.get("serial")
        except Exception:
            return None

    # --- description --- #
    def _capabilities(self):
        cls = type(self.device)
        methods = {}
        for name in dir(cls):
            if name.startswith("_") or name in protocol.BLOCKED_METHODS:
                continue
            attr = getattr(cls, name)
            if callable(attr):
                doc = (attr.__doc__ or "").strip().splitlines()
                methods[name] = {"doc": doc[0] if doc else "", "readonly": protocol.is_readonly(name)}
        return {
            "id": self.id,
            "type": self.type,
            "name": cls.NAME,
            "can_transmit": cls.CAN_TRANSMIT,
            "duplex": cls.DUPLEX,
            "methods": methods,
            "native_wire": protocol.wire_name(protocol.resolve_wire("native", self.device)),
            "shm": self.shm_enabled,
            "shm_host": socket.gethostname(),
            "endpoints": self.endpoints,
        }

    def status(self):
        """What `list` shows clients: id, type, name, state, frequency, sample rate, wire format and whether it is controlled"""
        with self.lock:
            return {
                "id": self.id, "type": self.type, "name": type(self.device).NAME,
                "state": dict(self.device.state),
                "cf": self.device.get_rx_freq(), "Fs": self.device.get_rx_sample_rate(),
                "controlled": self.token is not None,
                "program": self.program, "need": self.need, "want": self.want,
                "serial": self.serial(),
                "wire": protocol.wire_name(self.wire),
                "rx_channels": list(getattr(self.device, "rx_channels", (0,))),
                "tx_channels": list(getattr(self.device, "tx_channels", (0,))),
            }

    # --- control socket --- #
    def _control_loop(self):
        while not self._stop.is_set():
            if not self.ctrl.poll(POLL_MS):
                continue
            ident, data = self.ctrl.recv_multipart()
            req_id = None
            try:
                msg = protocol.unpack(data)
                req_id = msg.get("id")
                reply = protocol.ok(req_id, self._handle(msg))
            except Exception as e:
                if not isinstance(e, (err._Error, ValueError, TypeError, AttributeError, NotImplementedError)):
                    self.log.exception("control request failed")
                reply = protocol.fail(req_id, e)
            self.ctrl.send_multipart([ident, reply])

    def _handle(self, msg):
        op = msg.get("op")
        token = msg.get("token")
        controller = self.is_controller(token)
        if controller:
            self.touch()
        if op == "capabilities":
            return self.capabilities
        if op == "status":
            return self.status()
        if op == "call":
            return self._call(msg, controller)
        if op == "set_wire":
            if not controller:
                raise err.NotController(f"`set_wire` requires the controller of {self.id}")
            self.wire = protocol.resolve_wire(msg.get("format"), self.device)
            return protocol.wire_name(self.wire)
        raise ValueError(f"unknown op {op!r}")

    def _call(self, msg, controller):
        method = msg.get("method")
        if not isinstance(method, str) or method.startswith("_") or method not in self.capabilities["methods"]:
            raise AttributeError(f"{type(self.device).NAME} has no remote method {method!r}")
        if not controller and not protocol.is_readonly(method):
            raise err.NotController(f"`{method}` requires the controller of {self.id}")
        args, kwargs = msg.get("args", []), msg.get("kwargs", {})
        with self.lock:
            try:
                result = getattr(self.device, method)(*args, **kwargs)
                if method in ("start_rx", "stop_rx", "start_tx", "stop_tx"):
                    self.log.info("%s: %s", self.id, method)
                if method == "start_rx":
                    self._stream_gen += 1   # a new stream: indices start again, and the rings with them
                return result
            finally:
                if self.device.state.get("rx"):
                    self.streaming.set()
                else:
                    self.streaming.clear()
                    for ring in list(self._rings.values()):
                        ring.close_stream()   # readers drain what is there, and see that no more is coming

    # --- RX stream --- #
    def _pump_loop(self):
        """Publish every channel the device streams, each block tagged with its channel and the index of its first
        sample, so clients keep the channels apart and aligned and see where samples were lost"""
        stalled = False   # log a stall once, not on every poll
        broken = False    # likewise a failing device (e.g. unplugged while streaming)
        while not self._stop.is_set():
            if not self.streaming.wait(POLL_MS / 1000):
                self._poll_subscriptions()
                continue
            failed = True
            try:
                self._poll_subscriptions()
                channels = tuple(self.device.rx_channels)
                # Until a block's worth has arrived (the GIL is free meanwhile): publishing each USB packet as it comes
                # costs several times the CPU of publishing a couple of milliseconds at once
                batch = min(protocol.MAX_BLOCK_SAMPLES, max(MIN_BATCH, int(BATCH_SECONDS * self._rate())))
                self.device.wait_available(MAX_WAIT, channels[0], batch)
                for ch in channels:
                    samples, index = self.device.read_available(ch)
                    if len(samples):
                        self._publish(ch, samples, index)
                if broken:
                    self.log.info("%s: reading the device works again", self.id)
                stalled = False
                broken = False
                failed = False
            except err.StreamStalled as exc:
                if not stalled:
                    self.log.warning("%s", exc)
                    stalled = True
            except Exception:
                if not self._stop.is_set() and self.streaming.is_set() and self.device.state.get("rx") and not broken:
                    broken = True
                    self.log.exception("%s: reading the device failed (disconnected? retrying)", self.id)
            if failed:
                time.sleep(IDLE_SLEEP)

    def _rate(self):
        """The receive sample rate (for sizing blocks)"""
        return self.device.get_rx_sample_rate()

    def _poll_subscriptions(self):
        """Count who subscribes to what (the RX socket reports every subscribe and unsubscribe)"""
        while self.rx.poll(0):
            msg = self.rx.recv()
            if msg:
                topic = bytes(msg[1:])
                self._subs[topic] = max(0, self._subs.get(topic, 0) + (1 if msg[0] == 1 else -1))

    def _publish(self, ch, samples, index):
        """One read of a channel, to whoever wants it: into shared memory (and a notice, for clients on this machine), as
        blocks over the socket (for the rest)"""
        now = time.time()
        if index < self._ends.get(ch, 0):   # the indices went back: the stream was restarted (not by one of our own clients)
            self._stream_gen += 1
        self._ends[ch] = index + len(samples)
        if self._subs.get(self._shm_topic):
            ring = self._ring(ch) if self.shm_enabled else None
            if ring is not None:
                ring.publish(samples, index)
            header, payload = protocol.pack_notice(self._seq, now, ch, index, len(samples), ring.name if ring else "")
            self._seq += 1
            self.rx.send_multipart([self._shm_topic, header, payload])
        if self._subs.get(self._topic):
            for i in range(0, len(samples), protocol.MAX_BLOCK_SAMPLES):
                header, payload = protocol.pack_block(
                    self._seq, now, samples[i:i + protocol.MAX_BLOCK_SAMPLES], self.wire, channel=ch, index=index + i)
                self._seq += 1
                self.rx.send_multipart([self._topic, header, payload], copy=False)

    def _ring(self, ch):
        """The shared-memory ring of a channel of the current stream, made when first needed (None: it can't be made)"""
        if self._ring_gen != self._stream_gen:   # a new stream: its own rings
            self._destroy_rings()
            self._ring_gen, self._ring_failed = self._stream_gen, False
        ring = self._rings.get(ch)
        if ring is None and not self._ring_failed:
            top = max((r.hi for r in self.device.caps.rates), default=20e6)
            wanted = self._shm_samples or int(SHM_SECONDS * top)
            capacity = shm.fit_capacity(wanted, floor=1 if self._shm_samples else shm.MIN_CAPACITY)
            try:
                if not capacity:
                    raise OSError("no room in /dev/shm")
                ring = self._rings[ch] = shm.ShmWriter(capacity, self._stream_gen)
            except (OSError, ValueError) as exc:
                self.log.warning("no shared memory for %s (%s): clients on this machine use the socket", self.id, exc)
                self._ring_failed = True
        return ring

    def _destroy_rings(self):
        for ring in self._rings.values():
            ring.destroy()
        self._rings.clear()

    def _tx_loop(self):
        """Hand the controller's TX blocks to the device. Its write blocks while the device's queue is full, which
        fills this socket's small queue and so holds the sender back (flow control end to end)"""
        while not self._stop.is_set():
            if not self.tx.poll(POLL_MS):
                continue
            try:
                token, header, payload = self.tx.recv_multipart(copy=False)
            except zmq.ZMQError:
                continue
            if self.token is None or token.bytes.decode(errors="replace") != self.token:
                self.tx_refused += 1
                if self.tx_refused == 1:
                    self.log.warning("TX blocks from something other than the controller are dropped")
                continue
            try:
                _, _, samples, channel, _ = protocol.unpack_block_ex(header.bytes, payload.buffer)
                if not self.device.state.get("tx"):
                    self.tx_refused += 1
                    continue
                self.device.write(samples, channel=channel)
            except Exception:
                if not self._stop.is_set():
                    self.log.exception("transmitting failed")

    # --- teardown --- #
    def close(self):
        """Stop serving and close the hardware"""
        self._stop.set()
        self.streaming.clear()
        for t in self._threads:
            t.join()
        self._destroy_rings()
        self.ctrl.close(0)
        self.rx.close(0)
        self.tx.close(0)
        with self.lock:
            try:
                self._close_device()
            except Exception:
                self.log.exception("close failed")

    def _close_device(self):
        if self.device.state["open"]:
            self.device.close()


class RelayWorker(DeviceWorker):
    """A device hosted by another server, hosted here: a relay

    Programs on this machine connect here instead of to the far server, and the far server sees one client however many
    there are. The samples cross the network once and reach every local program through shared memory. Control passes
    through to the far device when this relay controls it (`Server.relay` takes control if it is free).

    The upstream is a `RemoteDevice`. A thread follows its state (a stream started by someone else upstream starts the
    relay's too), and `lost` is set when the far server stops answering.
    """
    WATCH = 0.25      # seconds between looks at the far device's state
    LOST_AFTER = 2    # failed looks (each up to the upstream's timeout) before it is given up for lost

    def __init__(self, server: "Server", dev_id: str, upstream: RemoteDevice, owns: bool, shm=True, shm_samples=None):
        self.upstream = upstream
        self._owns = owns                   # the relay opened the far device: closing the relay closes it
        status = upstream.refresh()
        self.lost = threading.Event()
        native = (upstream.capabilities or {}).get("native_wire")
        try:
            protocol.wire_format(native)    # the far device's own layout is the best one to pass on: lossless and compact
        except ValueError:
            native = "complex64"
        super().__init__(server, dev_id, status.get("type", "remote"), upstream, native, shm, shm_samples)
        if upstream.state.get("rx"):
            self.streaming.set()
        watcher = threading.Thread(target=self._watch_loop, name=f"pysdrlib-{dev_id}-watch", daemon=True)
        self._threads.append(watcher)
        watcher.start()

    def _capabilities(self):
        caps = dict(self.upstream.capabilities)   # the far device's name, methods, wire; the sockets are this relay's own
        caps.update(id=self.id, endpoints=self.endpoints, shm=self.shm_enabled, shm_host=socket.gethostname(),
                    relay={"address": self.upstream.address, "device": self.upstream.id})
        return caps

    def status(self):
        """The far device's state as this relay last heard it (every forwarded call, and the watcher, refresh it)"""
        up = self.upstream
        return {"id": self.id, "type": self.type, "name": up.capabilities["name"], "state": dict(up.state),
                "cf": up._cf, "Fs": up._Fs, "controlled": self.token is not None, "wire": protocol.wire_name(self.wire),
                "rx_channels": list(up.rx_channels), "tx_channels": list(up.tx_channels), "lost": self.lost.is_set(),
                "program": self.program, "need": self.need, "want": self.want, "serial": None}

    def _rate(self):
        return self.upstream._Fs or 1e6

    def release(self):
        if self.upstream.token:   # we control the far device, so a local controller leaving stops it as it would anywhere
            return super().release()
        with self.lock:
            self.token = None   # a viewer relay can't stop the far device
            self.program = self.need = self.want = None

    def _watch_loop(self):
        """Follow the far device: its state, and whether it still answers"""
        failed, was_rx = 0, bool(self.upstream.state.get("rx"))
        while not self._stop.wait(self.WATCH):
            try:
                with self.lock:
                    self.upstream.refresh()
                failed = 0
            except Exception as exc:
                failed += 1
                if failed >= self.LOST_AFTER and not self.lost.is_set():
                    self.log.error("%s: the far server stopped answering (%s)", self.id, exc)
                    self.lost.set()
                    self.streaming.clear()
                continue
            rx = bool(self.upstream.state.get("rx"))
            if rx:
                self.streaming.set()
            elif was_rx:
                self.streaming.clear()
                for ring in list(self._rings.values()):
                    ring.close_stream()
            was_rx = rx

    def _close_device(self):
        if self.upstream.state["open"]:
            if self._owns:
                self.upstream.close()
            else:
                self.upstream.detach()   # give control back; the far device carries on


class Server:
    def __init__(self, bind="tcp://127.0.0.1:5555", port_range=None, devices=None, lease=15.0, wire="native", shm=True,
                 shm_samples=None, scan_interval=0.0):
        """
        bind: C2 endpoint, tcp://host:port (port 0 or * picks one; see `endpoint`) or ipc://path.
        port_range: (low, high) for the per-device tcp ports; default lets the OS pick.
        devices: device types clients may open (default: every vendor package).
        lease: seconds a controller may be silent before it loses control (0 disables).
        wire: default RX wire format: "native" (the device's own sample layout), "complex64", "int8", "int16", ...
        shm: let clients on this machine take samples from shared memory (the socket still serves the rest).
        shm_samples: samples a shared-memory ring holds (default: a quarter second at the device's top rate).
        scan_interval: seconds between looks at which radios are attached, logging the ones that appear or vanish (0 = never).
        """
        self.log = logger.new("remote.server")
        self.scheme, self._host, self._port = _parse_bind(bind)
        self.port_range = tuple(port_range) if port_range else None
        self.allowed = list(devices) if devices else None
        self.lease = float(lease)
        protocol.wire_format(wire) if wire != "native" else None    # validate now
        self.wire = wire
        self.shm = bool(shm)
        self.shm_samples = shm_samples
        self.endpoint = None        # actual C2 endpoint once bound
        self.workers: dict[str, DeviceWorker] = {}
        self._counts: dict[str, int] = {}
        self._ctx = zmq.Context()
        self._c2 = None
        self._thread = None
        self._stop = threading.Event()
        self.scan_interval = float(scan_interval)
        self._scan_thread = None
        self._attached = None       # {key: DeviceInfo} at the last scan
        self._scan_lock = threading.Lock()   # one look at the USB bus at a time
        self._bind_c2(bind)

    # --- sockets --- #
    def _socket(self, kind):
        sock = self._ctx.socket(kind)
        sock.setsockopt(zmq.LINGER, 0)
        return sock

    def _bind(self, sock, name) -> str:
        """Bind a device socket, -> the endpoint to advertise to clients"""
        if self.scheme == "ipc":
            sock.bind(f"ipc://{self._host}.{name}")
            return f"ipc://{self._host}.{name}"
        base = f"tcp://{self._host}"
        if self.port_range:
            port = sock.bind_to_random_port(base, min_port=self.port_range[0], max_port=self.port_range[1])
        else:
            port = sock.bind_to_random_port(base)
        return f"tcp://{{host}}:{port}"

    def _bind_c2(self, bind):
        self._c2 = self._socket(zmq.ROUTER)
        if self.scheme == "tcp" and self._port is None:
            port = self._c2.bind_to_random_port(f"tcp://{self._host}")
            self.endpoint = f"tcp://{self._host}:{port}"
        else:
            self._c2.bind(bind)
            self.endpoint = bind

    # --- running --- #
    def start(self):
        """Serve in a background thread"""
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="pysdrlib-c2", daemon=True)
            self._thread.start()
        if self.scan_interval > 0 and self._scan_thread is None:
            self._scan_thread = threading.Thread(target=self._scan_loop, name="pysdrlib-scan", daemon=True)
            self._scan_thread.start()
        return self

    def serve_forever(self):
        """Serve in this thread until Ctrl+C or SIGTERM (a service being stopped), then `stop`"""
        if self.scan_interval > 0 and self._scan_thread is None:
            self._scan_thread = threading.Thread(target=self._scan_loop, name="pysdrlib-scan", daemon=True)
            self._scan_thread.start()
        try:
            self._run()
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self):
        """Stop serving and close every hosted device"""
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None
        if self._scan_thread is not None:
            self._scan_thread.join()
            self._scan_thread = None
        for dev_id in list(self.workers):
            self.workers.pop(dev_id).close()
        if self._c2 is not None:
            self._c2.close(0)
            self._c2 = None
            self._ctx.term()

    def __enter__(self):
        return self.start()
    def __exit__(self, *exc):
        self.stop()

    def _run(self):
        self.log.info("serving on %s", self.endpoint)
        while not self._stop.is_set():
            if self._c2.poll(POLL_MS):
                ident, data = self._c2.recv_multipart()
                req_id = None
                try:
                    msg = protocol.unpack(data)
                    req_id = msg.get("id")
                    reply = protocol.ok(req_id, self._dispatch(msg))
                except Exception as e:
                    if not isinstance(e, (err._Error, ValueError, TypeError, KeyError, AttributeError)):
                        self.log.exception("request failed")
                    reply = protocol.fail(req_id, e)
                self._c2.send_multipart([ident, reply])
            self._expire_leases()

    # --- attached radios --- #
    @staticmethod
    def _key(info):
        return (info.driver, info.serial or info.index, info.label)

    @staticmethod
    def _describe(info):
        bits = [f"serial {info.serial}"] if info.serial else ([f"index {info.index}"] if info.index is not None else [])
        return f"{info.driver}: {info.label}" + (f" ({', '.join(bits)})" if bits else "")

    def scan(self):
        """Look for attached radios and log what appeared or vanished since the last scan (the first logs all of them).
        -> the list of `DeviceInfo`"""
        with self._scan_lock:
            found = [i for i in devices.find() if i.driver in self.types()]
        now = {self._key(i): i for i in found}
        if self._attached is None:
            if not found:
                self.log.info("no radios found attached to this machine")
            for info in found:
                self.log.info("found %s", self._describe(info))
        else:
            for key, info in now.items():
                if key not in self._attached:
                    self.log.info("radio connected: %s", self._describe(info))
            for key, info in self._attached.items():
                if key not in now:
                    self.log.warning("radio disconnected: %s", self._describe(info))
        self._attached = now
        return found

    def _scan_loop(self):
        while True:
            try:
                self.scan()
            except Exception:
                self.log.exception("looking for radios failed")
            if self._stop.wait(self.scan_interval if self.scan_interval > 0 else 10.0):
                return

    def _expire_leases(self):
        if self.lease <= 0:
            return
        now = time.monotonic()
        for w in list(self.workers.values()):
            if w.token is not None and now - w.last_seen > self.lease:
                self.log.warning("%s: controller lease expired after %.0f s of silence", w.id, now - w.last_seen)
                w.release()

    # --- C2 ops --- #
    def types(self):
        """Device types clients may open: the `devices` given to the server, or every driver"""
        return self.allowed if self.allowed is not None else devices.ls()

    def open_device(self, dtype, init_args=(), init_kwargs=None, args=(), kwargs=None, controlled=False, wire=None,
                    program=None, need=None, want=None, unit=None):
        """Create and open a device of vendor package `dtype` and start hosting it.
        program/need/want: what the controller said it runs and needs (shown by `list`; see `choose`). unit: the `DeviceInfo`
        of the radio, when it was picked from the attached ones.
        -> (worker, token) where token is None unless `controlled`"""
        if dtype not in self.types():
            raise err.NoDevice(f"device type {dtype!r} is not available on this server (have: {', '.join(self.types())})")
        module = devices.get(dtype)
        device = module.Device(*init_args, **(init_kwargs or {}))
        device.open(*args, **(kwargs or {}))
        n = self._counts[dtype] = self._counts.get(dtype, -1) + 1
        try:
            worker = DeviceWorker(self, f"{dtype}-{n}", dtype, device, wire or self.wire, self.shm, self.shm_samples)
        except BaseException:
            device.close()
            raise
        worker.unit = unit
        self.workers[worker.id] = worker
        self.log.info("hosting %s (%s) with %s: ctrl %s, rx %s", worker.id, worker.capabilities.get("name", dtype),
                      f"{program or 'a client'} in control" if controlled else "no controller", worker.endpoints["ctrl"], worker.endpoints["rx"])
        token = worker.claim(program) if controlled else None
        if controlled:
            worker.need, worker.want = need or None, want or None
        return worker, token

    # --- choosing a radio --- #
    def _hosted_serials(self):
        return {w.serial() for w in self.workers.values()} - {None}

    def available(self):
        """The radios attached at the last scan, as dicts: driver, label, serial, index and `hosted` (the id of the worker
        that has it open, or None)"""
        taken = {}
        for w in self.workers.values():
            if w.serial():
                taken[w.serial()] = w.id
        out = []
        unknown = {}    # driver -> workers hosting a radio of it whose serial is not known: each has one of that driver's
        for w in self.workers.values():
            if not w.serial():
                unknown.setdefault(w.type, []).append(w.id)
        for info in (self._attached or {}).values():
            hosted = taken.get(info.serial) if info.serial else None
            if hosted is None and unknown.get(info.driver):
                hosted = unknown[info.driver].pop(0)
            out.append({"driver": info.driver, "label": info.label, "serial": info.serial, "index": info.index,
                        "hosted": hosted})
        return out

    @staticmethod
    def _matches(text, wanted):
        return wanted is None or (text is not None and str(wanted).lower() in str(text).lower())

    def _class_caps(self, dtype):
        """What a radio of this driver can do, before opening one (None if that cannot be told)"""
        try:
            return devices.get(dtype).Device().caps
        except Exception:
            self.log.debug("no capabilities known for %s before it is open", dtype, exc_info=True)
            return None

    def choose(self, dtype=None, serial=None, label=None, need=None, want=None):
        """The radios a client could use for this request, best first: `[(kind, target, caps)]` with kind "hosted" (a
        `DeviceWorker` nobody controls) or "unit" (a `DeviceInfo` attached and not hosted). A radio meets the request when it
        is of type `dtype`, its serial is `serial` and its label contains `label` (case ignored), and meets all of `need`; those
        that meet most of `want` come first, attached ones before hosted ones (a free hosted radio is one a client left open). Raises `err.NoDevice` saying why when none do."""
        need, want = choose.normalize(need, "need"), choose.normalize(want, "want")
        if dtype is not None and dtype not in self.types():
            raise err.NoDevice(f"device type {dtype!r} is not available on this server (have: {', '.join(self.types())})")
        self.scan()     # what is attached now
        reasons, found = [], []

        def consider(kind, target, driver, name, unit_serial, caps):
            if dtype is not None and driver != dtype:
                return
            if serial is not None and str(unit_serial) != str(serial):
                return
            if not self._matches(name, label):
                return
            who = f"{driver}: {name}" + (f" (serial {unit_serial})" if unit_serial else "")
            bad = choose.problems(caps, need) if caps is not None else []
            if bad:
                reasons.append(f"{who}: {'; '.join(bad)}")
            else:
                found.append((choose.rank(caps, want), kind == "hosted", len(found), kind, target, caps))

        for w in self.workers.values():
            if w.token is not None or isinstance(w, RelayWorker):
                continue    # someone controls it (or it is another server's)
            consider("hosted", w, w.type, type(w.device).NAME, w.serial(), w.device.caps)
        unknown = {}
        for w in self.workers.values():
            if not w.serial():
                unknown[w.type] = unknown.get(w.type, 0) + 1
        by_driver = {}
        for info in (self._attached or {}).values():
            if info.serial and info.serial in self._hosted_serials():
                continue
            if not info.serial and unknown.get(info.driver):    # one of these is already open: take it to be this one
                unknown[info.driver] -= 1
                continue
            if info.driver not in by_driver:
                by_driver[info.driver] = self._class_caps(info.driver)
            consider("unit", info, info.driver, info.label, info.serial, by_driver[info.driver])
        if not found:
            what = ", ".join(f"{k}={v}" for k, v in (("type", dtype), ("serial", serial), ("label", label)) if v is not None)
            what = (what + "; " if what else "") + f"need {need}" if need else what
            if reasons:
                raise err.NoDevice(f"no radio on this server meets the request ({what}): " + " | ".join(reasons))
            raise err.NoDevice(f"no radio on this server is free for the request ({what or 'any'}); "
                               f"attached: {', '.join(i['label'] for i in self.available()) or 'none'}")
        found.sort(key=lambda f: f[:3])
        return [(f[3], f[4], f[5]) for f in found]

    def _give_back(self, kind, worker):
        """Let go of a radio `open_matching` took but cannot use: a hosted one is released, one it opened is closed"""
        if kind == "hosted":
            worker.release()
        else:
            self.workers.pop(worker.id, None)
            worker.close()

    def open_matching(self, dtype=None, serial=None, label=None, need=None, want=None, program=None, init_args=(),
                      init_kwargs=None, wire=None):
        """Open (or take over, if it is hosted and nobody controls it) the best radio for the request (see `choose`).
        -> (worker, token, granted) where `granted` is what the radio can do nearest to `want` (and `need`)"""
        need, want = choose.normalize(need, "need"), choose.normalize(want, "want")
        last = None
        candidates = self.choose(dtype, serial, label, need, want)
        for i, (kind, target, caps) in enumerate(candidates):
            try:
                if kind == "hosted":
                    worker, token = target, target.claim(program)
                    worker.need, worker.want = need or None, want or None
                    self.log.info("%s: taken over by %s", worker.id, program or "a client")
                else:
                    worker, token = self.open_device(target.driver, init_args, init_kwargs, (), dict(target.open_args),
                                                     controlled=True, wire=wire, program=program, need=need, want=want, unit=target)
            except Exception as e:      # taken, gone since the scan, in use by another process, no permission...: next best
                self.log.warning("could not open %s: %s", getattr(target, "label", getattr(target, "id", target)), e)
                last = e
                continue
            live = worker.device.caps
            bad = choose.problems(live, need) if need else []
            if bad:     # the unit is not quite what its driver's class said: it refined its capabilities once open
                self.log.warning("%s does not meet the need once open: %s", worker.id, "; ".join(bad))
                last = err.NoDevice(f"{worker.id}: {'; '.join(bad)}")
                self._give_back(kind, worker)
                continue
            if want and any(choose.rank(c, want) < choose.rank(live, want) for _, _, c in candidates[i + 1:]):
                # opened, it does less of `want` than the class said (discrete rates, a narrower tuner) and a later candidate
                # is expected to do more: let that one have it
                self.log.info("%s does less of the request once open than the next candidate is expected to", worker.id)
                last = err.NoDevice(f"{worker.id} meets less of `want` than another radio")
                self._give_back(kind, worker)
                continue
            granted = choose.granted(live, {**need, **want}) if (want or need) else {}
            return worker, token, granted
        raise err.NoDevice(f"every radio that fits the request failed to open: {last}")

    def relay(self, address, device=None, *, open=None, control="auto", name=None, timeout=5.0, transport="auto"):
        """Host a device of another server here, so programs on this machine share one connection to it (see `RelayWorker`).

        address: the far server's endpoint. device: the id of a device it hosts (e.g. "hackrf-0"); or open: a device type to
        open there instead (the relay then controls it, and closing the relay closes it).
        control: take control of the device if it is free ("auto"), insist on it (True: `err.Busy` if someone has it), or only
        watch it (False: local programs can receive but not change anything).
        name: the id it has here (default: the far device's). Returns the `RelayWorker`.
        """
        if (device is None) == (open is None):
            raise ValueError("give exactly one of `device` (a hosted device's id) or `open` (a device type to open there)")
        upstream = RemoteDevice(address, open, id=device, timeout=timeout, transport=transport)
        upstream.open()
        try:
            if open is None and control:
                try:
                    upstream.claim()
                except err.Busy:
                    if control is True:
                        raise
            dev_id = base = name or upstream.id
            n = 0
            while dev_id in self.workers:
                n += 1
                dev_id = f"{base}-r{n}"
            worker = RelayWorker(self, dev_id, upstream, owns=open is not None, shm=self.shm, shm_samples=self.shm_samples)
        except BaseException:
            upstream.close() if open is not None else upstream.detach()
            raise
        self.workers[worker.id] = worker
        self.log.info("relaying %s of %s as %s", upstream.id, address, worker.id)
        return worker

    def _worker(self, msg) -> DeviceWorker:
        dev_id = msg.get("device")
        if dev_id not in self.workers:
            raise err.NoDevice(f"no hosted device {dev_id!r}")
        return self.workers[dev_id]

    def _controller(self, msg) -> DeviceWorker:
        worker = self._worker(msg)
        if not worker.is_controller(msg.get("token")):
            raise err.NotController(f"not the controller of {worker.id}")
        worker.touch()
        return worker

    def _dispatch(self, msg):
        op = msg.get("op")
        if op == "hello":
            return {"version": protocol.VERSION, "lease": self.lease, "types": self.types()}
        if op == "list":
            return {"types": self.types(), "devices": [w.status() for w in self.workers.values()],
                    "available": self.available()}
        if op == "open":
            program = _program(msg.get("program"))
            granted = None
            if any(msg.get(k) is not None for k in ("serial", "label", "need", "want")) or msg.get("type") is None:
                worker, token, granted = self.open_matching(
                    msg.get("type"), msg.get("serial"), msg.get("label"), msg.get("need"), msg.get("want"), program,
                    msg.get("init_args", []), msg.get("init_kwargs"), msg.get("wire"))
                self.log.info("%s: %s opened by %s", worker.id, worker.capabilities.get("name"), program or "a client")
            else:
                worker, token = self.open_device(
                    msg.get("type"), msg.get("init_args", []), msg.get("init_kwargs"),
                    msg.get("args", []), msg.get("kwargs"), controlled=True, wire=msg.get("wire"), program=program)
            return {"device": worker.id, "token": token, "lease": self.lease, "endpoints": worker.endpoints,
                    "type": worker.type, "granted": granted}
        if op == "attach":
            worker = self._worker(msg)
            if msg.get("program"):
                self.log.info("%s: %s attached", worker.id, _program(msg.get("program")))
            return {"device": worker.id, "type": worker.type, "endpoints": worker.endpoints,
                    "controlled": worker.token is not None, "wire": protocol.wire_name(worker.wire)}
        if op == "claim":
            program = _program(msg.get("program"))
            self.log.info("%s: claimed by %s", msg.get("device"), program or "a client")
            return {"token": self._worker(msg).claim(program), "lease": self.lease}
        if op == "release":
            worker = self._controller(msg)
            self.log.info("%s: released by its controller", worker.id)
            worker.release()
            return None
        if op == "ping":
            self._controller(msg)
            return {"lease": self.lease}
        if op == "close":
            worker = self._controller(msg)
            del self.workers[worker.id]
            self.log.info("%s: closed by its controller", worker.id)
            # Closing joins the driver's threads, and a wedged radio can take that a long time. This runs on the
            # C2 thread, so don't let it stop the server answering anyone: reply after CLOSE_WAIT regardless.
            closer = threading.Thread(target=worker.close, name=f"pysdrlib-{worker.id}-close", daemon=True)
            closer.start()
            closer.join(CLOSE_WAIT)
            if closer.is_alive():
                self.log.warning("%s: still closing after %.0fs; continuing in the background", worker.id, CLOSE_WAIT)
            return None
        raise ValueError(f"unknown op {op!r}")
