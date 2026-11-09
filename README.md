# pysdrlib
 Python wrappers for hardware SDRs

 - [Documentation](https://anonoei.github.io/pysdrlib/)
 - [PyPI](https://pypi.org/project/pysdrlib/)

## Notes
- No affiliation to the fantastic [pysdr](https://pysdr.org) page
- For a more robust C implementation, check [SoapySDR](https://github.com/pothosware/SoapySDR)

This serves as a lightweight backend for my [pyspecan](https://github.com/Anonoei/pyspecan) and [RPPS](https://github.com/Anonoei/RPPS) projects,
with minimal user intervention.
Other projects, like SoapySDR, are likely what you're looking for.

## Building from source
For each device's vendor (HackRF, ExtIO, etc), if the necessary libraries aren't installed, each vendor library is build on first use.
This requires `cmake`, a C++17 compiler
and the development files below (Debian/Ubuntu package names);
tests for a device whose library can't be built are skipped.

Prebuilt `librtlsdr` / `libhackrf` packages can be installed instead of building those two.
All packages are the build tools (`cmake`, a C++17 compiler, `pkg-config`) plus the development files listed.

**RTL-SDR** needs:
- `librtlsdr`, or
- Debian/Ubuntu: `sudo apt install cmake libusb-1.0-0-dev`
- Fedora/RHEL: `sudo dnf install gcc-c++ cmake pkgconf-pkg-config libusb1-devel`
- Arch: `sudo pacman -S base-devel cmake libusb`
- openSUSE: `sudo zypper install gcc-c++ cmake pkg-config libusb-1_0-devel`
- Alpine: `apk add build-base cmake pkgconf libusb-dev`
- macOS (Homebrew): `brew install cmake pkg-config libusb`

**HackRF** needs:
- `libhackrf`, or
- Debian/Ubuntu: `sudo apt install cmake libusb-1.0-0-dev`
- Fedora/RHEL: `sudo dnf install gcc-c++ cmake pkgconf-pkg-config libusb1-devel`
- Arch: `sudo pacman -S base-devel cmake libusb`
- openSUSE: `sudo zypper install gcc-c++ cmake pkg-config libusb-1_0-devel`
- Alpine: `apk add build-base cmake pkgconf libusb-dev`
- macOS (Homebrew): `brew install cmake pkg-config libusb`

**RX-888 (extio)** needs:
- `libextio_sdcc`, or
- Debian/Ubuntu: `sudo apt install cmake libusb-1.0-0-dev libfftw3-dev`
- Fedora/RHEL: `sudo dnf install gcc-c++ cmake pkgconf-pkg-config libusb1-devel fftw-devel`
- Arch: `sudo pacman -S base-devel cmake libusb fftw`
- openSUSE: `sudo zypper install gcc-c++ cmake pkg-config libusb-1_0-devel fftw3-devel`
- Alpine: `apk add build-base cmake pkgconf libusb-dev fftw-dev`
- macOS (Homebrew): `brew install cmake pkg-config libusb fftw`

**UHD (USRP B2xx, e.g. B210)** needs:
- `libuhd`, or (building it takes several minutes; only the B200 family is built)
- Debian/Ubuntu: `sudo apt install cmake libusb-1.0-0-dev libboost-all-dev`
- Fedora/RHEL: `sudo dnf install gcc-c++ cmake pkgconf-pkg-config libusb1-devel boost-devel`
- Arch: `sudo pacman -S base-devel cmake libusb boost`
- openSUSE: `sudo zypper install gcc-c++ cmake pkg-config libusb-1_0-devel boost-devel`
- Alpine: `apk add build-base cmake pkgconf libusb-dev boost-dev`
- macOS (Homebrew): `brew install cmake pkg-config libusb boost`

A B2xx also loads an FPGA image when opened: install UHD's images (`uhd_images_downloader -t b2xx`, or `apt install uhd-host`)
or point `UHD_IMAGES_DIR` at yours (an image already in `/usr/share/uhd/images` is used as is; pysdrlib never downloads one itself).

# Roadmap
- [X] [RTL-SDR](https://github.com/Anonoei/pysdrlib/tree/main/src/pysdrlib/rtl_sdr)
- [X] [Hack RF](https://github.com/Anonoei/pysdrlib/tree/main/src/pysdrlib/hackrf)
- [ ] [Blade RF](https://github.com/Anonoei/pysdrlib/tree/main/src/pysdrlib/bladerf)
- [X] [B210 (UHD)](https://github.com/Anonoei/pysdrlib/tree/main/src/pysdrlib/uhd)
- [X] [RX-888 (extio)](https://github.com/Anonoei/pysdrlib/tree/main/src/pysdrlib/extio)

## Usage
Dynamic imports
```
import pysdrlib
print(pysdrlib.devices.ls()) # Print available device interfaces
sdr = pysdrlib.devices.get("rtl_sdr").Device() # Initialize sdr
sdr.open()
```
Static imports
```
import pysdrlib.rtl_sdr
sdr = pysdrlib.rtl_sdr.Device() # Initialize sdr
```

### Finding devices
```python
for info in pysdrlib.find():          # the radios attached, for every driver whose library is installed or built
    print(info.driver, info.label, info.serial)
sdr = pysdrlib.open(pysdrlib.find()[0])           # or pysdrlib.open("<driver>", serial="...")
```
`find()` never builds a vendor library (`find(build=True)` allows it); `find(virtual=True)` also lists the simulated device.

### Reading
```python
sdr.start_rx()
x = sdr.read(65536, timeout=1.0)                 # exactly 65536 samples, waiting for them (err.Timeout after 1 s)
x, m = sdr.read(65536, meta=True)                # m.index: first sample's position in the stream; m.gaps / m.dropped:
                                                 # where samples were lost; m.events: retunes inside the block; m.time
sdr.read_into(buffer)                            # into an array you own

with sdr.stream(65536, meta=True) as s:          # starts receiving, stops on exit
    for x, m in s: ...

sdr.set_freq(162.4e6, flush=True)                # the next read is all at the new frequency (settling skipped)
```
`get_samples()` (whatever has arrived) still works. `sdr.on_drop` decides what lost samples do: `"warn"` (default),
`"raise"`, `"ignore"` or a function.

### Controls
Every device describes itself in `sdr.caps` (refined for the attached unit once open), and the same calls work on all of
them; a control a device doesn't have raises `err.NotSupported`:
```python
sdr.caps.freq, sdr.caps.rates                    # [Range]: tunable and sample-rate ranges (lo == hi: one discrete value)
sdr.gains()                                      # {stage: GainStage}: the device's own gain stages, by its names
sdr.set_gain(40)                                 # total gain (the device splits it); sdr.set_gain(16, stage="...") one stage
sdr.get_gain(), sdr.set_gain_mode("auto")        # readback; automatic gain where the device has it (sdr.gain_modes())
sdr.set_bandwidth(1.5e6)                         # analog filter (sdr.caps.bandwidths)
sdr.set_freq_correction(14.8)                    # ppm error of the reference: tuning (and continuous rates) corrected
sdr.settings(); sdr.set_setting("bias_t", True)  # everything else the device has
sdr.antennas(), sdr.clock_sources()              # where the device has them
sdr.strict = True                                # out-of-range values raise err.InvalidValue instead of being clamped
sdr.dc_correction = True                         # remove the DC offset from what read() returns
```
Every control takes `direction="rx"`/`"tx"` where it applies and `channel=0` (the count is in `sdr.caps.channels`).

### Channels
A device with several channels (`sdr.caps.channels`, e.g. a B210's 2 RX and 2 TX) streams any of them together:
```python
sdr.set_gain(40, channel=1)                       # every control takes the channel
sdr.set_freq(915e6, direction="tx")               # tuning takes the direction too (None: both)
sdr.caps.shared                                   # what one setting changes: "freq": "direction" means the channels
                                                  # of a direction share one synthesizer
sdr.start_rx(channels=(0, 1))
x = sdr.read(65536, channel=(0, 1))               # (2, 65536), rows starting on the same sample
x1 = sdr.read(65536, channel=1)                   # or one channel at a time
sdr.start_tx(channels=(0, 1))
sdr.write(np.stack((a, b)))                       # rows go out sample for sample together
```
Samples the hardware lost before they reached pysdrlib (an overflow) show as gaps in `read(..., meta=True)`, on every
channel alike, so they stay aligned.

### Remote devices
Host SDRs on one machine and use them from another (`pip install pysdrlib[remote]`, uses ZeroMQ).

```
pysdrlib server --bind tcp://0.0.0.0:5555 --port-range 6000-6100
pysdrlib client tcp://host:5555 list
pysdrlib client tcp://host:5555 rx hackrf --freq 100e6 --rate 10e6 -t 5 -o out.cfile
pysdrlib client tcp://host:5555 tx hackrf -i out.cfile --freq 915e6 --rate 2e6 --tx-gain 0 --tx-wire int8
```
```python
sdr = pysdrlib.device("remote", "tcp://host:5555", "hackrf")  # same API as a local device
sdr.open(); sdr.initialize(cf=100e6, Fs=10e6); sdr.start_rx()
samples = sdr.read(65536)
```
The controls above are forwarded to the hosted device and `sdr.caps` comes from it. Channels work as they do locally:
the server publishes every channel the device streams (`sdr.start_rx(channels=(0, 1))`, then `sdr.read(n, channel=(0, 1))`
on the controller and on viewers alike, aligned, with gaps), and the controller can transmit at the same time
(`sdr.start_tx(channels=...)`, `sdr.write(...)`: the server writes it to the device, and a full device queue holds the
client back). Only the controller transmits. One client controls a device; any number can attach as viewers (`pysdrlib.device("remote", addr, id="hackrf-0")`) to receive samples.

### Reading and writing sample files
```python
from pysdrlib import File, FileSink
with File("capture.cfile", "cf32") as f:           # File(path, fmt); set_path/set_fmt also work
    for block in f.forward(8192, partial=True):    # partial=True also yields the short last block
        print(f.percent())                         # fraction read, 0.0-1.0 (percent(fraction=False): 0-100)
    f.seek(0); tail = f.next(8192, partial=True)   # next() raises Overflow at EOF unless partial=True

with FileSink("out.cs16", "ci32t", clip=True) as out:   # or FileSink(..., open=True) and close() yourself
    out.write(samples)
```
- `File.next(count, partial=False)`, `forward(count, partial=False)` and `File(...)(count, partial=False)`: `partial=True` returns the remaining samples at the end of the file (an empty array once at EOF) instead of raising or stopping early.
- `FileSink(path, fmt, append=False, open=False, clip=False)` truncates an existing file unless `append=True`; the format of an existing file is **not** checked when appending. `write()` takes 1-D samples (other shapes raise `ValueError`; flatten or write per channel) and needs the sink opened (`open()`, `open=True`, or `with`).
- `clip=True` saturates values outside the format's range (per I/Q component) and logs one warning, "N samples clipped", when the sink closes; `sink.clipped` holds the running count. NaN is stored as 0 in integer formats and counted; infinity saturates. Without `clip`, any value outside the format's range (including NaN/inf in integer formats) raises `ValueError`; nothing wraps.
- Neither class scales: integer formats are read and written as stored, not divided by full scale.

### Stalls and dropped samples
Hardware devices watch their own stream each time you call `get_samples()`:
```python
try:
    samples = sdr.get_samples()
except pysdrlib.err.StreamStalled:   # no samples for sdr.stall_timeout seconds (default 1, None disables)
    sdr.stop_rx(); sdr.start_rx()    # or close and reopen the device
print(sdr.stats)  # samples delivered, dropped, missing, stalls, and seconds since the last samples
```
Samples lost because `get_samples()` wasn't called often enough, or because the device delivered fewer than its sample rate requires, raise a `pysdrlib.warn.Dropped` warning. Anything still buffered when a stall is detected is returned first.

# Contributing
1. `git clone https://github.com/Anonoei/pysdrlib`
2. `cd pysdrlib`
3. `git branch -c feature/<your feature>`
4. `python3 builder.py -b -l` build and install locally

## Adding a device
`python -m pysdrlib.scaffold my_radio` writes the skeleton of a driver (`--python-only` for one without a C++ extension),
`pysdrlib.testing.check_package("my_radio")` checks it as you fill it in, and the
[contributing guide](https://anonoei.github.io/pysdrlib/contributing.html) (`src/pysdrlib/contributing.py`) walks through the rest.
