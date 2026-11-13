# UHD

Ettus USRPs through UHD's C API: the B2xx family (B200/B210, on USB) and, **experimentally**, the X310 and X410 (on a network).

| Driver | Class | Radios | Link |
| --- | --- | --- | --- |
| `uhd` | `B210` | B200, B210, B200mini, B205mini | USB 3 |
| `x310` | `X310` | X300, X310 | 1/10 GbE, PCIe |
| `x410` | `X410` | X410 | 10/25/100 GbE |

The sections below describe the B2xx; [X310 and X410](#x310-and-x410) says what differs.

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

## X310 and X410

**Experimental.** These classes were written from UHD's documentation and tested on a fake driver, not on a radio: expect
to find things wrong (the first `open` says so). Reports are welcome.

```python
sdr = pysdrlib.open("x310", addr="192.168.10.2")          # or "x410"
sdr.initialize(cf=2.4e9, Fs=100e6)
sdr.set_rx_rf_gain(20)                                     # or set_rx_gain_db(20)
sdr.start_rx(channels=(0, 1)); x = sdr.read(65536, channel=(0, 1))

for info in pysdrlib.find(addresses=["192.168.10.2"]):     # network radios are only looked for where you say
    print(info.driver, info.label)                          # "x310 UHD X310 3125A1F"
```

- **Needs a libuhd that has these radios.** Install your system's UHD (`apt install libuhd-dev uhd-host`, `pacman -S
  libuhd`...); the copy `pysdrlib build uhd` makes is B2xx only. pysdrlib finds the system library first. The FPGA image
  on the radio must match your UHD (`uhd_image_loader`, `uhd_images_downloader`): UHD says so when it doesn't.
- **Finding.** `pysdrlib.find()` never touches a network: it lists B2xx on USB. Give `addresses=` to also ask those
  hosts. `pysdrlib.open("x310", addr=...)` and `open_args` (`serial`, `addr`, `args="master_clock_rate=184.32e6"`) go into
  UHD's device address.
- **What they have.** Channels, gains, frequency and bandwidth come from the radio when it is opened (`sdr.caps`): they depend
  on the daughterboards (X310: UBX, SBX, CBX...) or the ZBX (X410). Each channel has its own LO (`set_rx_freq(f,
  channel=1)`); a stream's channels share a rate. One overall gain per channel, `RF`; no AGC, no bias tee. `sensors()`,
  `time()`, `set_time()`, `set_clock_source()` and the `time_source` setting are as on a B2xx.
- **Rates.** UHD rounds a rate to what the master clock gives: read back what `set_rx_sample_rate` returns. The link
  limits the rate (10 GbE: 200 MS/s of sc16; 1 GbE: about 25). Converting 100 MS/s and more to complex64 in the host is
  heavy; watch `sdr.stats` and `warn.Dropped`.
- **Not measured.** `RETUNE_SAMPLES` and `RETUNE_SETTLE` are estimates, and nothing here was timed on a radio.

## Libraries
`pysdrlib/_native/uhd/uhd_shim.c` is a plain-C face on UHD's C API: `<uhd/uhd.h>` pulls in UHD's C++ headers and Boost when
included from C++, so only the shim sees UHD and the extension builds without Boost. libuhd itself is the system one if
installed, else built (`pysdrlib build uhd`) from a trimmed snapshot of UHD's sources in `uhd/vendor/` (B200 family only: the X series needs a system libuhd, which would about double the snapshot; no network
or Python needed, just cmake, a C++17 compiler, Boost and libusb). `tools/vendor.py` regenerates the snapshot from upstream.

- How-to [pysdr USRP](https://pysdr.org/content/usrp.html)
- Stand-alone implementation: [uhd](https://github.com/EttusResearch/uhd)
