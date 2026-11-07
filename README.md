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

# Roadmap
- [X] [RTL-SDR](https://github.com/Anonoei/pysdrlib/tree/main/src/pysdrlib/rtl_sdr)
- [X] [Hack RF](https://github.com/Anonoei/pysdrlib/tree/main/src/pysdrlib/hackrf)
- [ ] [Blade RF](https://github.com/Anonoei/pysdrlib/tree/main/src/pysdrlib/bladerf)
- [ ] [B210 (UHD)](https://github.com/Anonoei/pysdrlib/tree/main/src/pysdrlib/UHD)
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

### Remote devices
Host SDRs on one machine and use them from another (`pip install pysdrlib[remote]`, uses ZeroMQ).

```
pysdrlib server --bind tcp://0.0.0.0:5555 --port-range 6000-6100
pysdrlib client tcp://host:5555 list
pysdrlib client tcp://host:5555 rx hackrf --freq 100e6 --rate 10e6 -t 5 -o out.cfile
```
```python
sdr = pysdrlib.device("remote", "tcp://host:5555", "hackrf")  # same API as a local device
sdr.open(); sdr.initialize(cf=100e6, Fs=10e6); sdr.start_rx()
samples = sdr.get_samples()
```
One client controls a device; any number can attach as viewers (`pysdrlib.device("remote", addr, id="hackrf-0")`) to receive samples.

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
- `FileSink(path, fmt, append=False, open=False, clip=False)` truncates an existing file unless `append=True`; the format of an existing file is **not** checked when appending. `write()` needs the sink opened (`open()`, `open=True`, or `with`).
- `clip=True` saturates values outside the format's range (per I/Q component) and logs one warning, "N samples clipped", when the sink closes; `sink.clipped` holds the running count. Without it, out-of-range values are not checked for plain integer formats (they wrap) and raise `ValueError` for 4-bit and one's-complement formats.
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
