"""The air: a medium that devices transmit into and receive from, in one process or across processes on a machine

    lab = pysdrlib.air.Air("lab")
    tx = pysdrlib.open("sim", air="lab", transmit=True)       # a simulated radio's transmit channels go into the air
    rx = pysdrlib.open("sim", air="lab")                      # and its receive channel hears it (here, or in any process)
    tx.set_tx_freq(433.92e6); tx.set_tx_sample_rate(1e6); tx.start_tx(); tx.write(x)
    rx.initialize(cf=433.9e6, Fs=2e6); rx.start_rx(); y = rx.read(65536)

An `Air` is a name and its settings (`loss_db`, `capacity`, `latency`, `directory`); it holds nothing open. What uses it
attaches channels:

- `air.tx_channel(Fs, cf, clock)` → `TxChannel`: `write(samples, Fs, cf, scale=1.0)` places samples in the air at their
  time. Each one is a shared-memory stream (`pysdrlib.shm.ShmStream`, named `air-<name>.<pid>-<n>`, its manifest saying
  the rate, frequency and time of its samples). A new `Fs` or `cf` is a new generation of the stream.
- `air.rx_channel()` → `RxChannel`: `receive(first, n, cf, Fs, clock)` is what it hears in its samples first..first+n:
  every transmit channel in the air, shifted by the difference of the frequencies, resampled to its rate, attenuated by
  the air's `loss_db`, summed. A transmit channel whose centre is beyond +-Fs/2 of the receiver's is not heard. `anchor(t0,
  first)` says when its sample `first` is.

Whoever makes a channel closes it. A `Sim` with `air=` (an `Air`, or its name) makes one receive channel and a transmit
channel for each channel it transmits on, and closes them with itself; it hears its own transmission too, before its gain
and ADC (`gain_ref`, clipping), as its own tones are. Its transmit gain (the "PA" stage) scales what goes into the air.

**Time.** A channel is on one of two clocks, and channels on different clocks don't hear each other:

- `"wall"`: seconds of `time.time()`. A transmit channel is paced to its rate (`write` waits, as a radio's queue does)
  and its samples are placed at the time they go out; written late, after a pause, it starts again at the time it is
  written (the pause is silence). Processes on one machine share the clock, so a receiver in one hears a transmitter in
  another, in time. A realtime `Sim` hears the air `latency` (0.1 s) late, as a radio's transfers are, so what was just
  written is there to hear.
- `"samples"`: each counts its own samples from 0 (a `Sim` that isn't realtime: receiving from `open`, transmitting from
  its first `write`), so in one process a test is exact and repeatable: write, then `pump` the receiver.

Within a stream, time is kept relative to its first sample, so a receiver's samples are sample-exact and phase-continuous
from block to block on either clock (absolute phase isn't modelled: two receivers at different frequencies see a
transmitter at different phases).

**Lifetime.** A transmit channel's stream lasts until it is closed (on a `Sim`, `stop_tx` is silence; `close` ends it). A
receive channel that has seen a stream (it lists the air at every block on the samples clock, every 50 ms on the wall
clock) hears it to its end even after its transmitter is closed or its process gone, so one that is behind still hears it
all; one that never saw it doesn't. Each stream's ring holds `capacity` samples (default: a second); a receiver further
behind than that misses the oldest.

`airs()` lists the airs in use on this machine; `pysdrlib.find(virtual=True)` lists a simulated radio in each. Each
transmit channel is also a `shm` stream that `pysdrlib.open("shm@air-<name>.<pid>-<n>")` receives raw (hidden from
`find()` unless `virtual=True`).

It is a simulator: resampling is linear (exact when the rates are equal: the nearest sample, on the time grid) and
nothing is filtered, so a wideband transmitter heard by a slower receiver aliases into it; there is no multipath and no
Doppler. Add noise with the receiving `Sim`'s `noise=`.
"""
from .air import Air, airs
from .channel import RxChannel, TxChannel

__all__ = ["Air", "RxChannel", "TxChannel", "airs"]
