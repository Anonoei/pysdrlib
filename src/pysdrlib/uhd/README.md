# UHD

Ettus USRP B2xx (B200/B210) through UHD's C API.

```python
import pysdrlib
sdr = pysdrlib.open("uhd")                 # or pysdrlib.find("uhd")[0].open()
sdr.set_freq(915e6); sdr.set_sample_rate(10e6); sdr.set_rx_gain(40)
sdr.start_rx(); x = sdr.read(1_000_000)    # complex64

sdr.start_tx(); sdr.write(tone)            # complex64, full scale is +-1; sdr.tx_stats
```

| | |
|---|---|
| Frequency | 50 MHz - 6 GHz |
| Sample rate | 200 kS/s - 61.44 MS/s (about 32 MS/s sustained over USB 3 with Python in the loop) |
| Gain | one stage per direction, the AD9361 PGA: RX 0-76 dB (1 dB), TX 0-89.75 dB (0.25 dB); `set_gain_mode("auto")` for RX AGC |
| Antennas | RX: `TX/RX`, `RX2`. TX: `TX/RX` |
| Bandwidth | 200 kHz - 56 MHz analog filter, per direction |
| Settings | `dc_offset_auto`, `iq_balance_auto`, `time_source`, `master_clock_rate` |
| Clock | `internal`, `external`, `gpsdo` (`set_clock_source`) |
| Extras | `sensors()` (lo_locked, temp, rssi, ref_locked), `time()`/`set_time()`, `open(args="type=b200,...")` |

The master clock is chosen from the sample rate (an even multiple of it in the AD9361's 25-61.44 MHz range) unless
`master_clock_rate` is set; changing it pauses the streams.

**Second frontend.** The generic API (`Device`) drives channel 0. The B210's second RX/TX frontend is reachable through
`sdr.device` (the extension's methods take a `channel`, e.g. `sdr.device.set_rx_gain(30, 1)`); streaming it, and
two-channel reads, wait on channel support in the base `Device`.

## Libraries
`pysdrlib/_native/uhd/uhd_shim.c` is a plain-C face on UHD's C API: `uhd.h` pulls in UHD's C++ headers and Boost when
included from C++, so only the shim sees UHD and the extension builds without Boost. libuhd itself is the system one if
installed, else built from the `vendor/uhd` submodule (B200 family only, see `vendor/CMakeLists.txt`) on first import.

- How-to [pysdr USRP](https://pysdr.org/content/usrp.html)
- Stand-alone implementation: [uhd](https://github.com/EttusResearch/uhd)
