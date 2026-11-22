# Controls and channels

Every device describes itself in `sdr.caps`, and the same calls work on all of them. A control a device doesn't
have raises `err.NotSupported`.

## What a radio can do

```python
--8<-- "examples/controls.py:caps"
```

`Capabilities` ([reference](../api/caps.md)) is what the driver declares, refined once the device is open for the
unit attached (a tuner's real gain table, the rates its firmware offers).

## Gain

```python
--8<-- "examples/controls.py:gain"
```

There are three ways to set gain, and they're meant for different code:

- **`set_rx_gain(fraction)`** is the portable call. 0.5 is the middle of *this* radio's range, whatever the
  radio. 40 dB is most of one radio's range and a third of another's, so code that should run on more than one kind
  of radio uses the fraction. A value outside 0-1 raises rather than being clamped: it is nearly always decibels
  given to the wrong call.
- **`set_rx_gain_db(total)`** sets a total in dB, which the device splits across its stages.
- **`set_rx_gain_db(db, stage="...")`** sets one stage, by the device's own names (`rx_gain_stages()`). Each
  driver also keeps its own per-stage setters (`set_rx_lna_gain`, `set_rx_vga_gain`...) alongside the generic ones.

## Everything else

```python
--8<-- "examples/controls.py:other"
```

```python
--8<-- "examples/controls.py:strict"
```

Each control that differs between receiving and transmitting has a call for each direction: `set_rx_freq` /
`set_tx_freq`, `set_rx_gain` / `set_tx_gain`, `set_rx_bandwidth` / `set_tx_bandwidth`, `set_rx_antenna` /
`set_tx_antenna`... Each takes `channel=0`. `sdr.rx_antennas()` and `sdr.clock_sources()` list what a device has.

## Channels

A device with several channels (`sdr.caps.channels`, e.g. a B210's 2 RX and 2 TX) streams any of them together:

```python
--8<-- "examples/channels.py:channels"
```

`caps.shared` says what one setting changes: `"freq": "direction"` means the channels of a direction share one
synthesizer, so retuning one retunes the others. Samples the hardware lost before they reached pysdrlib (an overflow)
show as gaps on every channel alike, so the channels stay aligned.

## Transmitting

Radios that transmit (`caps.channels["tx"] > 0`) take `complex64` at full scale ±1 through `start_tx()` and
`write(samples)`. `write` blocks while the transmit queue is full, and `get_tx_stats()` counts underruns. Start at
`tx_gain=0`, into a load or an antenna you are licensed to use. The HackRF is half-duplex; see each driver's page.

The method reference for all of this is [`pysdrlib.base.device`](../api/device.md).
