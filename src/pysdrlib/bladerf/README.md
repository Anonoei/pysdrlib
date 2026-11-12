# BladeRF

Nuand bladeRF 2.0 micro (xA4, xA9) through libbladeRF. The bladeRF 1.0 (x40, x115) is **not** supported yet: opening one
raises `err.NoDevice`.

```python
import pysdrlib
sdr = pysdrlib.open("bladerf")                # or pysdrlib.find("bladerf")[0].open()
sdr.set_rx_freq(915e6); sdr.set_rx_sample_rate(10e6); sdr.set_rx_gain_db(30)
sdr.start_rx(); x = sdr.read(1_000_000)       # complex64

sdr.start_tx(); sdr.write(tone)               # complex64, full scale is +-1; sdr.tx_stats
```

| | |
|---|---|
| Frequency | 70 MHz - 6 GHz (libbladeRF lets the transmitter go down to 47 MHz) |
| Sample rate | 520.834 kS/s - 61.44 MS/s, full duplex |
| Samples | complex int 12 (SC16 Q11) |
| Channels | 2 RX, 2 TX (`start_rx(channels=(0, 1))`, `start_tx(channels=(0, 1))`) |
| Gain | one stage per direction. RX `full` (whole dB; 1 to 77 below 1.3 GHz, -4 to 71 up to 4 GHz, -10 to 62 above: libbladeRF clamps to the band), TX `dsa` (-89 to 0 dB: 0 is full power). `set_rx_gain_mode("auto")` for the AD9361's AGC |
| Bandwidth | 200 kHz - 56 MHz analog filter, per channel |
| Bias tee | `set_bias_t` |
| Settings | `clock_output` |
| Clock | `internal`, `external` (`set_clock_source`) |
| Extras | `sensors()` (rfic_temperature, pll_locked); `open(args="*:instance=1")` takes a libbladeRF device string |

The AD9361 has one RX and one TX synthesizer: a frequency is shared by a direction's channels (`caps.shared`), and a stream's
channels share its rate. Two channels are streamed as the pair `(0, 1)`.

Samples are carried over USB 3 (SuperSpeed); `caps.info["usb_speed"]` says which one the unit is on, and a USB 2 port cannot carry
more than a few MS/s. Overruns are counted from the timestamps libbladeRF puts in the receive stream (`stats`, `err.Dropped`).
The transmit stream carries none: if `write` falls behind, silence is sent and counted in `tx_stats["underruns"]`.

The gain is in whole dB because libbladeRF takes it as an integer: the AD9361's attenuator has quarter-dB steps that it does not offer.

**Not measured.** This driver was written without a bladeRF attached: it builds and passes the contract tests over a fake
extension, but `RETUNE_SAMPLES` and `RETUNE_SETTLE` are estimates from the stream settings, and nothing here has streamed
real samples. Run the `hardware` tests (`PYSDRLIB_HW=1 pytest`) with a unit attached before relying on it.

## Libraries
libbladeRF is the system one if installed (`apt install libbladerf2`), else built (`pysdrlib build bladerf`) from a trimmed
snapshot of bladeRF's sources in `bladerf/vendor/` (no network or Python needed, just cmake, a C compiler, `patch` and libusb).
The AD9361 driver it uses is Analog Devices' no-OS, a git submodule of bladeRF that its release tarball lacks: the snapshot
includes it. `tools/vendor.py` regenerates the snapshot from upstream. USB access needs libbladeRF's udev rules
(`88-nuand-bladerf2.rules`), or root.

- How-to [pysdr BladeRF](https://pysdr.org/content/bladerf.html)
- Stand-alone implementation: [bladeRF](https://github.com/Nuand/bladeRF)
