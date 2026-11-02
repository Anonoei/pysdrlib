"""pysdrlib client: use a device hosted by a pysdrlib server like a local one.

    sdr = pysdrlib.device("remote", "tcp://host:5555", "hackrf")   # a device type: this client controls it
    sdr.open()
    sdr.initialize(cf=100e6, Fs=10e6)
    sdr.start_rx()
    samples = sdr.get_samples()

    view = pysdrlib.device("remote", "tcp://host:5555", id="hackrf-0")  # an already hosted device: a viewer
    view.open()                       # can read samples and `get_*`; view.claim() to take control if it is free

`RemoteDevice` is a `Device`, so code written for a local device works unchanged. Any other public
method of the hosted device (e.g. its per-stage gain setters) is reachable by name too.
"""
import threading
import time

import numpy as np
import zmq

from .. import err
from .. import logger
from ..base.device import Device
from . import protocol

DEFAULT_TIMEOUT = 5.0


def _host_of(address: str):
    scheme, _, rest = address.partition("://")
    if scheme != "tcp":
        return None
    return rest.rpartition(":")[0]


class _Channel:
    """Request/reply over a DEALER socket. Thread-safe: one request at a time."""
    def __init__(self, endpoint: str, timeout: float):
        self.endpoint = endpoint
        self.timeout = timeout
        self.sock = zmq.Context.instance().socket(zmq.DEALER)
        self.sock.setsockopt(zmq.LINGER, 0)
        self.sock.connect(endpoint)
        self._id = 0
        self._lock = threading.Lock()

    def request(self, op, **fields):
        with self._lock:
            self._id += 1
            req_id = self._id
            self.sock.send(protocol.pack({"id": req_id, "op": op, **fields}))
            deadline = time.monotonic() + self.timeout
            while True:
                left = deadline - time.monotonic()
                if left <= 0 or not self.sock.poll(int(left * 1000) + 1):
                    raise TimeoutError(f"no reply from {self.endpoint} to `{op}` within {self.timeout}s")
                msg = protocol.unpack(self.sock.recv())
                if msg.get("id") == req_id:  # drop late replies to timed out requests
                    break
        if not msg.get("ok"):
            protocol.raise_error(msg.get("error", {}))
        return msg.get("result")

    def close(self):
        self.sock.close(0)


class Client:
    """Talks to a server's C2 socket: discovery. Use `RemoteDevice` to work with a device."""
    def __init__(self, address: str, timeout=DEFAULT_TIMEOUT):
        self.address = address
        self._c2 = _Channel(address, timeout)
        self.info = self._c2.request("hello")
        if self.info.get("version") != protocol.VERSION:
            raise err.ProtocolError(f"server speaks protocol {self.info.get('version')}, this is {protocol.VERSION}")

    def types(self):
        """Device types the server can open"""
        return self.info["types"]

    def list(self):
        """Devices the server currently hosts"""
        return self._c2.request("list")["devices"]

    def close(self):
        self._c2.close()

    def __enter__(self):
        return self
    def __exit__(self, *exc):
        self.close()


