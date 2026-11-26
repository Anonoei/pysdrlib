# Changelog

What changed in each release, newest first. Each release is tagged `vX.Y.Z` on GitHub, and the same version is on
PyPI.

## 0.5.1 (2026-11-26)

### Added

- **An air that devices transmit into and receive from** (`pysdrlib.air`): `Sim(transmit=True, air="lab")` (or
  `pysdrlib.open("sim", air="lab", transmit=True)`, and a server's `--open sim:air=lab,transmit=true`) transmits into
  it, and every `Sim(air="lab")` hears it, in this process or another on the machine (each transmit channel a
  shared-memory stream): shifted to the receiver's frequency, resampled to its rate, attenuated by the air's
  `loss_db`, then the receiver's gain, noise and ADC. Realtime sims are on the wall clock (transmitting is paced, a
  pause is silence); others count their own samples, so one process is exact and repeatable. Anything can attach
  channels without a sim (`air.tx_channel()`, `air.rx_channel()`). With an air, `transmitted` is not kept
  (`get_tx_stats()` counts what was written).
- A transmitting sim has a transmit gain stage, "PA" (0-47 dB, full scale at 47): `set_tx_gain`, `set_tx_gain_db` and
  `set_tx_pa_gain` scale what it transmits.
- `pysdrlib.find(virtual=True)` lists a simulated radio in each air with a transmitter in it (a transmitting sim from
  its first `write`, not from `open`). An air's transmit channels are `shm` streams that `find()` lists only with
  `virtual=True`.
