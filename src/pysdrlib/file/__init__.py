"""Sample files: read them like a device, write them from one, play a recording back as a radio.

Both directions use the formats in `pysdrlib.Formats` (`pysdrlib.Formats.ls()` lists them; reference: `pysdrlib.base.formats`).
The walkthrough, with code, is the *Sample files* guide.

| Class | |
| --- | --- |
| `File(path, fmt)` | Reads samples from a file, forward or back, in blocks |
| `FileSink(path, fmt)` | Writes samples to a file |
| `pysdrlib.open("file", path=..., tx_path=...)` | Plays a recording back as a receiving device, and/or records what it transmits (`Replay`) |

Any of them can be a [SigMF](https://sigmf.org) recording: `name.sigmf-data` with `name.sigmf-meta` beside it.

Format names follow SigMF: the number is the bits of one value (I or Q), so a GNU Radio `.cfile` (two float32) is
`cf32`, an RTL-SDR `.cu8` is `cui8`, and `ci16` is two int16. Before pysdrlib 0.5 the number was the bits of the whole
sample (`cf64` was two float32): check old code.

`File` has `get_samples()`, so code that processes a device's samples can be pointed at a recording (for the whole
`Device` interface, replay it). Neither class scales: integer formats are read and written as stored, not divided by
full scale.

## Reading

| Call | |
| --- | --- |
| `next(count, partial=False)` | The next `count` samples. Past the end it raises `err.Overflow`, unless `partial=True`, which returns what is left (an empty array at EOF) |
| `forward(count, partial=False)` | Yield blocks until EOF. `File(...)(count)` is the same |
| `prev(count)`, `reverse(count)` | Step back, returning the samples just passed (`reverse` yields block after block to the start) |
| `seek(sample)`, `reset()` | Move to a sample index / the start. `seek` outside the file raises `err.Overflow` |
| `percent(fraction=True)` | How much of the file has been read |
| `get_samples()` | `next(count)` with `count` from `set_sample_count` (default 8192) |

Positions and lengths are in **samples**, not bytes (`f.cur_samp`, `f.max_samp`). A trailing partial sample in the
file is ignored.

## Writing

- `FileSink(path, fmt, append=False, open=False, clip=False)` truncates an existing file unless `append=True`. The format
  of an existing file is **not** checked when appending.
- `write()` takes 1-D samples (other shapes raise `ValueError`: flatten, or write each channel to its own file) and needs
  the sink open (`open()`, `open=True` or `with`).
- A value outside the format's range (including NaN or infinity in an integer format) raises `ValueError`; nothing
  wraps. With `clip=True` values saturate instead (each I/Q component on its own), NaN is stored as 0, and one warning,
  "N samples clipped", is logged when the sink closes. `FileSink.clipped` holds the running count.

## SigMF

- A path ending in `.sigmf` (or `.sigmf-data`/`.sigmf-meta`) makes `FileSink` write SigMF; `sigmf=True` does it for any
  name. The `.sigmf-meta` file is written when the sink opens, and brought up to date by `flush()` and `close()`.
- `write(x, meta=m)` with the `RxMeta` of `sdr.read(..., meta=True)` records the stream as the device saw it: the
  sample rate and frequency (when not given), a capture at every retune, and an annotation wherever samples were lost or
  skipped by a flush.
- From a radio whose clock holds a date (UHD set from GPS or `set_time_now`, a replay), `core:datetime` is the radio's
  time of the first sample (`RxMeta.hw_time`), not the computer's when the file was opened.
- `File` takes the format from the metadata (a `fmt` given wins), and has `rate`, `freq` and `meta` (a `sigmf.Meta`).
- Datatypes: every format with a SigMF name (`Formats.cf32.sigmf == "cf32_le"`; `Formats.get("cu8")` works too).
  Not supported: big-endian, multi-channel recordings, and formats SigMF has no name for (4-bit, one's complement,
  float16). Appending to a SigMF recording isn't supported.

## Replay

- Samples are delivered in blocks on a thread, paced to the sample rate; `realtime=False` delivers them as fast as they
  are read (nothing is dropped: playback waits for the reader).
- The frequency follows the recording: each SigMF capture is a retune, an `Event` in `RxMeta`. `set_rx_freq` only
  relabels the stream, and the sample rate is fixed at the recording's.
- At the end it starts again with `loop=True`; otherwise delivery stops, `sdr.eof` is True, and `read` times out.
- `sdr.seek(sample)`, `sdr.position` and `sdr.length` move around the recording.
- `RxMeta.hw_time` is when the recording took each sample (from the SigMF `core:datetime`), across loops and seeks.
- A server hosts it like any radio: `pysdrlib.device("remote", address, "file").open(path=..., loop=True)`, or
  `pysdrlib server --open file:path=cap.sigmf,loop=true`.

## Transmitting to a file

- What is written goes to `tx_path` in `tx_format` (default `cf32`; SigMF for a `.sigmf` name), scaled to the format's
  full scale and saturated there.
- With `realtime` (the default) it goes out at the transmit rate like a radio's: `write` blocks while more than
  `TX_BUFFER_SECONDS` is queued ahead of the clock, and `get_tx_stats()` counts `sent`, `pending` and `underruns` (a
  write that came after everything had gone out). `realtime=False` takes it as fast as it is written.
- With `path` too, it plays a recording and records what is transmitted at once (a loopback for testing); without, it
  only transmits.

## Errors

`pysdrlib.file.err` defines `Overflow` (asking for samples past either end of the file) and `Underflow` (the file had
fewer samples than its size promised, e.g. it was truncated while being read). Using a `File` or `FileSink` that is not
open raises `pysdrlib.err.NotOpen`.

Tests are in `tests/test_file.py`.
"""
from . import err, sigmf
from .file import File
from .sink import FileSink
from .replay import Replay

Device = Replay   # pysdrlib.open("file", path=...): a recording played back like a radio
find = Replay.find   # nothing to list: open it with a path
