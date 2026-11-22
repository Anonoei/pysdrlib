# Changelog

What changed in each release, newest first. Each release is tagged `vX.Y.Z` on GitHub, and the same version is on
PyPI.

## 0.5.0 (unreleased)

### Breaking

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
  - Measures the arrival rate (`sdr.measured_rate`) and warns (`warn.RateMismatch`) when it disagrees with the
    declared `Fs`.
  - Accepts 4-bit packed (`ci4`) and half-float (`cf16`) streams. `pysdrlib.tcpiq.FORMATS` lists every stream format.
- **sim** runs on the same native streaming path as the hardware drivers. Its signal is built from tones, noise and
  QPSK bursts, set in `open()` or `configure()`. It can scale with gain and clip like an ADC, and can delay a retune,
  drop samples, stall or disconnect on demand. Each channel has its own gain and strength (`channel_db`).

### Receiving

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
- `find_gain()` finds and sets the highest receive gain that doesn't clip, for one channel or several, local or remote.
- Gain splitting is cached: a repeated HackRF `set_rx_gain` takes 5 µs instead of 723 µs.
- The native ring copies samples out with its lock released, so a slow reader no longer holds up the driver's thread.

### Files

- SigMF: `FileSink` writes `.sigmf-data` and `.sigmf-meta` files, with a capture per retune and an annotation where
  samples were lost. `File` reads the format, rate and frequency back.
- `pysdrlib.open("file", path=...)` plays a recording as a radio (`loop=`, `realtime=`, seek). `tx_path=` records
  what is transmitted. A server can host either.
- Big-endian formats (`ci16_be`, `cf32_be`...), 4-bit packed and half-float formats, and `FileSink(scale=True)`.
- The format is taken from the file extension (`.cf32`, `.cu8`, `.cfile`...) when none is given.

### Finding and opening radios

- `pysdrlib.discover()` lists radios attached here and on servers (`PYSDRLIB_SERVERS`, or zeroconf). Each comes with
  the target string `open()` takes: `[server/]driver[@serial]`. A malformed target raises `ValueError` up front.

### Command line and remote

- `pysdrlib play REC` describes a recording and shows its power as it plays (`--serve` hosts it like a radio).
- `pysdrlib client ... rx|watch -o FILE` records in any format, or SigMF.
- `server --open TYPE:KEY=VALUE` and `client --arg KEY=VALUE` pass arguments to a device's `open()`.
- Server metrics: `Client.metrics()`, `pysdrlib client ADDRESS stats`, and a periodic log line per streaming device.
- `RemoteDevice` forwards every public `Device` method. This fixes `split_rx_gain_db` running on the client.

### Fixes

- Building from a source checkout works again (`pip install -e .`, sdist to wheel, cibuildwheel). It was broken since
  hardware timestamps landed.
- `with pysdrlib.open(...) as sdr` no longer warns that the device is already open.
- pysdrlib's warnings point at the caller's line, not at pysdrlib's own code.

### Docs and tooling

- Docs moved to Zensical + mkdocstrings: the reference is the docstrings, and the how-to guides embed the tested
  examples. `AGENTS.md`, `llms.txt` and `llms-full.txt` are for coding agents.
- `tools/builder.py` matches RPPS's and pyspecan's. CI runs on Linux and Windows with Python 3.12, 3.13 and 3.14.
  Releases publish from a GitHub release tagged `vX.Y.Z`.

## 0.4.6 and earlier

See the [tags on GitHub](https://github.com/Anonoei/pysdrlib/tags).
