"""Nuand bladeRF 2.0 micro (xA4, xA9) through libbladeRF. The bladeRF 1.0 (x40, x115) is **not** supported yet: opening one
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
Those timestamps are also the received samples' hardware time: `read(..., meta=True)` gives `RxMeta.hw_time`, the
stream's sample counter in seconds (it counts at the sample rate, and a rate change carries the time on from there).

The gain is in whole dB because libbladeRF takes it as an integer: the AD9361's attenuator has quarter-dB steps that it does not offer.

**Tested on hardware** (bladeRF 2.0 micro xA4, FPGA 0.16.0, firmware 2.6.0, libbladeRF 2.6.0, USB 3, Linux): the `hardware`
tests (`PYSDRLIB_HW=1 pytest -m hardware -k bladerf`) pass, and a reader keeps up with no loss from 0.52 to 61.44 MS/s.

- Retuning: `bladerf_set_frequency` returns once the PLL is locked, ~11 ms within a band and ~130 ms across bands
  (100 MHz <-> 2.4/5 GHz), and pysdrlib marks the stream when it returns. At most 2034 samples from before a retune arrived
  after the mark (2 MS/s, none at 10 and 40 MS/s), so `RETUNE_SAMPLES` is one 16384-sample buffer and `RETUNE_SETTLE` 5 ms;
  `set_rx_freq(f, flush=True)` gave a clean next block in 72 of 72 tries (100 <-> 125 MHz and 100 MHz <-> 5 GHz, at 2, 10
  and 40 MS/s). A retune's event in `read(meta=True)` is placed at that mark, so it is late by the call's duration: the
  samples just before it are already at the new frequency.
- Gain: libbladeRF opens the receivers in the AD9361's AGC; the driver switches them to manual, which is what
  `get_rx_gain_mode()` reports. The AD9361 reports the gain of an enabled receiver only: read back while streaming.
- A host that falls behind can lose samples with no break in the stream's timestamps, so `stats.missing` (the rate
  check), not a gap, is what shows it.

Two-channel streaming and transmitting (zeros at -89 dB) were checked for plumbing only; no RF was measured on TX.

## Libraries
libbladeRF is the system one if installed (`apt install libbladerf2`), else built (`pysdrlib build bladerf`) from a trimmed
snapshot of bladeRF's sources in `bladerf/vendor/` (no network or Python needed, just cmake, a C compiler, `patch` and libusb).
The AD9361 driver it uses is Analog Devices' no-OS, a git submodule of bladeRF that its release tarball lacks: the snapshot
includes it. `tools/vendor.py` regenerates the snapshot from upstream. USB access needs libbladeRF's udev rules
(`88-nuand-bladerf2.rules`), or root.

- How-to [pysdr BladeRF](https://pysdr.org/content/bladerf.html)
- Stand-alone implementation: [bladeRF](https://github.com/Nuand/bladeRF)
"""
from .._ext import bladerf as ext, utils

BladeRF = utils.import_device(__name__, ".bladerf", "BladeRF", ext.LIB)
Device = BladeRF

def find():
    """[DeviceInfo] of the bladeRFs attached"""
    from ..devices import DeviceInfo
    from . import lib
    found = []
    for i, d in enumerate(lib.bladerf.find()):
        serial = d.get("serial") or None
        label = " ".join(x for x in (d.get("product") or "bladeRF", serial) if x)
        found.append(DeviceInfo("bladerf", label, serial, i, {"serial": serial} if serial else {}, dict(d)))
    return found
