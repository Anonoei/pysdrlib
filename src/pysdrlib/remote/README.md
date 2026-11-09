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

sdr = pysdrlib.device("remote", "tcp://host:5555", "hackrf")   # open a new hackrf on the server and control it
sdr.open()
sdr.initialize(cf=100e6, Fs=10e6)
sdr.start_rx()
samples = sdr.read(65536)                                      # complex64, like any device
sdr.close()                                                    # also closes the hackrf on the server
```
`sdr.caps` comes from the hosted device, and every control (`set_gain`, `set_bandwidth`, `set_setting`, a driver's own
setters such as `set_rx_lna_gain`...) is forwarded to it. Channels, transmit, `read(meta=True)` gaps and `stream()` work
as they do locally.

## Controllers and viewers

A hosted device has at most **one controller** and any number of **viewers**.

| | Controller | Viewer |
| --- | --- | --- |
| Receive samples | yes | yes |
| `get_*` calls | yes | yes |
| Change settings, start/stop, transmit | yes | no |

- Opening a device *type* (`pysdrlib.device("remote", addr, "hackrf")`) starts a new device and makes you its controller.
- Opening a hosted device *id* (`pysdrlib.device("remote", addr, id="hackrf-0")`) attaches you as a viewer.
  `sdr.claim()` takes control if nobody has it, and `sdr.release()` gives it back.
- `pysdrlib server --open hackrf` hosts a device at startup with no controller until a client claims it.
- A controller must keep talking to the server (the client sends heartbeats itself). One that is silent for `--lease`
  seconds (default 15) loses control: receiving and transmitting stop, the device stays open and can be claimed again.

## Sample format on the wire

By default samples cross the network in the device's own layout (8-bit for a HackRF, ...), which is lossless and
several times smaller than `complex64`. The client always hands you `complex64`. Choose another format with
`--wire` / `--rx-wire` (server default or per `rx`), `--tx-wire` (for `tx`), or the `wire=` / `tx_wire=` arguments of
`RemoteDevice`:

`native` (default), `complex64` (`float32`), `int8`, `uint8`, `int12`, `int12p` (packed), `int16`

A narrower format than the device's own loses precision; one wider only costs bandwidth.

## Command line

```
pysdrlib server [--bind ENDPOINT] [--port-range LOW-HIGH] [--devices a,b] [--open TYPE]... [--wire FMT] [--lease S] [-v]
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
| `server.py` | `Server` (the C2 socket: lifecycle and ownership) and `DeviceWorker` (one hosted device's sockets) |
| `client.py` | `Client` (discovery) and `RemoteDevice` (a `Device` backed by a server) |
| `protocol.py` | Wire format: message framing, sample block headers, wire formats. The file documents the sockets |
| `cli.py` | The `pysdrlib` command |

Per hosted device the server runs a control socket (`ROUTER`), an RX stream (`PUB`) and a TX stream (`PUSH`);
see `protocol.py` for the details. Tests are in `tests/test_remote.py`.
