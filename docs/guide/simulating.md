# Simulated radios

`pysdrlib.open("sim")` is a radio with no hardware behind it: tones, noise and bursts built to order, on pysdrlib's
real streaming path (see [`pysdrlib.sim`](../api/sim.md)). Simulated radios can also share an *air*
([`pysdrlib.air`](../api/air.md)): what one transmits, every one in it hears, shifted to its frequency, resampled to its
rate, and attenuated by the air's `loss_db`, then through its own noise, gain and ADC.

## Exact, in one process

A sim that isn't realtime counts its own samples, so a transmitter written and a receiver pumped in one process give
the same samples every run: a test of what reads a radio, with a signal that came through a transmitter.

```python
--8<-- "examples/air.py:exact"
```

## In time, across processes

Realtime sims are on the wall clock. Writing is paced to the transmit rate, and a pause in writing is silence. The air
is shared memory, so a transmitter in one process (or a `pysdrlib server --open sim:air=lab,transmit=true`) is heard by
a receiver in another, such as pyspecan opening `sim` with `air="lab"`. A realtime receiver hears the air 0.1 s late
(`Air(latency=)`), as a radio's transfers are.

```python
--8<-- "examples/air.py:realtime"
```

`pysdrlib.find(virtual=True)` lists a simulated radio in each air in use. Each transmit channel is also a `shm` stream,
so `pysdrlib.open("shm@air-lab.<pid>-0")` receives one transmitter raw.

## Channels of your own

Anything can transmit into an air, or receive from it, without a `Sim`: `air.tx_channel(Fs, cf, clock)` and
`air.rx_channel()` (close them when done). The clocks, lifetimes and limits are on the
[`pysdrlib.air`](../api/air.md) page.
