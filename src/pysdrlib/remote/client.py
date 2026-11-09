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
import collections
import threading
import time

import numpy as np
import zmq

from .. import err
from .. import logger
from ..base import caps as _caps
from ..base import convert
from ..base.device import Device
from ..base.stream import RxMeta
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
        """Close the connection"""
        self._c2.close()

    def __enter__(self):
        return self
    def __exit__(self, *exc):
        self.close()


class RemoteDevice(Device):
    NAME = "pysdrlib Remote Device"

    def __init__(self, address: str, device=None, *, id=None, timeout=DEFAULT_TIMEOUT, init_args=(), init_kwargs=None, wire=None, tx_wire="native"):
        """
        address: server C2 endpoint, e.g. tcp://host:5555
        device: device type to open on the server (this client becomes its controller)
        id: id of an already hosted device to attach to (as a viewer) instead
        init_args/init_kwargs: passed to the device type's constructor on the server
        wire: RX wire format to request when opening a device type (default: the server's, normally the
              device's native layout). See `set_wire`.
        tx_wire: wire format the TX stream is sent in: "native" (the device's own layout, lossless: the device
              quantises to it anyway, and it is several times smaller than complex64), "complex64", "int8", ...
              See `set_tx_wire`.
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
        protocol.wire_format(tx_wire) if tx_wire != "native" else None    # validate now
        self.tx_wire = tx_wire      # format of the TX stream we send ("native": the device's own layout)
        self.wire = None            # format of the RX stream, e.g. "int8" (see `refresh`)
        self._host = _host_of(address)
        self._c2 = None
        self._ctrl = None
        self._sub = None
        self._last_seq = None
        self._bufs = {}             # channel -> deque of [index of first sample, samples] received, not yet read
        self._rx_last = {}          # channel -> index after the last sample received
        self._rx_time = None        # time.monotonic() of the last block
        self._tx = None             # PUSH socket for TX blocks (the controller's)
        self._tx_seq = 0
        self._beat = None
        self._beat_stop = threading.Event()
        self._init_generic()
        self._remote_caps = None

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
        if self.token and "tx" in endpoints:   # only the controller transmits
            self._tx = zmq.Context.instance().socket(zmq.PUSH)
            self._tx.setsockopt(zmq.LINGER, 0)
            self._tx.setsockopt(zmq.SNDHWM, 4)
            self._tx.connect(self._endpoint(endpoints["tx"]))

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
        if self._tx is not None:
            self._tx.close(0)
            self._tx = None
        self.token = None
        self._remote_caps = None

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
        if method == "start_rx":   # what is still queued belongs to the last stream
            self._drop_received()
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
        self._rx_channels = tuple(status.get("rx_channels", (0,)))   # older servers: channel 0
        self._tx_channels = tuple(status.get("tx_channels", (0,)))
        return status

    def set_wire(self, fmt="native"):
        """Choose the RX stream's wire format for everyone watching: "native" (the device's own layout,
        lossless), "complex64", "int8", "int16", ... Narrower than the device's native is lossy.
        Controller only. Returns the format name."""
        self._ensure_open("set_wire")
        self.wire = self._ctrl.request("set_wire", format=fmt, token=self.token)
        return self.wire

    def set_tx_wire(self, fmt="native"):
        """Choose the wire format of the TX stream this client sends (it is the controller's alone, so this is local):
        "native" (the device's own layout, lossless), "complex64", "int8", "int16", ... Narrower than the device's
        native is lossy. Returns the format name."""
        if fmt != "native":
            protocol.wire_format(fmt)
        self.tx_wire = fmt
        return self.tx_wire_name

    @property
    def tx_wire_name(self):
        """Name of the format TX blocks are sent in right now (with "native" resolved to the device's layout)"""
        return protocol.wire_name(self._tx_format())

    def _tx_format(self):
        if self.tx_wire != "native":
            return protocol.wire_format(self.tx_wire)
        try:
            return protocol.wire_format((self.capabilities or {}).get("native_wire"))
        except ValueError:   # no capabilities yet, or a layout with no wire name: complex64 always works
            return convert.FLOAT32

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

    @property
    def caps(self):
        """The hosted device's capabilities (fetched once per connection)"""
        if self.__dict__.get("_remote_caps") is None:
            try:
                self._remote_caps = _caps.from_json(self._call("get_caps"))
            except AttributeError as exc:   # inside a property it would read as "no attribute 'caps'"
                raise err.NotSupported(f"the server at {self.address} runs a pysdrlib without capabilities "
                                       f"(update it): {exc}") from None
        return self._remote_caps

    def set_setting(self, name, value, channel=0):
        """Apply one of the hosted device's `settings()`"""
        return self._call("set_setting", name, value, channel)

    # --- samples --- #
    # The server publishes every channel it streams in blocks tagged with the channel and the index of their first
    # sample. They are kept per channel here; Device.read (and read(channel=(0, 1)), stream(), gaps...) reads them
    # through the receive hooks below, as it reads a local device's queues.
    def _pull(self, timeout):
        """Take the blocks that have arrived, waiting up to `timeout` seconds for the first"""
        wait = max(0, int(timeout * 1000))
        while self._sub.poll(wait):
            wait = 0
            _, header, payload = self._sub.recv_multipart(copy=False)
            seq, _, samples, channel, index = protocol.unpack_block_ex(header.bytes, payload.buffer)
            if self._last_seq is not None and seq > self._last_seq + 1:
                self.dropped_blocks += seq - self._last_seq - 1
            self._last_seq = seq
            if index is None:   # an older server: count them here
                index = self._rx_last.get(channel, 0)
            self._bufs.setdefault(channel, collections.deque()).append([index, samples])
            self._rx_last[channel] = index + len(samples)
            self._rx_time = time.monotonic()

    def _queued(self, channel):
        return sum(len(x) for _, x in self._bufs.get(channel, ()))

    def _drop_received(self):
        if self._sub is not None:
            self._pull(0)
        self._bufs.clear()
        self._rx_last.clear()

    def _rx_wait(self, count, timeout, slot=0):
        channel = self._rx_channels[slot]
        deadline = time.monotonic() + timeout
        self._pull(0)
        while self._queued(channel) < count:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            self._pull(min(left, 0.1))
        return self._queued(channel)

    def _rx_read(self, count, slot=0):
        channel = self._rx_channels[slot]
        blocks = self._bufs.get(channel)
        if not blocks:
            return np.empty(0, dtype=np.complex64), self._rx_last.get(channel, 0)
        index, x = blocks[0]   # one block at a time: a gap between blocks shows as the next index
        if len(x) > count:
            blocks[0] = [index + count, x[count:]]
            return x[:count], index
        blocks.popleft()
        return x, index

    def _rx_position(self, slot=0):
        age = None if self._rx_time is None else time.monotonic() - self._rx_time
        return self._rx_last.get(self._rx_channels[slot], 0), age

    def _rx_flush(self, slot=0):
        self._pull(0)
        blocks = self._bufs.pop(self._rx_channels[slot], ())
        return sum(len(x) for _, x in blocks)

    def get_samples(self, timeout=0.0) -> np.ndarray:
        """All samples of the first streaming channel received since the last call. Waits up to `timeout` seconds
        for the first block. See `read` for exact counts, several channels and stream metadata"""
        self._ensure_open("get_samples")
        channel = self._rx_channels[0]
        if not self._queued(channel):
            self._pull(timeout)
        self._pull(0)
        blocks = self._bufs.pop(channel, ())
        if not blocks:
            return np.empty(0, dtype=np.complex64)
        return blocks[0][1] if len(blocks) == 1 else np.concatenate([x for _, x in blocks])

    # --- transmitting: Device.write sends the blocks to the server, which writes them to the device --- #
    def _tx_write(self, samples, timeout, slot=0):
        if self._tx is None:
            if not self.token:
                raise err.NotController("only the controller can transmit")
            raise err.NotSupported(f"the server at {self.address} has no TX stream (update it)")
        channel = self._tx_channels[slot]
        fmt = self._tx_format()
        self._tx.setsockopt(zmq.SNDTIMEO, -1 if timeout is None or timeout < 0 else int(timeout * 1000))
        done = 0
        for i in range(0, len(samples), protocol.MAX_BLOCK_SAMPLES):
            part = samples[i:i + protocol.MAX_BLOCK_SAMPLES]
            header, payload = protocol.pack_block(self._tx_seq, time.time(), part, fmt, channel=channel)
            try:
                self._tx.send_multipart([self.token.encode(), header, payload], copy=False)
            except zmq.Again:   # the server (the device's queue) is full: timed out
                break
            self._tx_seq += 1
            done += len(part)
        return done

    def _tx_stats(self, slot=0):
        return self._call("get_tx_stats", self._tx_channels[slot])


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
for _name in ("set_gain", "set_gain_relative", "get_gain_relative", "set_gain_mode", "set_bandwidth", "set_antenna", "set_clock_source", "set_freq_correction",
              "get_gain", "get_gain_mode", "get_bandwidth", "get_antenna", "get_clock_source", "get_setting",
              "get_freq_correction"):
    setattr(RemoteDevice, _name, _forwarder(_name))
for _name in ("get_sample_rate", "get_freq"):
    setattr(RemoteDevice, _name, _forwarder(_name))
del _name
