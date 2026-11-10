# UHD

Ettus USRP B2xx (B200/B210) through UHD's C API.

```python
import pysdrlib
sdr = pysdrlib.open("uhd")                 # or pysdrlib.find("uhd")[0].open()
sdr.set_rx_freq(915e6); sdr.set_rx_sample_rate(10e6); sdr.set_rx_gain_db(40)
sdr.start_rx(); x = sdr.read(1_000_000)    # complex64

sdr.start_tx(); sdr.write(tone)            # complex64, full scale is +-1; sdr.tx_stats
```

| | |
|---|---|
| Frequency | 50 MHz - 6 GHz |
| Sample rate | 200 kS/s - 61.44 MS/s (about 32 MS/s sustained over USB 3 with Python in the loop) |
| Channels | B210: 2 RX, 2 TX (`start_rx(channels=(0, 1))`, `start_tx(channels=(0, 1))`); B200: 1 |
| Gain | one stage per direction, the AD9361 PGA: RX 0-76 dB (1 dB), TX 0-89.75 dB (0.25 dB); `set_rx_gain_mode("auto")` for RX AGC |
| Antennas | RX: `TX/RX`, `RX2`. TX: `TX/RX` |
| Bandwidth | 200 kHz - 56 MHz analog filter, per direction |
| Settings | `dc_offset_auto`, `iq_balance_auto`, `time_source`, `master_clock_rate` |
| Clock | `internal`, `external`, `gpsdo` (`set_clock_source`) |
| Extras | `sensors()` (lo_locked, temp, rssi, ref_locked), `time()`/`set_time()`, `open(args="type=b200,...")` |

The master clock is chosen from the sample rate (an even multiple of it in the AD9361's 25-61.44 MHz range) unless
`master_clock_rate` is set; changing it pauses the streams.

**Two channels.** A B210 has two RX and two TX channels (frontends A and B; a B200 has one). Every control takes
`channel`:
```python
sdr.set_rx_gain_db(50, channel=1); sdr.set_rx_antenna("TX/RX", channel=1)
sdr.set_tx_freq(915e6)                 # RX stays where it was
sdr.start_rx(channels=(0, 1)); x = sdr.read(65536, channel=(0, 1))     # (2, 65536): the same instants
sdr.start_tx(channels=(0, 1)); sdr.write(np.stack((a, b)))              # sent sample for sample together
```
The AD9361 has one RX and one TX synthesizer: a frequency is shared by a direction's channels (`caps.shared`), and the
driver tunes both channels' DSP alike, so they stay phase coherent (measured: the phase between them steady to 0.1 deg).
A stream's channels share its rate. With two channels the master clock is at most 30.72 MHz (UHD's limit), so the
driver picks a power-of-two multiple of the rate under it (10 MS/s: 20 MHz); 30.72 MS/s per channel is the most.
A two-channel stream starts on a timed command so both start on the same sample.

On the unit this was tested with, two-channel streams sometimes stop with UHD's `accum_timeout` control error (about
1 start in 8-16, the same with UHD's own `rx_samples_to_file --channels 0,1`): `read` raises `err.StreamStalled`; close
and reopen the device. Single-channel streams were not affected.

## Libraries
`pysdrlib/_native/uhd/uhd_shim.c` is a plain-C face on UHD's C API: `uhd.h` pulls in UHD's C++ headers and Boost when
included from C++, so only the shim sees UHD and the extension builds without Boost. libuhd itself is the system one if
installed, else built from the `vendor/uhd` submodule (B200 family only, see `vendor/CMakeLists.txt`) on first import.

- How-to [pysdr USRP](https://pysdr.org/content/usrp.html)
- Stand-alone implementation: [uhd](https://github.com/EttusResearch/uhd)