class RemoteDevice(Device):
    NAME = "pysdrlib Remote Device"

    def __init__(self, address: str, device=None, *, id=None, timeout=DEFAULT_TIMEOUT, init_args=(), init_kwargs=None, wire=None):
        """
        address: server C2 endpoint, e.g. tcp://host:5555
        device: device type to open on the server (this client becomes its controller)
        id: id of an already hosted device to attach to (as a viewer) instead
        init_args/init_kwargs: passed to the device type's constructor on the server
        wire: RX wire format to request when opening a device type (default: the server's, normally the
              device's native layout). See `set_wire`.
        """
        if (device is None) == (id is None):
            raise ValueError("give exactly one of `device` (a type to open) or `id` (a hosted device)")
        self.log = logger.new(f"device.{type(self).__name__}")
        self.device = None
        self.state = {"open": False, "rx": False, "tx": False}
        self._cf = None
        self._Fs = None

        self.address = address
        self.timeout = timeout
        self.type = device
        self.id = id
        self.token = None
        self.capabilities = None
        self.dropped_blocks = 0     # RX blocks lost between server and here (detected by sequence number)
        self._init = (list(init_args), dict(init_kwargs or {}))
        self._wire = wire
        self.wire = None            # format of the RX stream, e.g. "int8" (see `refresh`)
        self._host = _host_of(address)
        self._c2 = None
        self._ctrl = None
        self._sub = None
        self._last_seq = None
        self._leftover = np.empty(0, dtype=np.complex64)
        self._beat = None
        self._beat_stop = threading.Event()

    def __str__(self):
        return f"{type(self).__name__}({self.id}@{self.address}): {self.state}"

    @property
    def is_controller(self):
        return self.token is not None

    # --- connection --- #
    def _endpoint(self, ep):
        if "{host}" in ep:
            if self._host is None:
                raise err.ProtocolError(f"server sent a {{host}} endpoint but {self.address} isn't tcp")
            ep = ep.replace("{host}", self._host)
        return ep

    def open(self, *args, **kwargs):
        """Connect to the server. For a device type: opens it on the server (args/kwargs go to the device's `open`)"""
        if self.state["open"]:
            self.log.warning("Device is already open!")
            return
        self._c2 = _Channel(self.address, self.timeout)
        try:
            if self.type is not None:
                res = self._c2.request("open", type=self.type, args=list(args), kwargs=kwargs,
                                       init_args=self._init[0], init_kwargs=self._init[1], wire=self._wire)
                self.token = res["token"]
                self.id = res["device"]
                lease = res["lease"]
            else:
                if args or kwargs:
                    raise ValueError("an attached device is already open; open() takes no arguments")
                res = self._c2.request("attach", device=self.id)
                lease = self._c2.request("hello")["lease"]
            self._connect(res["endpoints"])
            self.capabilities = self._ctrl.request("capabilities")
            self.state["open"] = True
            self.refresh()
        except BaseException:
            self._teardown()
            raise
        if self.token:
            self._start_heartbeat(lease)

    def _connect(self, endpoints):
        self._ctrl = _Channel(self._endpoint(endpoints["ctrl"]), self.timeout)
        self._sub = zmq.Context.instance().socket(zmq.SUB)
        self._sub.setsockopt(zmq.LINGER, 0)
        self._sub.setsockopt(zmq.RCVHWM, 256)
        self._sub.setsockopt(zmq.SUBSCRIBE, self.id.encode())
        self._sub.connect(self._endpoint(endpoints["rx"]))
        self._last_seq = None

    def close(self):
        """Disconnect. The controller also closes the device on the server; a viewer just detaches."""
        self._ensure_open("close")
        try:
            if self.token:
                self._c2.request("close", device=self.id, token=self.token)
        finally:
            self._teardown()
            self.state.update(open=False, rx=False, tx=False)

    def _teardown(self):
        self._beat_stop.set()
        if self._beat is not None:
            self._beat.join()
            self._beat = None
        for name in ("_ctrl", "_c2"):
            ch = getattr(self, name)
            if ch is not None:
                ch.close()
                setattr(self, name, None)
        if self._sub is not None:
            self._sub.close(0)
            self._sub = None
        self.token = None

    def _start_heartbeat(self, lease):
        if not lease or lease <= 0:
            return
        self._beat_stop.clear()
        def beat(period=lease / 3):
            ch = _Channel(self.address, self.timeout)
            try:
                while not self._beat_stop.wait(period):
                    token = self.token
                    if token is None:
                        break
                    try:
                        ch.request("ping", device=self.id, token=token)
                    except Exception as e:
                        self.log.warning("heartbeat failed: %s", e)
            finally:
                ch.close()
        self._beat = threading.Thread(target=beat, name="pysdrlib-heartbeat", daemon=True)
        self._beat.start()

    # --- ownership --- #
    def claim(self):
        """Become the controller of an attached device (raises `err.Busy` if it has one)"""
        self._ensure_open("claim")
        if self.token:
            return
        res = self._c2.request("claim", device=self.id)
        self.token = res["token"]
        self._start_heartbeat(res["lease"])

    def release(self):
        """Give up control (stops RX/TX on the server); stay attached as a viewer"""
        self._ensure_open("release")
        if not self.token:
            return
        token, self.token = self.token, None
        self._beat_stop.set()
        self._c2.request("release", device=self.id, token=token)
        self.refresh()

    # --- remote calls --- #
    def _call(self, method, *args, **kwargs):
        self._ensure_open(method)
        result = self._ctrl.request("call", method=method, args=list(args), kwargs=kwargs, token=self.token)
        if method in ("set_freq", "get_freq"):
            self._cf = result
        elif method in ("set_sample_rate", "get_sample_rate"):
            self._Fs = result
        elif method in _SYNC_AFTER:
            self.refresh()
        return result

    def refresh(self):
        """Update `state`, `CF` and `Fs` from the server (a viewer sees what the controller does)"""
        self._ensure_open("refresh")
        status = self._ctrl.request("status")
        self.state.update(status["state"])
        self._cf = status["cf"]
        self._Fs = status["Fs"]
        self.wire = status["wire"]
        return status

    def set_wire(self, fmt="native"):
        """Choose the RX stream's wire format for everyone watching: "native" (the device's own layout,
        lossless), "complex64", "int8", "int16", ... Narrower than the device's native is lossy.
        Controller only. Returns the format name."""
        self._ensure_open("set_wire")
        self.wire = self._ctrl.request("set_wire", format=fmt, token=self.token)
        return self.wire

    def __getattr__(self, name):
        # Only reached when normal lookup fails: device-specific methods of the hosted device
        caps = self.__dict__.get("capabilities")
        if not name.startswith("_") and caps and name in caps["methods"]:
            def remote_method(*args, **kwargs):
                return self._call(name, *args, **kwargs)
            remote_method.__name__ = name
            remote_method.__doc__ = caps["methods"][name]["doc"]
            return remote_method
        raise AttributeError(f"{type(self).__name__} has no attribute {name!r}")

    def __dir__(self):
        caps = self.__dict__.get("capabilities")
        return sorted(set(super().__dir__()) | set(caps["methods"] if caps else ()))

    # --- samples --- #
    def get_samples(self, timeout=0.0) -> np.ndarray:
        """All samples received since the last call. Waits up to `timeout` seconds for the first block."""
        self._ensure_open("get_samples")
        blocks = [self._leftover] if self._leftover.size else []
        self._leftover = np.empty(0, dtype=np.complex64)
        deadline = time.monotonic() + timeout
        while True:
            wait = 0 if blocks else max(0, int((deadline - time.monotonic()) * 1000))
            if not self._sub.poll(wait):
                break
            _, header, payload = self._sub.recv_multipart(copy=False)
            seq, _, samples = protocol.unpack_block(header.bytes, payload.buffer)
            if self._last_seq is not None and seq > self._last_seq + 1:
                self.dropped_blocks += seq - self._last_seq - 1
            self._last_seq = seq
            blocks.append(samples)
        if not blocks:
            return np.empty(0, dtype=np.complex64)
        return blocks[0] if len(blocks) == 1 else np.concatenate(blocks)

    def read(self, count, timeout=None) -> np.ndarray:
        """Exactly `count` samples, waiting for them (`TimeoutError` after `timeout` seconds)"""
        deadline = None if timeout is None else time.monotonic() + timeout
        have = np.empty(0, dtype=np.complex64)
        parts, size = [], 0
        while size < count:
            wait = 1.0 if deadline is None else deadline - time.monotonic()
            if wait <= 0:
                raise TimeoutError(f"only got {size} of {count} samples")
            got = self.get_samples(timeout=min(wait, 1.0))
            if got.size:
                parts.append(got)
                size += got.size
        have = parts[0] if len(parts) == 1 else np.concatenate(parts)
        self._leftover = have[count:]
        return have[:count]


# Calls after which the local view of the server's state is out of date
_SYNC_AFTER = frozenset({"initialize", "start_rx", "stop_rx", "start_tx", "stop_tx"})

def _forwarder(name):
    def method(self, *args, **kwargs):
        return self._call(name, *args, **kwargs)
    method.__name__ = name
    method.__doc__ = getattr(Device, name).__doc__
    return method

# Everything Device implements locally (against CONFIG a remote device doesn't have) runs on the server instead
for _name in ("initialize", "start_rx", "stop_rx", "start_tx", "stop_tx", "set_sample_rate", "set_freq",
              "set_rx_gain", "set_tx_gain", "set_bias_t"):
    setattr(RemoteDevice, _name, _forwarder(_name))
for _name in ("get_sample_rate", "get_freq"):
    setattr(RemoteDevice, _name, _forwarder(_name))
del _name