- `pysdrlib.shm.streams(prefix=)` lists only the streams whose name starts with it.
- **Recordings say how the radio received.** `Device.get_rx_context()` gives a channel's `RxContext`: the frequency over the
  air and the rate, the `caps.Chain` in front of the radio (its LO, and `inverted` when the LO is above the signal and
  the spectrum arrives mirrored), the reference correction (`set_freq_correction`) and clock source, gains, antenna port
  and radio. `FileSink(..., context=)` keeps it in a SigMF recording as `pysdrlib:rx` (an optional `core:extensions`
  namespace), `sigmf.read(...).context` and `File(...).meta.context` give it back, a replayed recording reports it
  (`open("file", ...).get_rx_context()`), `pysdrlib client ... rx/watch -o x.sigmf` writes it (a viewer can ask too),
  and `pysdrlib play --info` prints it. A remote radio on a server older than 0.5.1 raises `NotSupported` saying so
  (`client ... -o x.sigmf` too; a raw recording doesn't ask). Any call to a method the server's pysdrlib doesn't have
  raises `NotSupported` naming the method and the server's version, before it is sent.
- **How well the receiver is known travels with it.** `set_freq_correction(ppm, accuracy=)` (a standard error, ppm;
  `get_freq_correction_accuracy`) and `caps.Chain(lo_accuracy=)` (Hz; `lo_accuracy` in a frontends file: a measured LO's)
  are in `RxContext` (`ppm_accuracy`, `chain.lo_accuracy`), so a reader can say what a frequency read off the samples is
  worth, and `play --info` shows them.
- **Levels in dBm.** `Device.set_level_calibration(offset_db, accuracy=, span=)`: dBm at the antenna port = dBFS (full
  scale |x| = 1 in the complex64 samples) + `offset_db`, made at the gains and frequency current then.
  `RxContext.level_offset` / `level_accuracy` (dB) carry it while the gains (and gain mode) are unchanged and the
  frequency is within `span` (default: the sample rate when it was set); otherwise both are None.
  `get_level_calibration()` says what was set and whether it still `holds`. It crosses the wire, and `play --info`
  shows it.
- **A chain names its feed and channel.** `caps.Chain(polarization="H" | "V" | "RHCP" | "LHCP")` says what the feed takes
  in, and `caps.Chain(channel=)` puts a chain on one channel of a radio whose channels' ports share a name (a B210's RX2
  on channels 0 and 1, a dual-feed LNB's two outputs). Both are keys of a frontends file too, and `get_rx_context(channel=k)`
  gives each channel's chain. Recordings written by 0.5.1 mark `pysdrlib:rx` as version 1.2.0; whatever a
  recording doesn't have reads as None.
- `Device.set_chains([caps.Chain(lo=5.15e9, invert=True)])` puts an LNB or converter in front of a radio on this
  machine: frequencies are over the air from then on (the radio stays where it is tuned, and `get_rx_freq` says what
  that is through the new chain). A remote radio's chains are its server's frontends file: `set_chains` on one raises
  `NotSupported`.
- `pysdrlib.open("sim", realtime=False)` works: `realtime` is taken by `open` as well as the constructor, as `air` and
  `transmit` are (and a server's `--open sim:realtime=false`).

### Fixes

- `set_rx_gain(fraction)` / `set_tx_gain(fraction)` no longer warn "... gain must be a multiple of N": a fraction
  nearly always falls between a stage's steps, so rounding it to one is expected (and with `strict = True` it no longer
  raises). `set_rx_gain_db` still warns when the decibels asked for can't be set exactly.
- A replayed recording's `get_rx_freq()` is the recording's frequency from the start (it was the default, 100 MHz,
  until playback began).
- `file` transmitting in real time paces with `time.perf_counter()`. On Windows before Python 3.13, `time.monotonic()`
  ticks every ~15.6 ms, so `write` could leave more queued than `TX_BUFFER_SECONDS` allows.

## 0.5.0 (2026-11-22)

### Breaking

- **pysdrlib is now licensed GPL-3.0-or-later** (it was MIT). Releases up to 0.4.6 stay MIT.
- **File format names count the bits of one value (I or Q), as SigMF does, not of the whole sample.** `cf32` is now
  two float32 (numpy `complex64`, a GNU Radio `.cfile`; it was `cf64`). `cf64` was `cf128`, `cf16` was `cf32`, and
  `ci16t` was `ci32t` (likewise for ci4t, ci8t and ci32t). Two's complement is the default (`ci8` is `ci8t`). Code
  that names `cf32` or `cf64` will misread files *without an error*, so check every caller.
- `TcpIq(sample_format=...)` in the constructor is gone. Pass it to `open()` instead:
  `pysdrlib.open("tcpiq", host=..., sample_format="ci16")`.
- Remote: protocol version 4 only. A server refuses a client of another version and says which side to update. Run
  the same pysdrlib version on both ends.
- `Overflow` and `Underflow` moved to `pysdrlib.err` (`pysdrlib.file.err` keeps the names).
- The native extensions lost their Python receive-callback path (`set_rx_callback`, `read_rx_indexed`,
  `rx_available`). This only affects drivers written outside pysdrlib.

### Radios

- **bladeRF 2.0 micro** (`bladerf`): 2 RX and 2 TX channels. Receive is tested on a 2.0 micro xA4 and is lossless
  from 0.52 to 40 MS/s. Transmit is so far checked for plumbing only.
- **tcpiq**:
  - Listening mode (`listen=True`) waits for a client to reconnect (`reconnect_timeout`, default 10 s). A client that
    reconnects takes over the stream, and keepalive drops a dead client in about 5 s.
  - Skips rtl_tcp's 12-byte header (`sdr.rtl_tcp` reports the tuner).
  - Measures the arrival rate (`sdr.measured_rate`, and `sdr.rate_mismatch` against `RATE_TOLERANCE`) and warns (`warn.RateMismatch`) when it disagrees with the
    declared `Fs`.
  - Accepts 4-bit packed (`ci4`) and half-float (`cf16`) streams. `pysdrlib.tcpiq.FORMATS` lists every stream format.
    The format can also be a `Formats` member (`Formats.ci16t`), as File and FileSink take.
  - Connected mode: when the server closes the stream, or stops answering (keepalive notices in about 5 s), a read
    raises `err.Lost` once what arrived has been read. Before, it waited until its timeout.
- **sim** runs on the same native streaming path as the hardware drivers. Its signal is built from tones, noise and
  QPSK bursts, set in `open()` or `configure()`. It can scale with gain and clip like an ADC, and can delay a retune,
  drop samples, stall or disconnect on demand. Each channel has its own gain and strength (`channel_db`).

### Receiving

- A change at a block's first sample (or in a gap just before it) is an `Event` at offset 0 in that block's
  `RxMeta.events`; before, only `RxMeta.freq`/`rate` showed it. After a flush there is still none.
- `err.Lost` raised by `read` carries what that read had received: `.samples` and `.meta`.
- `read(latest=True)` skips what is queued except the newest block, and anything from before the last setting
  change: what a display wants.
- `err.Lost` is raised when the radio is gone: unplugged, or a remote server that no longer hosts it.
- `on_drop` can be set for a single `read`, `stream` or `sweep`. `on_drop="raise"` raises `err.Dropped` carrying the
  block, and `sweep(on_drop="retry")` captures that step again.
- `RxMeta.hw_time` is the radio's own clock at a block's first sample. It is carried to remote clients and relays, and
  into SigMF recordings.
- A retune, rate, gain or bandwidth change is marked at the stream position from before the driver's call, so samples
  that arrive during a slow retune report the new setting.
- `Device.sweep(start, stop, step, count)` takes a list of frequencies, `offset=` (tune off centre, which moves the DC
  spike out of the way) and `skip=`.
- Every device has `is_controller` (always true locally), `id` and `granted` (None locally), so code that handles
  local and remote devices alike doesn't need `getattr`.
- `RxMeta.index_at(k)` (the stream index of a block's sample `k`), `RxMeta.next_index` (the stream index just after a block), `RxMeta.follows(prev)` (it starts where `prev`
  ended, nothing lost or discarded between) and `RxMeta.wall_time` (the radio's clock when it holds a date, else the
  receive time on the wall clock). `index` is documented as the absolute position across gaps.
- `rx_antenna_info()` / `tx_antenna_info()`: each antenna port with its chain, the antenna fitted, whether the
  spectrum comes mirrored there, and whether it is selected.
- `find_gain()` finds and sets the highest receive gain that doesn't clip, for one channel or several, local or remote.
- Gain splitting is cached: a repeated HackRF `set_rx_gain` takes 5 µs instead of 723 µs.
- The native ring copies samples out with its lock released, so a slow reader no longer holds up the driver's thread.

### Shared-memory streams

- **`shm` driver and `pysdrlib.shm.ShmStream`**: a process publishes samples under a name (`ShmStream("rpps.ch3", Fs=,
  cf=)`, then `publish(x, first)`), and any other on the machine receives them like a radio
  (`pysdrlib.open("shm@rpps.ch3")`, listed by `find()`). RxMeta carries the publisher's gaps, its wall-clock time
  (`hw_time`), and a freq or rate event when it retunes; a closed stream raises `err.Lost`.
- The ring under it (`ShmWriter`, `ShmReader`, `fit_capacity`) moved from `pysdrlib.remote.shm` to `pysdrlib.shm`, and
  needs no pyzmq.

### Files

- SigMF: `FileSink` writes `.sigmf-data` and `.sigmf-meta` files, with a capture per retune and an annotation where
  samples were lost. `File` reads the format, rate and frequency back.
- `pysdrlib.open("file", path=...)` plays a recording as a radio (`loop=`, `realtime=`, seek). `tx_path=` records
  what is transmitted. A server can host either.
- Big-endian formats (`ci16_be`, `cf32_be`...), 4-bit packed and half-float formats, and `FileSink(scale=True)`.
- `File.is_open`. A file whose format nothing names raises `err.UnknownFormat` (a `ValueError`), as do an
  unsupported SigMF datatype and an unknown stream format name.
- The format is taken from the file extension (`.cf32`, `.cu8`, `.cfile`...) when none is given.

### Finding and opening radios

- `pysdrlib.discover()` lists radios attached here and on servers (`PYSDRLIB_SERVERS`, or zeroconf). Each comes with
  the target string `open()` takes: `[server/]driver[@serial]`. A malformed target raises `ValueError` up front.
  `Radio.receiving` says whether a hosted radio is streaming.
- `err.NoDevice.reason` says why a server had no radio for a request: `"none_attached"`, `"none_free"`, `"unmet"` or
  `"no_type"` (it travels from the server to the client).

### Command line and remote

- `pysdrlib play REC` describes a recording and shows its power as it plays (`--serve` hosts it like a radio).
- `pysdrlib client ... rx|watch -o FILE` records in any format, or SigMF.
- `server --open TYPE:KEY=VALUE` and `client --arg KEY=VALUE` pass arguments to a device's `open()`.
- `format_antenna` moved from `pysdrlib.remote.cli` to `pysdrlib.base.antennas`, which needs no remote extra.
- Server metrics: `Client.metrics()`, `pysdrlib client ADDRESS stats`, and a periodic log line per streaming device.
- A viewer whose state is older than the server's (it attached before the controller started receiving) has `read`
  ask the server once instead of raising `err.NotStreaming`.
- `RemoteDevice` forwards every public `Device` method. This fixes `split_rx_gain_db` running on the client.

### Fixes

- Building from a source checkout works again (`pip install -e .`, sdist to wheel, cibuildwheel). It was broken since
  hardware timestamps landed.
- A recording played with `realtime=False` could lose whole blocks while a read was copying out of the queue: the
  ring now reports what is being copied (`rx_stats()["reading"]`), and playback waits for that as well.
- `with pysdrlib.open(...) as sdr` no longer warns that the device is already open.
- pysdrlib's warnings point at the caller's line, not at pysdrlib's own code.

### Docs and tooling

- Docs moved to Zensical + mkdocstrings: the reference is the docstrings, and the how-to guides embed the tested
  examples. `AGENTS.md`, `llms.txt` and `llms-full.txt` are for coding agents.
- `tools/builder.py` matches RPPS's and pyspecan's. CI runs on Linux and Windows with Python 3.12, 3.13 and 3.14.
  Releases publish from a GitHub release tagged `vX.Y.Z`.

## 0.4.6 and earlier

See the [tags on GitHub](https://github.com/Anonoei/pysdrlib/tags).
