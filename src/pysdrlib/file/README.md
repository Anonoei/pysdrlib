# File

Read sample files like a device, and write them from one. Both use the formats in `pysdrlib.Formats`
(`pysdrlib.Formats.ls()` lists them).

| Class | |
| --- | --- |
| `File(path, fmt)` | Reads samples from a file, forward or back, in blocks |
| `FileSink(path, fmt)` | Writes samples to a file |

**Check the format name:** the number in a complex format's name is the bits of the *whole* sample, so a GNU Radio
`.cfile` (two float32) is `cf64`, not `cf32`. See [the format reference](https://anonoei.github.io/pysdrlib/base/formats.html).

`File` is a `pysdrlib.Device`-shaped source: it has `get_samples()`, so code that processes a device's samples can be
pointed at a recording. Neither class scales: integer formats are read and written as stored, not divided by full
scale.

## Reading

```python
from pysdrlib import File

with File("capture.cfile", "cf64") as f:           # cf64: two float32 per sample (a GNU Radio .cfile)
    for block in f.forward(8192, partial=True):    # blocks until EOF; partial=True also yields the short last one
        print(f.percent())                         # fraction read, 0.0-1.0 (percent(fraction=False): 0-100)
    f.seek(0)                                      # move to a sample index
    head = f.next(8192)                            # the next 8192 samples
    back = f.prev(8192)                            # step back and return the samples just before the position
```

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

```python
from pysdrlib import FileSink

with FileSink("out.cs16", "ci32t", clip=True) as out:    # or FileSink(..., open=True) and close() yourself
    out.write(samples)
print(out.count, out.clipped)
```

- `FileSink(path, fmt, append=False, open=False, clip=False)` truncates an existing file unless `append=True`. The format
  of an existing file is **not** checked when appending.
- `write()` takes 1-D samples (other shapes raise `ValueError`: flatten, or write each channel to its own file) and needs
  the sink open (`open()`, `open=True` or `with`).
- A value outside the format's range (including NaN or infinity in an integer format) raises `ValueError`; nothing
  wraps. With `clip=True` values saturate instead (each I/Q component on its own), NaN is stored as 0, and one warning,
  "N samples clipped", is logged when the sink closes. `FileSink.clipped` holds the running count.

## Errors

`pysdrlib.file.err` defines `Overflow` (asking for samples past either end of the file) and `Underflow` (the file had
fewer samples than its size promised, e.g. it was truncated while being read). Using a `File` or `FileSink` that is not
open raises `pysdrlib.err.NotOpen`.

Tests are in `tests/test_file.py`.
