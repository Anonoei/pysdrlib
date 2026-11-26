"""Streams in shared memory: one process publishes samples under a name, any other on the machine receives them like a radio

    from pysdrlib.shm import ShmStream
    with ShmStream("rpps.ch3", Fs=250e3, cf=433.92e6, bw=200e3) as out:    # the publisher
        out.publish(x, first)                                             # complex64, at stream index `first`

    sdr = pysdrlib.open("shm@rpps.ch3")                                   # any other process
    sdr.initialize(); x, m = sdr.read(65536, meta=True)

`pysdrlib.find()` lists the streams published (`shm` is a driver like any other, with nothing to build). A program that
reads radios through pysdrlib reads these the same way: the publisher's rate and frequency, `RxMeta.index` and `gaps`
across what the publisher lost, `hw_time`/`wall_time` from the publisher's clock, and a "freq" or "rate" `Event` when it
retunes. A `pysdrlib server` shares a radio with its local clients over the same ring (`ring`), with the ZeroMQ socket
in place of the manifest.

## Publishing

| `ShmStream(...)` | |
| --- | --- |
| `name` | Letters, digits, `_`, `.` and `-`; a `.` makes a namespace ("rpps.ch3"). Not `/` or `@`: the name is the `@` part of a target string. Raises FileExistsError while a running process publishes it |
| `Fs`, `cf` | Sample rate and centre frequency of what is published |
| `capacity` | Samples the ring holds: default one second, at least 2^18, held to what `/dev/shm` has room for (OSError when not even that fits) |
| `**info` | Anything else for readers, as JSON values (`bw`, `label`...): the manifest's "info", `DeviceInfo.extra["info"]`, `sdr.manifest["info"]` |
| `publish(x, first, wall_time=None)` | Append complex64 samples whose first has stream index `first`: at or after `head`, later leaves a gap. `wall_time` is `time.time()` of `x[0]`, where the source knows it (`RxMeta.wall_time`); the first publish of a generation anchors readers' timestamps there (default: now) |
| `retune(Fs=, cf=, capacity=, **info)` | A new generation on a new ring, its indices starting over. Readers finish the old one, then follow |
| `close()` | Readers finish what is there, then their `read` raises `err.Lost`. Also on `with` exit, and when the object is collected |

## Receiving

- `pysdrlib.open("shm@NAME")` (the end of a name will do, if only one stream has it), `pysdrlib.open("shm", name=...)`,
  or `pysdrlib.open("shm")` when only one stream is published. `directory=` reads another directory.
- The rate and frequency are the publisher's: setting them only clamps to them (raises with `sdr.strict`). There is no
  gain. `sdr.manifest` is the manifest of the generation being received.
- Receiving starts with what is published next. A reader that falls a whole ring behind loses the oldest samples, a gap
  in `RxMeta` like a radio's overflow; the publisher never waits for a reader.
- The stream ends (`err.Lost` after what is there is read) when the publisher closes it, or its process is gone.

## The manifest

`<directory>/<name>.json`, replaced at once (never half written). The directory is `$PYSDRLIB_SHM_DIR`, else
`pysdrlib-shm` in the temporary directory. `stream.streams()` lists the manifests of running publishers (`prefix=`: only names starting with it).

| Field | |
| --- | --- |
| `format` | 1. A reader skips a manifest of a format it doesn't know |
| `name`, `segment`, `gen`, `capacity` | The stream's name; the ring's shared-memory segment (a name, as `SharedMemory(name=)` takes); the generation (0, then one more for each retune, also the ring's `stream_id`); the ring's samples |
| `Fs`, `cf` | Sample rate and centre frequency of the generation |
| `pid` | The publisher's process: a manifest whose process is gone is left out |
| `wall_time`, `wall_index` | `time.time()` of the ring sample `wall_index` (the generation's first published), or null before the first publish |
| `anchors` | `{segment: [wall_time, wall_index]}` of the last 8 generations, for a reader still on one when the publisher retuned |
| `info` | The publisher's own fields |

A retune writes the new manifest, then closes the old ring and removes it; `close` closes the ring, removes the manifest,
then the ring. A reader that sees its ring closed reads the manifest: a new segment is the next generation, none (or the
same) is the end.

The ring's own rules (one writer, complex64, polling, gaps) are in `ring`. Tests are in `tests/test_shm_stream.py` (the ring alone: `tests/test_shm.py`).
"""
from .ring import MAX_CAPACITY, MIN_CAPACITY, ShmReader, ShmWriter, fit_capacity
from .stream import ShmStream, streams
from .device import ShmDevice

Device = ShmDevice
find = ShmDevice.find
