# Remote

Host SDRs on one machine and use them from another over [ZeroMQ](https://zeromq.org/). A remote device is a normal
`pysdrlib.Device`, so code written for a local radio works unchanged.

```
pip install pysdrlib[remote]        # adds pyzmq
```

## Quick start

On the machine with the radios:
```
pysdrlib server --bind tcp://0.0.0.0:5555
```
From anywhere that can reach it:
```
pysdrlib client tcp://host:5555 list
pysdrlib client tcp://host:5555 rx hackrf --freq 100e6 --rate 10e6 -t 5 -o out.cfile
```
```python
import pysdrlib

sdr = pysdrlib.connect("tcp://host:5555", "hackrf")            # open a new hackrf on the server and control it
sdr.initialize(cf=100e6, Fs=10e6)
sdr.start_rx()
samples = sdr.read(65536)                                      # complex64, like any device
sdr.close()                                                    # also closes the hackrf on the server
```
`sdr.caps` comes from the hosted device, and every control (`set_rx_gain`, `set_bandwidth`, `set_setting`, a driver's own
setters such as `set_rx_lna_gain`...) is forwarded to it. Channels, transmit, `read(meta=True)` gaps and `stream()` work
as they do locally.

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

```python
sdr = pysdrlib.connect(addr, program="pyspecan",
                       need={"freq": 433e6, "rate": 2e6},           # a radio that cannot do this is no use to me
                       want={"freq": 433.92e6, "rate": 10e6})       # and of those, the nearest to this is best
sdr.id, sdr.granted                                                  # which radio, and what it can do nearest to `want`

sdr = pysdrlib.connect(addr, "hackrf", serial="0000...c3", program="recorder")   # a specific unit
sdr = pysdrlib.connect(addr, label="RX-888", program="recorder")                 # a unit whose label contains this
```

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
  [frontends.py](frontends.py)) that an LNB, converter or antenna sits on a radio, the server maps it to the radio's own
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

```python
sdr = pysdrlib.connect("ipc:///tmp/pysdrlib", "hackrf")                # the one that tunes and records
sdr.initialize(cf=100e6, Fs=10e6); sdr.start_rx()

view = pysdrlib.connect("ipc:///tmp/pysdrlib", sdr.id)                  # another program: a spectrum display, say
x = view.read(65536)                                                    # the same stream, at its own pace
```

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

```
pysdrlib relay tcp://radiohost:5555 hackrf-0                 # then, on this machine:
```
```python
sdr = pysdrlib.connect("tcp://127.0.0.1:5556", "hackrf-0", claim=True)    # the relay is at 127.0.0.1:5556 (--bind)
sdr.initialize(cf=100e6, Fs=10e6); sdr.start_rx(); x = sdr.read(65536)
```

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

## Command line

```
pysdrlib server [--bind ENDPOINT] [--port-range LOW-HIGH] [--devices a,b] [--open TYPE]... [--frontends FILE] [--wire FMT] [--lease S] [--no-shm] [-v]
pysdrlib relay ADDRESS (DEVICE | --open TYPE) [--bind ENDPOINT] [--control auto|yes|no] [--name ID] [--timeout S] [--lease S] [--no-shm] [-v]
pysdrlib client ADDRESS [--timeout S] COMMAND
```

| Client command | What it does |
| --- | --- |
| `list` | Device types the server allows and the devices it hosts |
| `info DEVICE` | A hosted device's capabilities, as JSON |
| `rx TYPE -o FILE (-n SAMPLES \| -t SECONDS)` | Open a device, configure it (`--freq`, `--rate`, `--rx-gain`, `--set KEY=VALUE`) and record |
| `watch DEVICE -o FILE (-n \| -t)` | Record from an already hosted device without controlling it |
| `set DEVICE KEY=VALUE...` | Change settings on a hosted device (takes control briefly) |
| `tx TYPE -i FILE` | Open a device, configure it (`--freq`, `--rate`, `--tx-gain`, `--set`) and transmit; `--loop` repeats |

Recordings and transmit files are raw `complex64` (GNU Radio `.cfile`). Numbers accept scientific notation (`100e6`).
`--port-range` limits the ports the per-device sockets use, which is what you open in a firewall. `python -m pysdrlib`
is the same as the `pysdrlib` command.

Transmitting is a real RF emission: start with `--tx-gain 0`, into a load or an antenna you are licensed to use.

## Security

There is **no authentication or encryption**. The controller token only stops two clients from stepping on each other;
anyone who can reach the server can list devices, receive samples, and claim any uncontrolled device. The server binds
to `127.0.0.1` unless told otherwise. Use `--bind tcp://0.0.0.0:5555` only on a network you trust, or tunnel the ports
(SSH, a VPN).

## Running a server from Python

```python
from pysdrlib.remote import Server

server = Server("tcp://0.0.0.0:5555", port_range=(6000, 6100), devices=["hackrf", "rtl_sdr"], lease=15)
server.serve_forever()          # or server.start() ... server.stop()
```

## Layout

| File | Contents |
| --- | --- |
| `remote/server.py` | `Server` (the C2 socket: lifecycle and ownership) and `DeviceWorker` (one hosted device's sockets) |
| `remote/client.py` | `Client` (discovery) and `RemoteDevice` (a `Device` backed by a server) |
| `remote/server.py` also has | `RelayWorker`, a device of another server hosted here (`Server.relay`) |
| `remote/shm.py` | `ShmWriter` (the server's ring in shared memory) and `ShmReader` (a client's view of it); the ring is C++ in `_native/include/hardware_shm.hpp` |
| `remote/protocol.py` | Wire format: message framing, sample block headers, wire formats. The file documents the sockets |
| `remote/cli.py` | The `pysdrlib` command |

Per hosted device the server runs a control socket (`ROUTER`), an RX stream (`PUB`) and a TX stream (`PUSH`);
see `remote/protocol.py` for the details. Tests are in `tests/test_remote.py`.
