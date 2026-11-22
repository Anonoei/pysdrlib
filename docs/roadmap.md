# Roadmap

pysdrlib is meant to be a lighter, narrower SoapySDR: each driver wraps its radio's own (vendored) library, and there
is no generic bridge. For far more hardware from C, use [SoapySDR](https://github.com/pothosware/SoapySDR). USB radios
come first; network and PCIe radios are opt-in, and the network is probed only when `find(addresses=...)` asks.

Radios, in the order they are planned:

1. **ADALM-Pluto** (libiio)
2. **LimeSDR** (LimeSuite)
3. **USRP X310 / X410**, through the existing `uhd` driver. Those drivers exist but are experimental: they have not
   been run on a radio yet.
4. **A low-cost FPGA radio** (2 × 50 MS/s RX and TX over USB 3 or PCIe): the protocol first, with the gateware in a
   separate repository.

Also open: the bladeRF 1.0 (x40, x115), and transmit on the bladeRF 2.0 checked on the air (so far only the plumbing
is tested).

A driver for any of these can also live outside pysdrlib: see [Drivers of your own](guide/custom-drivers.md).
