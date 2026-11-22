"""Reading samples: an exact count with what happened to the stream, blocks from a stream, sweeps, retunes and lost
samples. Runs on the simulated radio."""
import numpy as np

import pysdrlib

sdr = pysdrlib.open("sim")
sdr.initialize(cf=100e6, Fs=2e6)
sdr.start_rx()

# --8<-- [start:meta]
x, m = sdr.read(65536, meta=True)        # m: RxMeta
print(m.index, m.freq, m.rate)           # the first sample's position in the stream, what the radio was set to
print(m.gaps, m.dropped, m.events)       # where samples were lost, how many, retunes inside the block
print(m.time, m.hw_time)                 # host time of the first sample; the radio's own clock (or None)

buffer = np.empty(16384, np.complex64)
sdr.read_into(buffer)                    # into an array you own: no allocation per read
# --8<-- [end:meta]

# --8<-- [start:retune]
sdr.set_rx_freq(162.4e6, flush=True)     # the next read is all at the new frequency (settling skipped)
x, m = sdr.read(8192, meta=True)
assert m.freq == 162.4e6
# --8<-- [end:retune]

# --8<-- [start:drops]
sdr.on_drop = "ignore"                   # lost samples: "warn" (default), "raise", "ignore" or a function
sdr.drop(10_000)                         # (the simulator loses samples on demand, as a radio's overflow would)
for _ in range(8):                       # the loss shows in the block it falls in
    x, m = sdr.read(65536, meta=True)
    if m.dropped:
        print("lost", m.dropped, "samples at", m.gaps)   # [(offset in the block, samples missing just before it)]
        break
try:
    sdr.drop(10_000)
    for _ in range(8):
        x = sdr.read(65536, on_drop="raise")   # per read: raise err.Dropped for a capture that must be whole
except pysdrlib.err.Dropped as e:
    print(e)
print(sdr.stats)                         # delivered, dropped, missing, stalls, seconds since the last samples
# --8<-- [end:drops]

# --8<-- [start:stalls]
try:
    x = sdr.read(65536, timeout=1.0)
except pysdrlib.err.StreamStalled:       # no samples for sdr.stall_timeout seconds (default 1; None disables)
    sdr.stop_rx(); sdr.start_rx()        # or close and reopen the device
# --8<-- [end:stalls]
sdr.stop_rx()

# --8<-- [start:stream]
with sdr.stream(65536, meta=True) as s:  # starts receiving, stops on exit
    for i, (x, m) in enumerate(s):
        print(m.index, np.mean(np.abs(x) ** 2))
        if i == 3:
            break
# --8<-- [end:stream]

# --8<-- [start:sweep]
for freq, x in sdr.sweep(88e6, 96e6, count=65536):    # start to stop, in steps of 80% of the rate
    print(f"{freq / 1e6:.1f} MHz: {10 * np.log10(np.mean(np.abs(x) ** 2)):.1f} dB")
for freq, x in sdr.sweep([162.4e6, 162.55e6], count=4096, offset=250e3, skip=1000):   # a list; DC off the middle
    pass
# --8<-- [end:sweep]
sdr.close()
