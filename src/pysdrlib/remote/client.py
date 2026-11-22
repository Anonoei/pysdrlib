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
import functools
import inspect
import socket
import threading
import time

import numpy as np
import zmq

from .. import err
from .. import logger
from ..base import caps as _caps
from ..base import convert
from ..base.device import Device
from ..base.watchdog import StreamStats
from . import choose
from . import protocol
from . import shm

DEFAULT_TIMEOUT = 5.0
PULL_LIMIT = 256   # blocks taken from the socket per call (its receive high-water mark)
STALL_TIMEOUT = 2.0   # seconds without samples before `read` asks the server why (RemoteDevice.stall_timeout)
PROBE_TIMEOUT = 2.0   # seconds to wait for its answer before the server counts as gone


def _rx_synced(method):
    """Run a method that touches the receive state with its lock held: a program can read on one thread while another calls
    the device (start_rx, flush), and a server relaying a remote device does exactly that"""
    @functools.wraps(method)
    def locked(self, *args, **kwargs):
        with self._rx_lock:
            return method(self, *args, **kwargs)
    return locked


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
        return self.exchange(op, **fields).get("result")

    def exchange(self, op, **fields) -> dict:
        """`request`, returning the whole reply (its `result`, and the `stream` change a call made)"""
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
        return msg

    def close(self):
        self.sock.close(0)


def _hello(c2, address):
    """The server's `hello` (its protocol version, lease, device types); `err.ProtocolError` unless it speaks ours"""
    from .. import __version__
    info = c2.request("hello", version=protocol.VERSION, pysdrlib=__version__)   # a server refuses a version not its own
    why = protocol.mismatch(protocol.VERSION, __version__, info.get("version"), info.get("pysdrlib"), "server")
    if why:   # a server from before it checked
        raise err.ProtocolError(f"{address}: {why}")
    return info


class Client:
    """Talks to a server's C2 socket: discovery. Use `RemoteDevice` to work with a device."""
    def __init__(self, address: str, timeout=DEFAULT_TIMEOUT):
        self.address = address
        self._c2 = _Channel(address, timeout)
        self.info = _hello(self._c2, address)

    def types(self):
        """Device types the server can open"""
        return self.info["types"]

    def list(self):
        """Devices the server currently hosts"""
        return self._c2.request("list")["devices"]

    def available(self):
        """Radios attached to the server's machine as of its last scan: dicts with `driver`, `label`, `serial`, `index` and
        `hosted` (the id of the device that has it open, or None)"""
        return self._c2.request("list").get("available", [])

    def metrics(self, device=None):
        """What the server's devices (or the one with id `device`) have passed since hosted: see `Server.metrics`"""
        return self._c2.request("metrics", device=device)

    def candidates(self, device=None, serial=None, label=None, need=None, want=None):
        """The server's radios that fit the request, best first, without opening any (see `Server.candidates`); raises
        `err.NoDevice` saying why when none do"""
        return self._c2.request("choose", type=device, serial=serial, label=label, need=need, want=want)["candidates"]

    def close(self):
        """Close the connection"""
        self._c2.close()

    def __enter__(self):
        return self
    def __exit__(self, *exc):
        self.close()


