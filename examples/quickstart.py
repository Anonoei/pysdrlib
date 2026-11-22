"""Find a radio, open it, tune it and read samples. Runs on the simulated radio, so it needs no hardware."""
import pysdrlib

# --8<-- [start:find]
for info in pysdrlib.find(virtual=True):    # radios attached (virtual=True: the simulated one too)
    print(info.driver, info.label, info.serial)
# --8<-- [end:find]

# --8<-- [start:read]
sdr = pysdrlib.open("sim")                 # a driver name; or a DeviceInfo from find(), or "<driver>@<serial>"
sdr.initialize(cf=100e6, Fs=2e6)           # center frequency and sample rate
sdr.set_rx_gain(0.5)                       # half of this radio's gain range, whatever the radio
sdr.start_rx()
x = sdr.read(65536, timeout=1.0)           # exactly 65536 complex64 samples (err.Timeout after 1 s)
sdr.stop_rx()
sdr.close()
# --8<-- [end:read]

print(x.dtype, x.shape)
assert x.dtype == "complex64" and x.shape == (65536,)
