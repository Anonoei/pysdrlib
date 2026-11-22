"""Controls: what a radio can do (caps), gain as a fraction, in dB and by stage, and the rest of its settings. Every
device answers the same calls; one it doesn't have raises err.NotSupported. Runs on the simulated radio."""
import pysdrlib

sdr = pysdrlib.open("sim")

# --8<-- [start:caps]
caps = sdr.caps                          # refined for the attached unit once open
print(caps.freq, caps.rates)             # [Range]: tunable and sample-rate ranges (lo == hi: one discrete value)
print(caps.channels, caps.shared)        # channels per direction; what one setting changes
print(sdr.rx_gain_stages())              # {stage: GainStage}: the device's own gain stages, by its names
# --8<-- [end:caps]

# --8<-- [start:gain]
sdr.set_rx_gain(0.6)                     # a fraction of the device's range (0 least, 1 most): the portable call
sdr.set_rx_gain_db(40)                   # or in dB: a total the device splits across its stages...
sdr.set_rx_gain_db(16, stage="IF")       # ... or one stage, by its name
print(sdr.get_rx_gain(), sdr.get_rx_gain_db(), sdr.get_rx_gains_db())   # fraction, total dB, dB by stage
# --8<-- [end:gain]

# --8<-- [start:other]
print(sdr.rx_gain_modes(), sdr.settings())   # automatic gain modes, everything else the device has
for call in (lambda: sdr.set_rx_bandwidth(1.5e6),     # analog filter (caps.bandwidths)
             lambda: sdr.set_rx_gain_mode("auto")):   # automatic gain where the device has it
    try:
        call()
    except pysdrlib.err.NotSupported as e:            # the simulator has neither
        print("not supported:", e)
sdr.set_freq_correction(14.8)            # ppm error of the reference: tuning (and continuous rates) corrected
sdr.dc_correction = True                 # remove the DC offset from what read() returns
# --8<-- [end:other]

# --8<-- [start:strict]
sdr.strict = True                        # out-of-range values raise instead of being clamped
try:
    sdr.set_rx_freq(20e9)
except pysdrlib.err.InvalidValue as e:
    print(e)
# --8<-- [end:strict]
sdr.close()
