# RX-888

- Stand-alone implementation: [ExtIO_sddc](https://github.com/ik1xpv/ExtIO_sddc)
- Wraps ExtIO_sddc's `Core` (`RadioHandlerClass`), which also drives the BBRF103, RX888 mkII/mkIII, RX999 and HF103.
  Only the RX888 has been tested.

## Specs
- Frequency (10 kHz - 1.75 GHz)
  - Below half the ADC rate (32 MHz) the ADC samples the HF input directly; above it the VHF/UHF tuner is used
- Sample rate (2, 4, 8, 16, 32 MHz): 64 MHz ADC, decimated by the driver's DDC. Other rates snap to the nearest
- Samples (complex float32, from the ADC's 16 bit samples)
- Gain (dB, in model/input dependent steps; the driver snaps to the nearest)
  - RF: HF attenuator (-31.5 - 0) / tuner RF gain (0 - 49.6)
  - IF: HF VGA / tuner IF gain (-4.7 - 40.8). The original RX888 has no IF stage on HF
  - `set_rx_gain` puts what the IF stage can't supply on the RF stage
- Bias-T (active input), ADC dither, ADC randomizer, PGA

## Setup
The first time `pysdrlib.extio` is imported it looks for `libsddc_core` on the system, then in the cache, and otherwise builds from `vendor/ExtIO_sddc` (needs `cmake`, a C++17 compiler and the libusb-1.0 and fftw3f development files).

The RX888 boots as a bare USB bootloader and the driver uploads its firmware.
On Linux, add a udev rule (before `70-uaccess.rules`), and replug the device:
```/etc/udev/rules.d/60-rx888.rules
SUBSYSTEM=="usb", ATTRS{idVendor}=="04b4", ATTRS{idProduct}=="00f1|00f3", MODE="0660", TAG+="uaccess"
```

Run `sudo udevadm control --reload-rules` to reload udev rules
