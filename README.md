# pysdrlib
Python wrappers for hardware SDRs: every radio has the same API, and samples are always `complex64`.

- [Documentation](https://anonoei.github.io/pysdrlib/): guides, examples and the API reference
- [PyPI](https://pypi.org/project/pysdrlib/)
- [AGENTS.md](https://github.com/Anonoei/pysdrlib/blob/main/AGENTS.md), [llms.txt](https://anonoei.github.io/pysdrlib/llms.txt): orientation for coding agents

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
| [RTL-SDR](https://anonoei.github.io/pysdrlib/api/rtl_sdr/) | `rtl_sdr` | |
| [HackRF](https://anonoei.github.io/pysdrlib/api/hackrf/) | `hackrf` | Transmit (half-duplex) |
| [USRP B210 (UHD)](https://anonoei.github.io/pysdrlib/api/uhd/) | `uhd` | 2 RX and 2 TX channels |
| [USRP X310, X410 (UHD)](https://anonoei.github.io/pysdrlib/api/uhd/#x310-and-x410) | `x310`, `x410` | Experimental, untested on hardware; on a network (`find(addresses=...)`), needs a system libuhd |
| [RX-888 (ExtIO)](https://anonoei.github.io/pysdrlib/api/extio/) | `extio` | |
| [BladeRF 2.0 micro](https://anonoei.github.io/pysdrlib/api/bladerf/) | `bladerf` | 2 RX and 2 TX channels; tested on a 2.0 micro xA4 (receive), transmit checked for plumbing only |
| [TCP IQ stream](https://anonoei.github.io/pysdrlib/api/tcpiq/) | `tcpiq` | Receive raw I/Q over TCP, as the client (`host=`, `port=`) or the server (`listen=True`); not listed by `find()` |

Also: [`sim`](https://anonoei.github.io/pysdrlib/api/sim/), a simulated radio that needs no hardware, [`file`](https://anonoei.github.io/pysdrlib/guide/files/), which plays a
recording back as a radio, and [`shm`](https://anonoei.github.io/pysdrlib/api/shm/), which receives a stream another process
publishes in shared memory (`ShmStream`). A radio needs its vendor's library: `pysdrlib build` builds the bundled copy
([Installing](https://anonoei.github.io/pysdrlib/guide/installing/)).

## Quickstart

```python
import pysdrlib

for info in pysdrlib.find(virtual=True):    # radios attached (virtual=True: the simulated one too)
    print(info.driver, info.label, info.serial)

sdr = pysdrlib.open("sim")                 # a driver name; or a DeviceInfo from find(), or "<driver>@<serial>"
sdr.initialize(cf=100e6, Fs=2e6)           # center frequency and sample rate
sdr.set_rx_gain(0.5)                       # half of this radio's gain range, whatever the radio
sdr.start_rx()
x = sdr.read(65536, timeout=1.0)           # exactly 65536 complex64 samples (err.Timeout after 1 s)
sdr.stop_rx()
sdr.close()
```

## Guides

- [Installing](https://anonoei.github.io/pysdrlib/guide/installing/): vendor libraries, build dependencies, udev rules
- [Receiving](https://anonoei.github.io/pysdrlib/guide/receiving/): `read` and its metadata, streams, sweeps, retunes, lost samples
- [Controls and channels](https://anonoei.github.io/pysdrlib/guide/controls/): capabilities, gain, settings, multi-channel, transmit
- [Sample files](https://anonoei.github.io/pysdrlib/guide/files/): raw formats, SigMF, a recording played back as a radio
- [Remote radios](https://anonoei.github.io/pysdrlib/guide/remote/): use a radio on another machine, or share one between programs
- [Drivers of your own](https://anonoei.github.io/pysdrlib/guide/custom-drivers/): a new radio without changing pysdrlib
- [Contributing](https://anonoei.github.io/pysdrlib/contributing/), [Roadmap](https://anonoei.github.io/pysdrlib/roadmap/), [Changelog](https://anonoei.github.io/pysdrlib/changelog/)

## License

Copyright (C) 2025-2026 Anonoei and contributors. pysdrlib is free software: you can redistribute it and/or modify it
under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of
the License, or (at your option) any later version. See [LICENSE](LICENSE). Releases up to 0.4.6 were MIT-licensed.
