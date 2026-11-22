"""Sample files: write and read raw formats and SigMF, record a stream with its metadata, and play a recording back as
a radio. Writes into a temporary folder; runs on the simulated radio."""
import os
import tempfile

import numpy as np

import pysdrlib
from pysdrlib import File, FileSink

os.chdir(tempfile.mkdtemp())
samples = (np.exp(2j * np.pi * 0.05 * np.arange(100_000)) * 0.5).astype(np.complex64)

# --8<-- [start:raw]
with FileSink("out.cs16", "ci16", clip=True) as out:      # ci16: two int16 per sample; clip: saturate, don't raise
    out.write(samples)
print(out.count, out.clipped)

with File("out.cs16", "ci16") as f:                        # the format of a raw file is yours to give
    for block in f.forward(8192, partial=True):            # blocks until EOF; partial=True yields the short last one
        pass
    f.seek(0)                                              # positions are in samples
    head = f.next(8192)
    back = f.prev(4096)                                    # step back over the samples just read
    print(f.cur_samp, f.max_samp, f.percent())
# --8<-- [end:raw]

# --8<-- [start:sigmf]
sdr = pysdrlib.open("sim")
sdr.initialize(cf=100e6, Fs=2e6)
with FileSink("capture.sigmf", "ci16", hw=sdr.NAME) as out, sdr.stream(65536, meta=True) as s:
    for i, (x, m) in enumerate(s):
        out.write(x, meta=m)               # the rate, frequency, retunes and lost samples go into capture.sigmf-meta
        if i == 2:
            sdr.set_rx_freq(101e6)         # a new SigMF capture
        if i == 5:
            break
sdr.close()

f = File("capture.sigmf")                  # or "capture", "capture.sigmf-meta", "capture.sigmf-data"
print(f.fmt, f.rate, f.freq, f.meta.captures)
# --8<-- [end:sigmf]

# --8<-- [start:replay]
sdr = pysdrlib.open("file", path="capture.sigmf", loop=True)   # a recording, received like a radio
sdr.initialize()                           # the recording's rate and frequency
with sdr.stream(65536, meta=True) as s:
    for i, (x, m) in enumerate(s):
        print(m.index, m.freq, m.events)   # each SigMF capture is a retune
        if i == 4:
            break
sdr.close()
# --8<-- [end:replay]

# --8<-- [start:tx]
sdr = pysdrlib.open("file", tx_path="sent.sigmf", tx_format="ci16", rate=2e6, realtime=False)
sdr.initialize(cf=915e6, Fs=2e6)
sdr.start_tx()
sdr.write(samples)                         # as to a radio: complex64, full scale +-1
sdr.stop_tx()
sdr.close()
print(File("sent.sigmf").max_samp)
# --8<-- [end:tx]
