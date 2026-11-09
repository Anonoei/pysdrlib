"""pysdrlib server: hosts hardware SDRs for remote clients.

    server = Server("tcp://0.0.0.0:5555", port_range=(6000, 6100))
    server.serve_forever()          # or server.start() ... server.stop()

The C2 socket (the address given to `Server`) manages lifecycle and ownership. Every hosted device
gets its own control socket and RX stream socket on separate ports, each served by its own
threads, so a slow call on one device never blocks another. See `protocol` for the wire format.

Ownership: a device has at most one controller (the holder of its token) and any number of viewers.
Viewers may subscribe to RX and call `get_*` methods. A controller that stops talking to the server
for `lease` seconds loses control (RX/TX are stopped, the device stays open and can be claimed).
"""
import hmac
import secrets
import threading
import time

import zmq

from .. import devices
from .. import err
from .. import logger
from . import protocol

POLL_MS = 100
IDLE_SLEEP = 0.002

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
    def __init__(self, server: "Server", dev_id: str, dtype: str, device, wire="native"):
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

        self.tx_refused = 0                     # TX blocks dropped: not from the controller, or not transmitting
        self.ctrl = server._socket(zmq.ROUTER)
        self.rx = server._socket(zmq.PUB)
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
    def claim(self) -> str:
        """Make a new controller token the owner. Raises `err.Busy` if the device already has a controller"""
        with self.lock:
            if self.token is not None:
                raise err.Busy(f"{self.id} already has a controller")
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
            self.streaming.clear()
            for flag, stop in (("rx", self.device.stop_rx), ("tx", self.device.stop_tx)):
                if self.device.state.get(flag):
                    try:
                        stop()
                    except Exception:
                        self.log.exception("stop_%s failed during release", flag)

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
            "config": cls.CONFIG.json() if cls.CONFIG is not None else None,
            "methods": methods,
            "native_wire": protocol.wire_name(protocol.resolve_wire("native", self.device)),
            "endpoints": self.endpoints,
        }

    def status(self):
        """What `list` shows clients: id, type, name, state, frequency, sample rate, wire format and whether it is controlled"""
        with self.lock:
            return {
                "id": self.id, "type": self.type, "name": type(self.device).NAME,
                "state": dict(self.device.state),
                "cf": self.device.get_freq(), "Fs": self.device.get_sample_rate(),
                "controlled": self.token is not None,
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
                return getattr(self.device, method)(*args, **kwargs)
            finally:
                if self.device.state.get("rx"):
                    self.streaming.set()
                else:
                    self.streaming.clear()

    # --- RX stream --- #
    def _pump_loop(self):
        """Publish every channel the device streams, each block tagged with its channel and the index of its first
        sample, so clients keep the channels apart and aligned and see where samples were lost"""
        stalled = False   # log a stall once, not on every poll
        while not self._stop.is_set():
            if not self.streaming.wait(POLL_MS / 1000):
                continue
            sent = 0
            try:
                for ch in tuple(self.device.rx_channels):
                    samples, index = self.device.read_available(ch)
                    now = time.time()
                    for i in range(0, len(samples), protocol.MAX_BLOCK_SAMPLES):
                        header, payload = protocol.pack_block(
                            self._seq, now, samples[i:i + protocol.MAX_BLOCK_SAMPLES], self.wire, channel=ch, index=index + i)
                        self._seq += 1
                        self.rx.send_multipart([self.id.encode(), header, payload], copy=False)
                    sent += len(samples)
                stalled = False
            except err.StreamStalled as exc:
                if not stalled:
                    self.log.warning("%s", exc)
                    stalled = True
            except Exception:
                if not self._stop.is_set() and self.streaming.is_set() and self.device.state.get("rx"):
                    self.log.exception("reading the device failed")
            if not sent:
                time.sleep(IDLE_SLEEP)

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
        self.ctrl.close(0)
        self.rx.close(0)
        self.tx.close(0)
        with self.lock:
            if self.device.state["open"]:
                try:
                    self.device.close()
                except Exception:
                    self.log.exception("close failed")


class Server:
    def __init__(self, bind="tcp://127.0.0.1:5555", port_range=None, devices=None, lease=15.0, wire="native"):
        """
        bind: C2 endpoint, tcp://host:port (port 0 or * picks one; see `endpoint`) or ipc://path.
        port_range: (low, high) for the per-device tcp ports; default lets the OS pick.
        devices: device types clients may open (default: every vendor package).
        lease: seconds a controller may be silent before it loses control (0 disables).
        wire: default RX wire format: "native" (the device's own sample layout), "complex64", "int8", "int16", ...
        """
        self.log = logger.new("remote.server")
        self.scheme, self._host, self._port = _parse_bind(bind)
        self.port_range = tuple(port_range) if port_range else None
        self.allowed = list(devices) if devices else None
        self.lease = float(lease)
        protocol.wire_format(wire) if wire != "native" else None    # validate now
        self.wire = wire
        self.endpoint = None        # actual C2 endpoint once bound
        self.workers: dict[str, DeviceWorker] = {}
        self._counts: dict[str, int] = {}
        self._ctx = zmq.Context()
        self._c2 = None
        self._thread = None
        self._stop = threading.Event()
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
        return self

    def serve_forever(self):
        """Serve in this thread until Ctrl+C, then `stop`"""
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

    def _expire_leases(self):
        if self.lease <= 0:
            return
        now = time.monotonic()
        for w in list(self.workers.values()):
            if w.token is not None and now - w.last_seen > self.lease:
                self.log.warning("%s: controller lease expired", w.id)
                w.release()

    # --- C2 ops --- #
    def types(self):
        """Device types clients may open: the `devices` given to the server, or every driver"""
        return self.allowed if self.allowed is not None else devices.ls()

    def open_device(self, dtype, init_args=(), init_kwargs=None, args=(), kwargs=None, controlled=False, wire=None):
        """Create and open a device of vendor package `dtype` and start hosting it.
        -> (worker, token) where token is None unless `controlled`"""
        if dtype not in self.types():
            raise err.NoDevice(f"device type {dtype!r} is not available on this server (have: {', '.join(self.types())})")
        module = devices.get(dtype)
        device = module.Device(*init_args, **(init_kwargs or {}))
        device.open(*args, **(kwargs or {}))
        n = self._counts[dtype] = self._counts.get(dtype, -1) + 1
        try:
            worker = DeviceWorker(self, f"{dtype}-{n}", dtype, device, wire or self.wire)
        except BaseException:
            device.close()
            raise
        self.workers[worker.id] = worker
        self.log.info("hosting %s", worker.id)
        return worker, (worker.claim() if controlled else None)

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
            return {"types": self.types(), "devices": [w.status() for w in self.workers.values()]}
        if op == "open":
            worker, token = self.open_device(
                msg.get("type"), msg.get("init_args", []), msg.get("init_kwargs"),
                msg.get("args", []), msg.get("kwargs"), controlled=True, wire=msg.get("wire"))
            return {"device": worker.id, "token": token, "lease": self.lease, "endpoints": worker.endpoints}
        if op == "attach":
            worker = self._worker(msg)
            return {"device": worker.id, "type": worker.type, "endpoints": worker.endpoints,
                    "controlled": worker.token is not None, "wire": protocol.wire_name(worker.wire)}
        if op == "claim":
            return {"token": self._worker(msg).claim(), "lease": self.lease}
        if op == "release":
            self._controller(msg).release()
            return None
        if op == "ping":
            self._controller(msg)
            return {"lease": self.lease}
        if op == "close":
            worker = self._controller(msg)
            del self.workers[worker.id]
            worker.close()
            return None
        raise ValueError(f"unknown op {op!r}")
