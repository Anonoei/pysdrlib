# Remote radios

Host radios on one machine and use them from another, or share one radio between several programs on the same
machine (`pip install pysdrlib[remote]`). A remote device is a normal `Device`. The reference (ownership, need/want
matching, shared memory, relays, wire formats, monitoring counters, security) is the
[`pysdrlib.remote`](../api/remote.md) page.

!!! warning
    There is no authentication or encryption. Anyone who can reach a server can list its devices, receive samples
    and claim any uncontrolled device. The server binds to `127.0.0.1` unless told otherwise; bind it to a network
    only if you trust that network, or tunnel the ports (SSH, a VPN).

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

From Python (here with the server in the same process, on the simulated radio):

```python
--8<-- "examples/remote.py:server"
```

```python
--8<-- "examples/remote.py:connect"
```

```python
--8<-- "examples/remote.py:client"
```

The first client to open a device *type* controls it. Others attach to its *id* (`sim-0`, `hackrf-0`) as viewers:
they receive the same stream but can't change it. `claim=True` (or `sdr.claim()`) takes control of a device nobody
controls. A program on the server's machine takes samples through shared memory, so five readers cost little more than
one.

## Letting the server pick

```python
sdr = pysdrlib.connect(addr, program="pyspecan",
                       need={"freq": 433e6, "rate": 2e6},           # a radio that cannot do this is no use to me
                       want={"freq": 433.92e6, "rate": 10e6})       # and of those, the nearest to this is best
sdr.id, sdr.granted                                                  # which radio, and what it can do nearest to `want`

sdr = pysdrlib.connect(addr, "hackrf", serial="0000...c3", program="recorder")   # a specific unit
sdr = pysdrlib.connect(addr, label="RX-888", program="recorder")                 # a unit whose label contains this
```

`program` is shown next to the radio you control (`pysdrlib client ADDRESS list`), so someone who finds a radio busy can
see who has it.

## Finding servers

```
pysdrlib server --bind tcp://0.0.0.0:5555 --advertise              # announce over mDNS (--advertise-name NAME)
pysdrlib discover --list                                            # every server announcing itself, and what each has
```
```python
for s in pysdrlib.remote.discover(timeout=2):                       # [{"name", "address", "addresses", "kind", ...}]
    with Client(s["address"]) as c:
        print(s["name"], c.types(), c.list(), c.available())

sdr = pysdrlib.connect(need={"freq": 433e6, "rate": 2e6})           # no address: the best radio of every server found
sdr = pysdrlib.connect(device="hackrf")                             # a server with a HackRF attached
```

## Sharing a radio between programs on one machine

Run `pysdrlib server` next to the radio and let each program connect to it. One controls it; the others watch.

```python
sdr = pysdrlib.connect("ipc:///tmp/pysdrlib", "hackrf")                # the one that tunes and records
sdr.initialize(cf=100e6, Fs=10e6); sdr.start_rx()

view = pysdrlib.connect("ipc:///tmp/pysdrlib", sdr.id)                  # another program: a spectrum display, say
x = view.read(65536)                                                    # the same stream, at its own pace
```

## Relaying a radio on another machine

Several programs on a machine that is not the radio's would each pull their own copy of the stream across the
network. A relay takes it once and shares it locally:

```
pysdrlib relay tcp://radiohost:5555 hackrf-0                 # on this machine; then:
```
```python
sdr = pysdrlib.connect("tcp://127.0.0.1:5556", "hackrf-0", claim=True)    # the relay is at 127.0.0.1:5556 (--bind)
```

## Command line

```
pysdrlib server [--bind ENDPOINT] [--port-range LOW-HIGH] [--devices a,b] [--open TYPE[:KEY=VALUE,...]]... [--frontends FILE] [--wire FMT] [--lease S] [--no-shm] [--advertise [--advertise-name NAME]] [--stats-interval S] [-v]
pysdrlib relay ADDRESS (DEVICE | --open TYPE) [--bind ENDPOINT] [--control auto|yes|no] [--name ID] [--timeout S] [--lease S] [--no-shm] [--advertise [--advertise-name NAME]] [-v]
pysdrlib client ADDRESS [--timeout S] COMMAND
pysdrlib discover [--timeout S] [--list] [--all-versions]
```

| Client command | What it does |
| --- | --- |
| `list` | Device types the server allows and the devices it hosts |
| `stats [DEVICE] [--json]` | What each hosted device has published, to how many subscribers, and what was lost |
| `info DEVICE` | A hosted device's capabilities, as JSON |
| `rx TYPE -o FILE [-f FORMAT] [--sigmf] (-n SAMPLES \| -t SECONDS)` | Open a device (`--arg KEY=VALUE` for its `open()`), configure it (`--freq`, `--rate`, `--rx-gain`, `--set KEY=VALUE`) and record |
| `watch DEVICE -o FILE (-n \| -t)` | Record from an already hosted device without controlling it |
| `set DEVICE KEY=VALUE...` | Change settings on a hosted device (takes control briefly) |
| `tx TYPE -i FILE` | Open a device, configure it (`--freq`, `--rate`, `--tx-gain`, `--set`) and transmit; `--loop` repeats |

- Recordings are raw `complex64` (GNU Radio `.cfile`) unless `-f FORMAT` picks another (`ci16`, `ci8`, `cu8`...).
  Integer formats saturate at full scale.
- An output ending in `.sigmf`, or `--sigmf`, records SigMF, with the sample rate, the frequency, every retune and
  where samples were lost.
- Transmit files are raw `complex64`.
- `--arg KEY=VALUE` (client) and `--open TYPE:KEY=VALUE,...` (server) pass arguments to the device's `open()`: a
  recording to serve (`--open file:path=cap.sigmf,loop=true`), or a TCP stream's address
  (`--arg host=10.0.0.5 --arg sample_format=cu8`).
- `--port-range` limits the ports the per-device sockets use, which is what you open in a firewall.
- `python -m pysdrlib` is the same as the `pysdrlib` command.

Transmitting is a real RF emission: start with `--tx-gain 0`, into a load or an antenna you are licensed to use.

## Running a server from Python

```python
from pysdrlib.remote import Server

server = Server("tcp://0.0.0.0:5555", port_range=(6000, 6100), devices=["hackrf", "rtl_sdr"], lease=15)
server.serve_forever()          # or server.start() ... server.stop()
```
