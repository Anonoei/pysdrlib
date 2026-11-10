# pysdrlib
Python wrappers for hardware SDRs: every radio has the same API, and samples are always `complex64`.

- [Documentation](https://anonoei.github.io/pysdrlib/)
- [PyPI](https://pypi.org/project/pysdrlib/)

```
pip install pysdrlib
pip install pysdrlib[remote]     # also use radios on another machine (ZeroMQ)
```

This is a lightweight backend for my [pyspecan](https://github.com/Anonoei/pyspecan) and [RPPS](https://github.com/Anonoei/RPPS)
projects, with minimal user intervention. It has no affiliation to the fantastic [pysdr](https://pysdr.org) page.
For a more robust C implementation with far more hardware, use [SoapySDR](https://github.com/pothosware/SoapySDR).

## Supported devices

| Device | Driver | Notes |
| --- | --- | --- |
| [RTL-SDR](https://github.com/Anonoei/pysdrlib/tree/main/src/pysdrlib/rtl_sdr) | `rtl_sdr` | |
| [HackRF](https://github.com/Anonoei/pysdrlib/tree/main/src/pysdrlib/hackrf) | `hackrf` | Transmit (half-duplex) |
| [USRP B210 (UHD)](https://github.com/Anonoei/pysdrlib/tree/main/src/pysdrlib/uhd) | `uhd` | 2 RX and 2 TX channels |
| [RX-888 (ExtIO)](https://github.com/Anonoei/pysdrlib/tree/main/src/pysdrlib/extio) | `extio` | |
| [BladeRF](https://github.com/Anonoei/pysdrlib/tree/main/src/pysdrlib/bladerf) | `bladerf` | Planned |

Also: `sim`, a simulated radio that needs no hardware, and [`File`](#sample-files), which reads recordings the way a
device reads samples.

## Usage

```python
import pysdrlib

for info in pysdrlib.find():             # radios attached, for every driver whose library is installed or built
    print(info.driver, info.label, info.serial)

sdr = pysdrlib.open(pysdrlib.find()[0])  # opened; or pysdrlib.open("<driver>", serial="...")
sdr.initialize(cf=100e6, Fs=2e6)
sdr.set_rx_gain(0.5)               # half of this radio's gain range, whatever the radio
sdr.start_rx()
x = sdr.read(65536, timeout=1.0)         # exactly 65536 samples (err.Timeout after 1 s)
```
Or pick the driver yourself:
```python
sdr = pysdrlib.device("rtl_sdr")         # not opened yet: call sdr.open(); pysdrlib.devices.ls() lists the drivers
import pysdrlib.rtl_sdr                  # static import; pysdrlib.rtl_sdr.Device()
```
`find()` never builds a vendor library (`find(build=True)` allows it); `find(virtual=True)` also lists the simulated device.

### Reading
`read` and `stream` are the way to get samples: an exact count, waiting for it, with the loss and retune information of
the stream. (`get_samples()` is the low-level call under them: whatever has arrived, any number, without waiting.)
```python
x, m = sdr.read(65536, meta=True)        # m.index: the first sample's position in the stream; m.gaps / m.dropped:
                                         # where samples were lost; m.events: retunes inside the block; m.time
sdr.read_into(buffer)                    # into an array you own

with sdr.stream(65536, meta=True) as s:  # starts receiving, stops on exit
    for x, m in s: ...

sdr.set_rx_freq(162.4e6, flush=True)     # the next read is all at the new frequency (settling skipped)
```
`sdr.on_drop` decides what lost samples do: `"warn"` (default), `"raise"`, `"ignore"` or a function.

### Controls
Every device describes itself in `sdr.caps` (refined for the attached unit once open), and the same calls work on all of
them. A control a device doesn't have raises `err.NotSupported`.
```python
sdr.caps.freq, sdr.caps.rates                    # [Range]: tunable and sample-rate ranges (lo == hi: one discrete value)
sdr.rx_gain_stages()                                      # {stage: GainStage}: the device's own gain stages, by its names
sdr.set_rx_gain(0.6)                             # gain as a fraction of the device's range (0 least, 1 most): the portable call
sdr.set_rx_gain_db(40)                           # or in dB: a total (the device splits it), or set_rx_gain_db(16, stage="...") one;
                                                 # dB mean different things on different radios, so prefer the fraction
sdr.get_rx_gain(), sdr.get_rx_gain_db()          # readback, as a fraction and in dB (sdr.get_rx_gains_db(): by stage)
sdr.set_rx_gain_mode("auto")                        # automatic gain where the device has it (sdr.rx_gain_modes())
sdr.set_rx_bandwidth(1.5e6)                         # analog filter (sdr.caps.bandwidths)
sdr.set_freq_correction(14.8)                    # ppm error of the reference: tuning (and continuous rates) corrected
sdr.settings(); sdr.set_setting("bias_t", True)  # everything else the device has
sdr.rx_antennas(), sdr.clock_sources()              # where the device has them
sdr.strict = True                                # out-of-range values raise err.InvalidValue instead of being clamped
sdr.dc_correction = True                         # remove the DC offset from what read() returns
```
Every control that differs between receiving and transmitting has a call for each: `set_rx_freq`/`set_tx_freq`,
`set_rx_gain`/`set_tx_gain`, `set_rx_bandwidth`/`set_tx_bandwidth`, `set_rx_antenna`/`set_tx_antenna`...
There is no `direction=` argument anywhere. Each takes `channel=0` (the count is in `sdr.caps.channels`).
Devices also keep their own per-stage setters (`set_rx_lna_gain`, ...) alongside the generic ones.

### Channels
A device with several channels (`sdr.caps.channels`, e.g. a B210's 2 RX and 2 TX) streams any of them together:
```python
sdr.set_rx_gain_db(40, channel=1)                 # every control takes the channel
sdr.set_tx_freq(915e6)                            # tuning is per direction: set_rx_freq / set_tx_freq
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

### Stalls and dropped samples
Hardware devices watch their own stream each time you `read` (or call `get_samples()`):
```python
try:
    samples = sdr.read(65536, timeout=1.0)
except pysdrlib.err.StreamStalled:   # no samples for sdr.stall_timeout seconds (default 1, None disables)
    sdr.stop_rx(); sdr.start_rx()    # or close and reopen the device
print(sdr.stats)  # samples delivered, dropped, missing, stalls, and seconds since the last samples
```
Samples lost because you didn't read often enough, or because the device delivered fewer than its sample
rate requires, raise a `pysdrlib.warn.Dropped` warning. Anything still buffered when a stall is detected is returned first.

### Sample files
```python
from pysdrlib import File, FileSink
with File("capture.cfile", "cf64") as f:     # cf64: two float32 per sample, a GNU Radio .cfile
    for block in f.forward(8192, partial=True):
        ...
with FileSink("out.cs16", "ci32t", clip=True) as out:
    out.write(samples)
```
See the [file README](https://github.com/Anonoei/pysdrlib/tree/main/src/pysdrlib/file) for the details: partial blocks,
seeking, appending and clipping.

### Remote devices
Host SDRs on one machine and use them from another (`pip install pysdrlib[remote]`).
```
pysdrlib server --bind tcp://0.0.0.0:5555
pysdrlib client tcp://host:5555 rx hackrf --freq 100e6 --rate 10e6 -t 5 -o out.cfile
```
```python
sdr = pysdrlib.connect("tcp://host:5555", "hackrf")          # same API as a local device (opened, and yours to control)
sdr.initialize(cf=100e6, Fs=10e6); sdr.start_rx()
samples = sdr.read(65536)
```
Receiving, transmitting and channels all work; one client controls a device and any number can watch it. There is no
authentication or encryption, so only serve a network you trust. See the
[remote README](https://github.com/Anonoei/pysdrlib/tree/main/src/pysdrlib/remote) for the command line, ownership
and wire formats.

## Building from source
Each driver needs its vendor's library (libhackrf, libuhd...).
Use the one your package manager has, or build the copy that
comes with pysdrlib:

```
pysdrlib build              # where each driver's library is: installed, built before, or missing
pysdrlib build hackrf       # build one
pysdrlib build --all        # build every one that is missing
```
Importing a driver never builds anything by itself: without its library it fails with a message that says to run the command
above. (`PYSDRLIB_BUILD=auto` in the environment restores building on first use, and `find(build=True)` builds what is missing.)
Building needs `cmake`, a C++17 compiler, `pkg-config` and the development files below, and installs nothing system-wide: it
is built out of the source tree into a per-user cache.
Tests for a device whose library isn't there are skipped.

Prebuilt `librtlsdr` / `libhackrf` / `libextio_sdcc` / `libuhd` packages can be installed instead of building them.

Install the build tools and `libusb`, plus **fftw** for the RX-888 or **boost** for UHD:

| System | Command |
| --- | --- |
| Debian/Ubuntu | `sudo apt install build-essential pkg-config cmake libusb-1.0-0-dev` + `libfftw3-dev` (RX-888) / `libboost-all-dev` (UHD) |
| Fedora/RHEL | `sudo dnf install gcc-c++ cmake pkgconf-pkg-config libusb1-devel` + `fftw-devel` / `boost-devel` |
| Arch | `sudo pacman -S base-devel cmake libusb` + `fftw` / `boost` |
| openSUSE | `sudo zypper install gcc-c++ cmake pkg-config libusb-1_0-devel` + `fftw3-devel` / `boost-devel` |
| Alpine | `apk add build-base cmake pkgconf libusb-dev` + `fftw-dev` / `boost-dev` |
| macOS (Homebrew) | `brew install cmake pkg-config libusb` + `fftw` / `boost` |

A B2xx also loads an FPGA image when opened: install UHD's images (`uhd_images_downloader -t b2xx`, or
`apt install uhd-host`) or point `UHD_IMAGES_DIR` at yours (an image already in `/usr/share/uhd/images` is used as is;
pysdrlib never downloads one itself).

## Contributing
```
git clone --recurse-submodules https://github.com/Anonoei/pysdrlib
cd pysdrlib
git switch -c feature/<your feature>
python3 builder.py -b -l                  # build and install locally
pytest                                    # runs without hardware; PYSDRLIB_HW=1 adds the hardware tests
```

### Adding a device
`python -m pysdrlib.scaffold my_radio` writes the skeleton of a driver (`--python-only` for one without a C++ extension),
`pysdrlib.testing.check_package("my_radio")` checks it as you fill it in, and the
[contributing guide](https://anonoei.github.io/pysdrlib/contributing.html) (`src/pysdrlib/contributing.py`)
walks through the rest.

### Documentation
The docs are built with [pdoc3](https://pdoc3.github.io/pdoc/) (dark theme, from `pdoc_templates/`):
```
pip install pdoc3
python -m pdoc --html --force --template-dir pdoc_templates -o docs pysdrlib
```
and published to GitHub Pages on every push to `main`.
