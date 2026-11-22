# Receiving

Every radio has the same calls. The examples here run on the simulated radio (`"sim"`); put a driver name
(`"rtl_sdr"`, `"hackrf"`, `"uhd"`...) or a `DeviceInfo` from `find()` in its place for real hardware.

## Find, open, read

```python
--8<-- "examples/quickstart.py:find"
```

```python
--8<-- "examples/quickstart.py:read"
```

- `pysdrlib.open` takes a driver name (`serial=` picks the unit), a `DeviceInfo`, a target string
  (`"hackrf@<serial>"`, `"radiohost/hackrf-0"`: see `pysdrlib.targets`), and returns the device opened.
- `pysdrlib.device("rtl_sdr", ...)` makes one without opening it; its arguments go to the class (`sdr.open()` next).
  `pysdrlib.devices.ls()` lists the drivers, and `import pysdrlib.rtl_sdr` works as a static import.
- `find()` never builds a vendor library (`find(build=True)` does), probes no network unless given
  `addresses=[...]`, and lists the simulated radio only with `virtual=True`.

## `read`: an exact count, and what happened

`read` and `stream` are the way to get samples. `get_samples()` is the low-level call under them: whatever has
arrived, any number, without waiting.

```python
--8<-- "examples/receiving.py:meta"
```

`RxMeta` ([reference](../api/stream.md)) says where the block is in the stream and what happened inside it: lost
samples (`gaps`), retunes (`events`), and the radio's own timestamp where it has one (UHD, a replayed recording).

## Retuning

```python
--8<-- "examples/receiving.py:retune"
```

`flush=True` discards what was received before the change and the radio's settling time. Without it, a block can
straddle the retune: `m.events` says where.

## Lost samples and stalls

Samples are lost when Python doesn't read often enough, or when the radio delivers fewer than its rate requires.
Either way they show as gaps, so indices stay true:

```python
--8<-- "examples/receiving.py:drops"
```

Hardware devices also watch their own stream each time you read. A stream that stops delivering raises:

```python
--8<-- "examples/receiving.py:stalls"
```

Anything still buffered when a stall is detected is returned first.

## Streams and sweeps

```python
--8<-- "examples/receiving.py:stream"
```

```python
--8<-- "examples/receiving.py:sweep"
```

A sweep retunes with `flush=True` at each step, so every block is all at its frequency. `offset` moves the DC
spike off the middle of each step; `skip` discards more samples after each retune.

Whole script: [`examples/receiving.py`](https://github.com/Anonoei/pysdrlib/blob/main/examples/receiving.py).
Next: [controls and channels](controls.md).
