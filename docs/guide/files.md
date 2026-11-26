# Sample files

`File` reads a sample file, `FileSink` writes one, and the `file` driver plays a recording back as a radio (and
records what is transmitted to it). The formats are `pysdrlib.Formats` (`Formats.ls()` lists them). Their names
follow SigMF: the number is the bits of one value (I or Q). So a GNU Radio `.cfile` is `cf32`, an RTL-SDR `.cu8` is
`cui8`, and `ci16` is two int16. The semantics of every call are on the [`pysdrlib.file`](../api/file.md) page.

## Raw files

```python
--8<-- "examples/files.py:raw"
```

Neither class scales: integer formats are read and written as stored. A value outside an integer format's range
raises `ValueError` unless the sink was made with `clip=True`.

## SigMF

A path ending in `.sigmf` (or `.sigmf-data` / `.sigmf-meta`) makes `FileSink` write [SigMF](https://sigmf.org).
Giving `write` the `RxMeta` of each block records the stream as the radio saw it:

```python
--8<-- "examples/files.py:sigmf"
```

`FileSink(..., context=sdr.get_rx_context())` also keeps how the radio received: the LNB or converter in front of it
(its LO, and whether the spectrum arrives mirrored), the reference correction and clock, gains and antenna port. It is
stored as `pysdrlib:rx`, an optional SigMF extension that other readers skip. `sigmf.read(path).context` or
`File(path).meta.context` gives it back, a replayed recording reports it, and `pysdrlib play --info` prints it.

## A recording as a radio

```python
--8<-- "examples/files.py:replay"
```

The recording is delivered on a thread, paced to its sample rate (`realtime=False`: as fast as it is read), and each
SigMF capture is a retune. A server hosts it like a radio:
`pysdrlib server --open file:path=capture.sigmf,loop=true`.

## Transmitting into a file

```python
--8<-- "examples/files.py:tx"
```

With `path` too, it plays a recording and records what is transmitted at once: a loopback for testing a transmitter.

## `pysdrlib play`

```
pysdrlib play capture.sigmf                         what it is, then its power second by second as it plays
pysdrlib play capture.cfile --fmt cf32 --rate 2e6 --freq 100e6 --loop
pysdrlib play capture.sigmf --info                  just what it is: format, length, rate, captures, annotations
pysdrlib play capture.sigmf --serve tcp://0.0.0.0:5555 --loop    host it: clients watch (or claim) device file-0
```

`--fast` plays as fast as it can be read, and `--interval S` sets how often a line is printed. Serving needs pyzmq.

## Recording from the command line

```
pysdrlib client tcp://host:5555 rx hackrf --freq 100e6 --rate 10e6 -t 5 -f ci8 -o capture.sigmf
```

See [Remote radios](remote.md#command-line).