class RemoteDevice(Device):
    NAME = "pysdrlib Remote Device"

    def __init__(self, address: str, device=None, *, id=None, timeout=DEFAULT_TIMEOUT, init_args=(), init_kwargs=None, wire=None, tx_wire="native",
                 transport="auto", program=None, serial=None, label=None, need=None, want=None, pick=None):
        """
        address: server C2 endpoint, e.g. tcp://host:5555
        device: device type to open on the server (this client becomes its controller)
        id: id of an already hosted device to attach to (as a viewer) instead
        program: the name of the program you are running: the server shows it in `list` and its log, so others can tell who
              has a radio
        serial, label: which unit to open: the serial number, or part of the label (`pysdrlib client ADDRESS list` shows both).
              With these, `device` may be left out to take a unit of any driver.
        need, want: what the program needs (a radio that cannot is not used) and would like: dicts with `freq` (Hz, or a
              (low, high) pair), `rate`, `bandwidth`, `channels`, `tx`. The server opens the best radio that meets `need`
              (or takes over a free hosted one); `granted` says what it can do nearest to `want` and `need`.
        pick: the `key` of one of `Client.candidates`: open that radio and no other (`err.Busy` if it has been taken since).
        init_args/init_kwargs: passed to the device type's constructor on the server
        wire: RX wire format to request when opening a device type (default: the server's, normally the
              device's native layout). See `set_wire`.
        tx_wire: wire format the TX stream is sent in: "native" (the device's own layout, lossless: the device
              quantises to it anyway, and it is several times smaller than complex64), "complex64", "int8", ...
              See `set_tx_wire`.
        transport: how samples reach this client. "auto": from shared memory when the server is on this machine and
              offers it (any number of clients then read one copy, each at its own pace), else over the socket; "zmq":
              always over the socket; "shm": shared memory, or `err.NotSupported`.
        """
        choosing = any(v is not None for v in (serial, label, need, want, pick))
        if id is not None and (device is not None or choosing):
            raise ValueError("`id` attaches to a hosted device: it takes no `device`, `serial`, `label`, `need` or `want`")
        if id is None and device is None and not choosing:
            raise ValueError("give one of `device` (a type to open), `id` (a hosted device), or `serial`/`label`/`need`/`want` to choose a radio")
        self.program = program
        self.granted = None         # what the radio the server chose can do nearest to `want` (see `need`, `want`)
        self._choice = {k: v for k, v in (("serial", serial), ("label", label), ("need", choose.normalize(need, "need") or None),
                                          ("want", choose.normalize(want, "want") or None), ("pick", pick)) if v is not None}
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
        if transport not in ("auto", "zmq", "shm"):
            raise ValueError(f"transport is 'auto', 'zmq' or 'shm', not {transport!r}")
        self.transport = transport
        self._endpoints = None      # the sockets the server handed out (kept for a later claim)
        self._lost = {}             # channel -> samples lost between the server and here since start_rx
        self._rx_lock = threading.RLock()   # guards the receive state below (see `_rx_synced`)
        self._shm_mode = False      # samples come from shared memory (see `_subscribe`)
        self._rings = {}            # channel -> shm.ShmReader of the stream's ring
        self._retired = collections.deque(maxlen=32)   # names of the rings closed (an old stream's)
        self._pos = {}              # channel -> stream index of the next sample to read from its ring
        self._from_start = False    # this client started the stream: read its rings from their first sample
        self._host = _host_of(address)
        self._c2 = None
        self._ctrl = None
        self._sub = None
        self._last_seq = None
        self._bufs = {}             # channel -> deque of [index of first sample, samples] received, not yet read
        self._changes_seen = set()  # `seq` of the stream changes applied (see `_apply_change`)
        self._rx_last = {}          # channel -> index after the last sample received
        self._rx_time = None        # time.monotonic() of the last block
        self._idle_since = None     # time.monotonic() a read first found nothing, with no block yet (see `_rx_idle`)
        self._probed = 0.0          # time.monotonic() the server was last asked how the device is
        self.stall_timeout = STALL_TIMEOUT   # seconds without samples before `read` asks the server why; None: never
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
            lease = _hello(self._c2, self.address)["lease"]
            if self.id is None:
                res = self._c2.request("open", type=self.type, args=list(args), kwargs=kwargs,
                                       init_args=self._init[0], init_kwargs=self._init[1], wire=self._wire,
                                       program=self.program, **self._choice)
                self.token = res["token"]
                self.id = res["device"]
                self.type = res.get("type", self.type)
                self.granted = res.get("granted")
            else:
                if args or kwargs:
                    raise ValueError("an attached device is already open; open() takes no arguments")
                res = self._c2.request("attach", device=self.id, program=self.program)
            self._endpoints = res["endpoints"]
            self._connect(res["endpoints"])
            self.capabilities = self._ctrl.request("capabilities")
            self._subscribe()
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
        self._sub.connect(self._endpoint(endpoints["rx"]))
        self._last_seq = None
        self._connect_tx()

    def _connect_tx(self):
        """The socket the controller sends the samples it transmits on (made once there is a token, so also after `claim`)"""
        endpoints = self._endpoints or {}
        if self.token and self._tx is None and "tx" in endpoints:   # only the controller transmits
            self._tx = zmq.Context.instance().socket(zmq.PUSH)
            self._tx.setsockopt(zmq.LINGER, 0)
            self._tx.setsockopt(zmq.SNDHWM, 4)
            self._tx.connect(self._endpoint(endpoints["tx"]))

    def _subscribe(self):
        """Choose how samples arrive and subscribe to it: the notices of the server's shared-memory rings, or the blocks"""
        caps = self.capabilities
        local = (self._host is None or self._host in ("127.0.0.1", "localhost", "::1")
                 or caps.get("shm_host") == socket.gethostname())
        if self.transport == "shm" and not (caps.get("shm") and local):
            raise err.NotSupported(f"the server at {self.address} offers no shared memory to this machine")
        self._shm_mode = self.transport != "zmq" and bool(caps.get("shm")) and local
        topic = self.id.encode()
        self._sub.setsockopt(zmq.SUBSCRIBE, protocol.SHM_PREFIX + topic if self._shm_mode else topic)

    def _use_sockets(self, why):
        """Shared memory isn't available after all (the server is elsewhere, or could not make a segment): take blocks
        over the socket instead"""
        if not self._shm_mode:
            return
        if self.transport == "shm":
            raise err.NotSupported(f"shared memory: {why}")
        self.log.info("using the socket for samples (%s)", why)
        self._shm_mode = False
        topic = self.id.encode()
        self._sub.setsockopt(zmq.UNSUBSCRIBE, protocol.SHM_PREFIX + topic)
        self._sub.setsockopt(zmq.SUBSCRIBE, topic)
        self._close_rings()

    def _close_rings(self):
        for reader in self._rings.values():
            self._retired.append(reader.name)   # a notice of it still on its way must not bring it back
            reader.close()
        self._rings.clear()
        self._pos.clear()

    def close(self):
        """Disconnect. The controller also closes the device on the server; a viewer just detaches."""
        self._ensure_open("close")
        try:
            if self.token:
                self._c2.request("close", device=self.id, token=self.token)
        finally:
            self._teardown()
            self.state.update(open=False, rx=False, tx=False)

    def detach(self):
        """Disconnect without closing the hosted device (`close` closes it, if this client controls it). A controller
        gives up control first, which stops what it had running, as `release` does"""
        self._ensure_open("detach")
        try:
            if self.token:
                self.release()
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
        self._close_rings()
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
        res = self._c2.request("claim", device=self.id, program=self.program)
        self.token = res["token"]
        self._connect_tx()
        self._start_heartbeat(res["lease"])

    def release(self):
        """Give up control (stops RX/TX on the server); stay attached as a viewer"""
        self._ensure_open("release")
        if not self.token:
            return
        token, self.token = self.token, None
        self._beat_stop.set()
        self._c2.request("release", device=self.id, token=token)
        if self._tx is not None:   # only the controller transmits
            self._tx.close(0)
            self._tx = None
        self.refresh()

    # --- remote calls --- #
    def _call(self, method, *args, **kwargs):
        self._ensure_open(method)
        if method == "start_rx":   # what is still queued belongs to the last stream
            self._drop_received()
        reply = self._ctrl.exchange("call", method=method, args=list(args), kwargs=kwargs, token=self.token)
        result = reply.get("result")
        if reply.get("stream"):   # what the call changed in the stream: known before the next read
            with self._rx_lock:
                self._apply_change(reply["stream"])
        if method in ("set_rx_freq", "get_rx_freq"):
            self._cf = result
        elif method in ("set_rx_sample_rate", "get_rx_sample_rate"):
            self._Fs = result
        elif method in _SYNC_AFTER:
            self.refresh()
        return result

    def _rx_not_started(self):
        if not self.is_controller and self.state["open"]:   # a viewer: the controller may have started it since
            self.refresh()
            if self.state["rx"]:
                return
            raise err.NotStreaming(f"{self.id} isn't receiving on the server: its controller hasn't started it")
        super()._rx_not_started()

    def refresh(self):
        """Update `state`, `CF` and `Fs` from the server (a viewer sees what the controller does)"""
        self._ensure_open("refresh")
        status = self._ctrl.request("status")
        self.state.update(status["state"])
        self._cf = status["cf"]
        self._Fs = status["Fs"]
        self.wire = status["wire"]
        self._rx_channels = tuple(status["rx_channels"])
        self._tx_channels = tuple(status["tx_channels"])
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
            self._remote_caps = _caps.from_json(self._call("get_caps"))
        return self._remote_caps

    # --- samples --- #
    # The server publishes every channel it streams in blocks tagged with the channel and the index of their first
    # sample. They are kept per channel here; Device.read (and read(channel=(0, 1)), stream(), gaps...) reads them
    # through the receive hooks below, as it reads a local device's queues.
    @_rx_synced
    def _pull(self, timeout):
        """Take the blocks that have arrived, waiting up to `timeout` seconds for the first. At most as many as the
        socket queues (RCVHWM): a server producing faster than they can be decoded must not keep this from returning"""
        wait = max(0, int(timeout * 1000))
        for _ in range(PULL_LIMIT):
            if not self._sub.poll(wait):
                break
            wait = 0
            _, header, payload = self._sub.recv_multipart(copy=False)
            if protocol.is_event(header.bytes):
                self._apply_change(protocol.unpack_event(payload.buffer))
                continue
            if self._shm_mode:
                self._notice(header.bytes, payload.buffer)
                continue
            seq, _, samples, channel, index = protocol.unpack_block(header.bytes, payload.buffer)
            if self._last_seq is not None and seq > self._last_seq + 1:
                self.dropped_blocks += seq - self._last_seq - 1
            self._last_seq = seq
            if index < self._rx_last.get(channel, 0):   # the indices went back: someone restarted the stream
                self._stream_restarted()
            if index > self._rx_last.get(channel, index):   # blocks the socket dropped on the way
                self._lost[channel] = self._lost.get(channel, 0) + index - self._rx_last[channel]
            self._bufs.setdefault(channel, collections.deque()).append([index, samples])
            self._rx_last[channel] = index + len(samples)
            self._rx_time = time.monotonic()

    def _apply_change(self, change):
        """A change to the stream on the server (retunes, gains, a flush; see `protocol.change`): recorded as
        `Device._note` records a local device's, so `read` reports it in RxMeta and skips what a flush discarded. Each
        once: the caller gets its own both in the reply and on the stream"""
        seq = change.get("seq")
        if seq in self._changes_seen:
            return
        if len(self._changes_seen) > 4096:
            self._changes_seen.clear()
        self._changes_seen.add(seq)
        for index, kind, value, ch in change.get("events", ()):
            self._seed_now(kind, (ch,))
            self._rx_events.append((int(index), kind, value, ch))
        self._rx_discard = max(self._rx_discard, int(change.get("discard", 0)))

    def _seed_now(self, kind, channels):
        """Before a change of `kind` is recorded: what was in effect until then (the blocks before it are labelled
        with that, not with the new value)"""
        before = {"freq": self._cf, "rate": self._Fs}.get(kind)
        if before is not None:
            for ch in channels:
                self._rx_now.setdefault((kind, ch), before)

    def _stream_restarted(self):
        """A new stream (indices from 0 again): the last one's positions, flush point and changes are void"""
        self._rx_next, self._rx_now, self._rx_events, self._rx_discard = {}, {}, [], 0
        self._rx_last.clear()
        self._lost.clear()

    def _notice(self, header, payload):
        """A block was written to a ring: attach to the ring if it is a new one, and note when it arrived"""
        _, _, channel, index, count, name = protocol.unpack_notice(header, payload)
        self._rx_time = time.monotonic()
        reader = self._rings.get(channel)
        if reader is not None and reader.name == name:
            return
        if not name:
            return self._use_sockets("the server could not make a shared-memory segment")
        if name in self._retired:   # sent before the stream it belongs to was stopped
            return
        try:
            new = shm.ShmReader(name)
        except (FileNotFoundError, ValueError, OSError) as exc:
            return self._use_sockets(f"{name} can't be attached: {exc}")
        # A new stream is read from its first sample; one already running, from this block on
        restarted = reader is not None and reader.stream_id != new.stream_id
        fresh = self._from_start or restarted
        if restarted:
            self._stream_restarted()
        if reader is not None:
            reader.close()
        self._rings[channel], self._pos[channel] = new, 0 if fresh else index

    def _queued(self, channel):
        if self._shm_mode:
            reader = self._rings.get(channel)
            return 0 if reader is None else max(0, min(reader.head - self._pos[channel], reader.ring.capacity))
        return sum(len(x) for _, x in self._bufs.get(channel, ()))

    @_rx_synced
    def _drop_received(self):
        if self._sub is not None:
            self._pull(0)
        self._rx_time = self._idle_since = None
        self._bufs.clear()
        self._stream_restarted()
        self._close_rings()   # the old stream's; the new one has rings of its own
        self._from_start = self.is_controller

    def _rx_idle(self):
        """Nothing has arrived for `stall_timeout` seconds: ask the server (at most once per `stall_timeout`) whether
        it is there and how its device is. err.Lost when it doesn't answer, no longer hosts the device or the device
        is gone or failing; err.StreamStalled when the device stopped delivering"""
        if self.stall_timeout is None:
            return
        now = time.monotonic()
        last = self._rx_time if self._rx_time is not None else self._idle_since
        if last is None:
            self._idle_since = now
            return
        if now - last < self.stall_timeout or now - self._probed < self.stall_timeout:
            return
        self._probed = now
        try:
            ch = _Channel(self.address, min(self.timeout, PROBE_TIMEOUT))
            try:
                hosted = {d["id"]: d for d in ch.request("list")["devices"]}
            finally:
                ch.close()
        except (TimeoutError, zmq.ZMQError) as e:
            raise err.Lost(f"{self.id}: no samples for {now - last:.1f}s, and the server at {self.address} doesn't "
                           f"answer ({e})") from None
        status = hosted.get(self.id)
        if status is None:
            raise err.Lost(f"{self.id}: the server at {self.address} no longer hosts it")
        health = status.get("health", "ok")
        if health in ("lost", "failing"):
            raise err.Lost(f"{self.id}: the server's device is {'gone' if health == 'lost' else 'failing'}")
        if health == "stalled":
            raise err.StreamStalled(f"{self.id}: the server's device stopped delivering samples")
        if not status.get("state", {}).get("rx"):
            raise err.NotStreaming(f"{self.id} isn't receiving on the server (its controller stopped it)")

    @_rx_synced
    def _rx_wait(self, count, timeout, slot=0):
        channel = self._rx_channels[slot]
        deadline = time.monotonic() + timeout
        while self._queued(channel) < count:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            self._pull(min(left, 0.1))
        return self._queued(channel)

    @_rx_synced
    def _rx_read_into(self, out, timeout, slot=0):
        if not self._shm_mode:
            return super()._rx_read_into(out, timeout, slot)
        channel = self._rx_channels[slot]
        deadline = time.monotonic() + timeout
        self._pull(0)   # changes (events) already sent apply to what is read now
        while True:
            reader = self._rings.get(channel)
            if reader is not None:
                pos = self._pos[channel]
                n, first = reader.read(out, pos)
                if n or first > pos:   # data, or the reader was lapped or in a gap: go on from there
                    self._lost[channel] = self._lost.get(channel, 0) + first - pos
                    self._pos[channel] = first + n
                    if n:
                        return n, first
                    continue
            left = deadline - time.monotonic()
            if left <= 0:
                return 0, self._pos.get(channel, 0)
            self._pull(min(left, 0.1))   # until the server writes again

    @_rx_synced
    def _rx_read(self, count, slot=0):
        if self._shm_mode:
            out = np.empty(count, dtype=np.complex64)
            n, first = self._rx_read_into(out, 0.0, slot)
            return out[:n], first
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

    @_rx_synced
    def _rx_position(self, slot=0):
        age = None if self._rx_time is None else time.monotonic() - self._rx_time
        channel = self._rx_channels[slot]
        if self._shm_mode:
            reader = self._rings.get(channel)
            return (0 if reader is None else reader.head), age
        return self._rx_last.get(channel, 0), age

    @_rx_synced
    def _rx_flush(self, slot=0):
        self._pull(0)
        channel = self._rx_channels[slot]
        if self._shm_mode:
            reader = self._rings.get(channel)
            if reader is None:
                return 0
            skipped, self._pos[channel] = self._queued(channel), reader.head
            return skipped
        blocks = self._bufs.pop(self._rx_channels[slot], ())
        return sum(len(x) for _, x in blocks)

    @property
    def stats(self) -> StreamStats:
        """What this client has received of the first channel since `start_rx`: `samples`, `dropped` (lost between the
        server and here: blocks the socket dropped, or samples a slow reader was lapped for in shared memory) and `age` (since
        the server last wrote). `missing` and `stalls` are counted by the server's own device, not here"""
        channel = self._rx_channels[0]
        age = None if self._rx_time is None else time.monotonic() - self._rx_time
        seen = self._pos.get(channel, 0) if self._shm_mode else self._rx_last.get(channel, 0)
        lost = self._lost.get(channel, 0)
        return StreamStats(max(0, seen - lost), lost, 0, 0, age)

    @_rx_synced
    def get_samples(self, timeout=0.0) -> np.ndarray:
        """All samples of the first streaming channel received since the last call. Waits up to `timeout` seconds
        for the first block. See `read` for exact counts, several channels and stream metadata"""
        self._ensure_open("get_samples")
        channel = self._rx_channels[0]
        if not self._queued(channel):
            self._pull(timeout)
        self._pull(0)
        if self._shm_mode:
            return self._take_all(channel)
        blocks = self._bufs.pop(channel, ())
        if not blocks:
            return np.empty(0, dtype=np.complex64)
        return blocks[0][1] if len(blocks) == 1 else np.concatenate([x for _, x in blocks])

    @_rx_synced
    def _take_all(self, channel):
        """Everything in a channel's ring since this client last read it (gaps skipped)"""
        reader = self._rings.get(channel)
        parts = []
        while reader is not None:
            pos = self._pos[channel]
            out = np.empty(min(max(reader.head - pos, 1), 1 << 22), dtype=np.complex64)
            n, first = reader.read(out, pos)
            self._pos[channel] = first + n
            if n:
                parts.append(out[:n])
            elif first == pos:
                break
        return np.concatenate(parts) if len(parts) > 1 else (parts[0] if parts else np.empty(0, dtype=np.complex64))

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

# Device's public methods run on the server (against the hosted driver: its caps, gain split, state), except those that
# read samples, which come over the stream, and those that only read `caps` (fetched once per connection)
_LOCAL = frozenset({"open", "close", "get_samples", "read", "read_into", "stream", "read_available", "wait_available",
                    "flush", "write", "get_tx_stats", "get_caps", "settings", "clock_sources",
                    "rx_gain_stages", "tx_gain_stages", "rx_gain_modes", "tx_gain_modes", "rx_antennas", "tx_antennas"})
for _name, _fn in list(vars(Device).items()):
    if not _name.startswith("_") and inspect.isfunction(_fn) and not inspect.isgeneratorfunction(_fn) \
            and _name not in _LOCAL and _name not in vars(RemoteDevice):   # (a generator, `sweep`, reads: it runs here)
        setattr(RemoteDevice, _name, _forwarder(_name))
del _name, _fn
