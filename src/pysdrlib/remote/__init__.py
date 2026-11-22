r"""Radios hosted on one machine and used from another, over [ZeroMQ](https://zeromq.org/) (`pip install pysdrlib[remote]`).

A remote device is a normal `pysdrlib.Device` (`RemoteDevice`), so code written for a local radio works unchanged.
`sdr.caps` comes from the hosted device, and every control (`set_rx_gain`, `set_rx_bandwidth`, `set_setting`, a
driver's own setters such as `set_rx_lna_gain`...) is forwarded to it. Channels, transmit, `read(meta=True)` gaps and
`stream()` work as they do locally, and so do retunes: `m.events` marks a change inside a block, `m.freq` is the
frequency the block was received at, and `set_rx_freq(f, flush=True)` skips what came before (viewers see the
controller's changes the same way).

How to run a server, connect, find servers, share and relay a radio, and the `pysdrlib server/relay/client/discover`
command line: the *Remote radios* guide. This page is the reference for what those do.

## Finding servers

A server can announce itself on the local network (mDNS / DNS-SD, as `_pysdrlib._tcp`, with `--advertise`), so clients
find it without being told its address: `discover()` lists the servers announcing themselves. `connect` with no address
asks every server it finds which of its radios fit, without opening any, and connects to the best:

- Radios are compared across servers as one server compares its own (see *Saying who you are and what you need*): what fits `need`, nearest `want`, the
  antenna best for the frequency, one attached before one a client left open. Of equals, a server on this machine wins
  (samples come through shared memory).
- Every fitting radio of every server is put in one order, and `connect` opens exactly the radio it ranked (the open names
  it, `pick=`): if someone took it in the meantime, the server says `Busy` rather than opening its next best in its
  place, and the next radio in the order is tried, on whichever server it is. A radio that fails to open is passed over
  the same way; `err.NoDevice` says why each was left out.
- `discover_timeout=` (default 2 s) is how long it listens for servers. A server too old to be asked is tried last,
  as is one that can open the device type asked for but has none attached (`sim`, say).
- The announcement says only where a server is, its protocol and pysdrlib version, and whether it is a `server` or a
  `relay` (`pysdrlib relay --advertise`). What it has is asked of the server itself, so it is never out of date.
- A server bound to every interface (`0.0.0.0`) announces each of its addresses; `address` is one on a network the client
  is on, `addresses` all of them. A server on `127.0.0.1` or `ipc://` cannot be advertised (`--advertise` says so).
- Servers of another protocol version are left out (`--all-versions` / `all_versions=True` shows them).
- mDNS stays on the local network: a server across a router or a VPN still has to be given by address. Advertising is off
  unless asked for; it makes a server easier to find, not easier to use (see *Security*). `avahi-browse -rt _pysdrlib._tcp`
  sees the same announcements.

## Controllers and viewers

A hosted device has at most **one controller** and any number of **viewers**.

| | Controller | Viewer |
| --- | --- | --- |
| Receive samples | yes | yes |
| `get_*` calls | yes | yes |
| Change settings, start/stop, transmit | yes | no |

- Connecting to a device *type* (`pysdrlib.connect(addr, "hackrf")`) starts a new device and makes you its controller.
  `reuse=True` first takes over a hosted one nobody controls (a client that died without closing leaves its device open).
- Connecting to a hosted device *id* (`pysdrlib.connect(addr, "hackrf-0")`) attaches you as a viewer; `claim=True` also
  takes control if it is free. (`pysdrlib.device("remote", addr, ...)` is the same without choosing for you.)
  `sdr.claim()` takes control if nobody has it, and `sdr.release()` gives it back.
- `pysdrlib server --open hackrf` hosts a device at startup with no controller until a client claims it.
- A controller must keep talking to the server (the client sends heartbeats itself). One that is silent for `--lease`
  seconds (default 15) loses control: receiving and transmitting stop, the device stays open and can be claimed again.

## Saying who you are and what you need

A program can tell the server its name and let the server pick the radio, instead of knowing which radios it has:

- `program` is shown by `pysdrlib client ADDRESS list` (and in the server log) next to the radio you control, with the
  `need`/`want` you gave, so someone who finds a radio busy can see who has it. A viewer's or claimant's `program` is logged.
- `serial` and `label` pick a unit among the radios *attached* to the server (`list` shows them under "attached, not open"
  with their serials). With them, or with `need`/`want`, the driver may be left out.
- `need` and `want` are dicts of `freq` (Hz, or a `(low, high)` pair that must be tunable without a gap), `rate`,
  `bandwidth` (the analog filter, or the sample rate where there is none), `channels` and `tx`. A limit a driver does not
  declare is taken as met.
- Radios that do not meet `need` are never used; `err.NoDevice` names why each was left out. Of the rest, the one that
  meets most of `want` wins, and a radio nobody has open is preferred to taking over one a client left open and uncontrolled.
  Nothing is tuned for you: `granted` reports what it can do, then `initialize` as usual.
- A radio that runs *faster* than a `rate` you need is usable (decimate it yourself) but never preferred: the radio
  nearest above wins, and a faster one is picked only when nothing runs as fast. `granted["rate"]` is the rate it will really
  run at, so check it.
- `freq` is always the frequency over the air. If the operator says (`pysdrlib server --frontends FILE`, JSON or TOML, see
  `pysdrlib.remote.frontends`) that an LNB, converter or antenna sits on a radio, the server maps it to the radio's own
  tuning, `set_rx_freq`/`set_tx_freq` take and `get_*_freq` return the over-the-air frequency, and `granted` adds `if_freq`,
  `antenna` and `inverted` (`get_rx_inverted()`: the spectrum comes mirrored). Among radios that do equally well, the one
  whose antenna (`ANT500`: a 20-88 cm 50 ohm whip, best 75 MHz to 1 GHz; add your own with a `kind` such as `lpda` or `dish`, a
  `size` in metres and an `impedance`, all shown by `list`) is best at the wanted `freq` comes first; outside its best
  band it is only less preferred, not refused.
- Nothing stops a controller from tuning outside `need`/`want`: they are for choosing and for show, not limits.
- Command line: `rx --serial SN --program NAME`, or `rx --need rate=2e6 --freq 433.92e6 --rate 10e6` (with no type).

## Sample format on the wire

By default samples cross the network in the device's own layout (8-bit for a HackRF, ...), which is lossless and
several times smaller than `complex64`. The client always hands you `complex64`. Choose another format with
`--wire` / `--rx-wire` (server default or per `rx`), `--tx-wire` (for `tx`), or the `wire=` / `tx_wire=` arguments of
`RemoteDevice`:

`native` (default), `complex64` (`float32`), `int8`, `uint8`, `int12`, `int12p` (packed), `int16`

A narrower format than the device's own loses precision; one wider only costs bandwidth.

## Sharing a radio between programs on one machine

Run `pysdrlib server` next to the radio and let each program connect to it: one program controls the radio, any number of
others watch (see *Controllers and viewers*). A program on the server's machine takes the samples from **shared memory**
by default (`transport="auto"`), so the cost of serving one reader or five is almost the same:

- The server keeps one ring of `complex64` samples per streaming channel (a quarter second at the device's top rate) in
  shared memory and tells readers when it has written. Each reader has its own place in it: a reader that falls behind
  loses the samples it was too slow for, and sees that in `read(..., meta=True)` (`m.gaps`, `m.dropped`) like any other
  loss. It never slows the radio, the server, or another reader.
- A client starts on the position of the block it first hears of; the one that called `start_rx` reads from the
  stream's first sample. When the controller restarts the stream (`stop_rx`, `start_rx`) readers move to its new ring and
  indices start again from 0.
- A client on another machine, or one that cannot attach the segment, takes blocks over the socket as before, with no
  setting. `transport="zmq"` forces that; `transport="shm"` insists on shared memory (`err.NotSupported` if there is none).
  `pysdrlib server --no-shm` (or `Server(shm=False)`) doesn't offer it.
- The server only does the work of a way somebody is using: it writes the ring while a client reads from it, and encodes
  and sends blocks while one takes them over the socket.
- Rings live in `/dev/shm` (named `psdr-...`) and are removed when the stream is restarted, the device is closed or the
  server stops. If a very large ring would not fit in `/dev/shm` the server makes a smaller one, or none (then clients use
  the socket). A server killed outright can leave `psdr-*` files there; deleting them is safe when it is not running.

Measured with the simulated radio, with reader processes on the same stream (CPU as a share of one core):

| Stream | Readers | Socket (server + readers) | Shared memory (server + readers) |
| --- | --- | --- | --- |
| 20 MS/s | 1 | 25% | 21% |
| 20 MS/s | 4 | 66% | 48% |
| 61.44 MS/s | 1 | 35% | 29% |
| 61.44 MS/s | 4 | 94% | 62% |

The server's share is flat in the number of readers with shared memory (13% at 20 MS/s whether one reads or four) where
over the socket it grows. What each reader still costs is mostly the array `read()` allocates and fills; `read_into` with
your own buffer avoids the allocation.

## Relaying a radio on another machine to programs here

Several programs on a machine that is **not** the radio's would each pull their own copy of the stream across the network,
and a B210 at 61 MS/s in its native layout is already about 245 MB/s (2 Gb/s) for one. A relay is a server on *this*
machine that hosts the far device: it takes the stream once, and the programs here share it through shared memory.

- It hosts the device under the far device's own id, and the programs use it like any other hosted device: the same API,
  the same controller and viewer rules (those of the relay, among the programs on this machine).
- **Control passes through.** When the far device is free the relay takes control of it and local control calls reach it. If
  someone else controls it the relay only watches: programs here can receive but not change anything (`NotController`).
  `--control yes` insists (an error if it is taken), `--control no` always only watches. `--open TYPE` opens a new far
  device instead, which closing the relay closes; otherwise closing it gives control back and the far device carries on.
- A relay follows the far device: a stream someone else starts or stops there starts or stops here, and a restarted
  stream starts over for programs here too.
- The far samples travel in the far device's own layout (8-bit for a HackRF), as to any client, and are shared here as
  `complex64`. Transmitting works when the relay has control. The relay adds a hop: its server publishes in batches of about 2 ms, so expect a few milliseconds more than a direct connection (not measured).
- It does not reconnect: when the far server stops answering (about two timeouts) `pysdrlib relay` exits with an error,
  so a supervisor can restart it. From Python, `worker.lost` is set and `list` shows `lost`. `Server.relay(address, device)`
  does the same inside your own server.

What it saves is the network: the far server sends one stream however many programs listen here. On a machine with
no network between them (measured with the simulated radio, the far server in its own process) the CPU is about even:

| Stream | Readers | Directly: far server / all | Through a relay: far server / relay / all |
| --- | --- | --- | --- |
| 20 MS/s | 4 | 15% / 49% | 12% / 9% / 48% |
| 61.44 MS/s | 1 | 20% / 34% | 21% / 16% / 47% |
| 61.44 MS/s | 4 | 31% / 100% | 22% / 18% / 86% |

So a relay costs a little for one program, and pays for itself from two or three, and mostly in bandwidth.

## Monitoring

Each hosted device counts what passes through it. `pysdrlib client ADDRESS stats` shows it, and `Client.metrics()`
returns it (`--json` prints the same dict):

- `rx`: samples published, blocks and bytes sent on the socket (in the wire format), shared-memory notices, and how
  many clients subscribe over the socket and over shared memory.
- `device`: the radio's own stream counts while it receives: samples delivered, `dropped` (the server fell behind),
  `missing` (the radio didn't deliver at its rate), stalls.
- `tx`: samples and blocks transmitted, blocks `refused` (not from the controller, or not transmitting), and the
  device's transmit stats.
- `control`: calls answered and failed. A relay adds `upstream.dropped_blocks`: blocks lost between the far server and
  the relay.

So lost samples can be placed: `dropped_blocks` on a client (the network or a slow client), `device.dropped` (the
server's host), `device.missing` (the radio or its USB link). `pysdrlib server` and `pysdrlib relay` also log a line per
streaming device every `--stats-interval` seconds (default 60; 0 turns it off): the rate published, subscribers, and
what the device dropped or missed since the last line.

## Security

There is **no authentication or encryption**. The controller token only stops two clients from stepping on each other;
anyone who can reach the server can list devices, receive samples, and claim any uncontrolled device. The server binds
to `127.0.0.1` unless told otherwise. Use `--bind tcp://0.0.0.0:5555` only on a network you trust, or tunnel the ports
(SSH, a VPN).

## Layout

| File | Contents |
| --- | --- |
| `remote/server.py` | `Server` (the C2 socket: lifecycle and ownership) and `DeviceWorker` (one hosted device's sockets) |
| `remote/client.py` | `Client` (discovery) and `RemoteDevice` (a `Device` backed by a server) |
| `remote/server.py` also has | `RelayWorker`, a device of another server hosted here (`Server.relay`) |
| `pysdrlib.shm` | `ShmWriter` (the server's ring in shared memory) and `ShmReader` (a client's view of it), in `shm/ring.py`; the ring is C++ in `_native/include/hardware_shm.hpp` |
| `remote/discovery.py` | `Advertiser` (a server's mDNS announcement) and `discover` |
| `remote/protocol.py` | Wire format: message framing, sample block headers, wire formats. The file documents the sockets |
| `remote/cli.py` | The `pysdrlib` command |

Per hosted device the server runs a control socket (`ROUTER`), an RX stream (`PUB`) and a TX stream (`PUSH`);
see `remote/protocol.py` for the details. Tests are in `tests/test_remote.py`.
"""
try:
    import zmq  # noqa: F401
except ImportError as e:
    raise ImportError("pysdrlib's remote support needs pyzmq: pip install pysdrlib[remote]") from e

from .client import Client, RemoteDevice
from .server import Server
from .discovery import discover

Device = RemoteDevice
