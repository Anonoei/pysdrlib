# Installing

```
pip install pysdrlib
pip install pysdrlib[remote]     # also use radios on another machine (ZeroMQ, and zeroconf to find servers)
```

Samples come out as `complex64` from every radio. A radio also needs its vendor's library (libhackrf, libuhd,
libbladeRF...): pysdrlib uses the one your system has, or builds the copy that ships with it.

## Vendor libraries

```
pysdrlib build              # where each driver's library is: installed, built before, or missing
pysdrlib build hackrf       # build one
pysdrlib build --all        # build every one that is missing
```

Importing a driver never builds anything by itself. Without its library it fails with a message that says to run the
command above. (`PYSDRLIB_BUILD=auto` in the environment restores building on first use, and `find(build=True)` builds
what is missing.) Building is done out of the source tree into a per-user cache and installs nothing system-wide.
`find()` only asks the drivers whose library is there.

Prebuilt `librtlsdr` / `libhackrf` / `libextio_sdcc` / `libuhd` / `libbladeRF` packages can be installed instead of
building them.

## Build dependencies

Building needs `cmake`, a C++17 compiler, `pkg-config` and `libusb`, plus **fftw** for the RX-888, **boost** for UHD or
`patch` for the bladeRF:

| System | Command |
| --- | --- |
| Debian/Ubuntu | `sudo apt install build-essential pkg-config cmake libusb-1.0-0-dev` + `libfftw3-dev` (RX-888) / `libboost-all-dev` (UHD) |
| Fedora/RHEL | `sudo dnf install gcc-c++ cmake pkgconf-pkg-config libusb1-devel` + `fftw-devel` / `boost-devel` |
| Arch | `sudo pacman -S base-devel cmake libusb` + `fftw` / `boost` |
| openSUSE | `sudo zypper install gcc-c++ cmake pkg-config libusb-1_0-devel` + `fftw3-devel` / `boost-devel` |
| Alpine | `apk add build-base cmake pkgconf libusb-dev` + `fftw-dev` / `boost-dev` |
| macOS (Homebrew) | `brew install cmake pkg-config libusb` + `fftw` / `boost` |

## Per radio

Each driver's page says what the radio needs beyond its library:

- [RTL-SDR](../api/rtl_sdr.md): a udev rule, and the kernel's DVB driver (the bundled build detaches it).
- [RX-888](../api/extio.md): a udev rule; the driver uploads the firmware.
- [USRP B2xx](../api/uhd.md): UHD's FPGA images (`uhd_images_downloader -t b2xx`, or `apt install uhd-host`, or
  `UHD_IMAGES_DIR`). pysdrlib never downloads one itself. The X310 and X410 on a network need a system libuhd.
- [HackRF](../api/hackrf.md), [bladeRF](../api/bladerf.md): the library only.
- [TCP IQ](../api/tcpiq.md), [sim](../api/sim.md), [file](../api/file.md): nothing.

## Checking it

```
pysdrlib build                                   # which libraries are there
python -c "import pysdrlib; print(pysdrlib.find())"
```

Then the [quickstart](receiving.md).
