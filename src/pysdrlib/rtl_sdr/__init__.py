"""RTL-SDR dongles through librtlsdr (receive only).

- How-to [pysdr RTL-SDR](https://pysdr.org/content/rtlsdr.html)
- Stand-alone implementation: [pyrtlsdr](https://github.com/pyrtlsdr/pyrtlsdr)
- Specs [rtl-sdr.com](https://www.rtl-sdr.com/about-rtl-sdr/)
- Wraps [librtlsdr](https://github.com/osmocom/rtl-sdr). Tested with an RTL-SDR Blog V4 (R828D tuner).

## Specs
- Frequency (24 MHz - 1.766 GHz): the R820T/R820T2/R828D tuner on nearly every current dongle. The older E4000 and FC00xx tuners cover other ranges
- Sample rate (225,001 - 300,000 Hz and 900,001 - 3.2 MHz): rates between the two ranges snap to the nearest edge. Over about 2.56 MHz a dongle on USB 2.0 usually drops samples
- Samples (complex uint8, offset binary, converted to complex64)
- Gain (dB): one tuner gain, `set_rx_rf_gain` (same as `set_rx_gain`), 0 - 49.6 in uneven steps; the driver snaps to the nearest.
  `set_agc(True)` lets the tuner choose instead
- Bias-T (RTL-SDR Blog V3 and V4 only), frequency correction in ppm (`set_freq_correction`)
- `open(serial_number=None, index=0)` picks a dongle when several are attached

## Setup
The first time `pysdrlib.rtl_sdr` is imported it looks for `librtlsdr` on the system, then in the cache, and otherwise builds from `vendor/rtl-sdr` (needs `cmake`, a C compiler and the libusb-1.0 development files).

The bundled build detaches the kernel's DVB driver (`dvb_usb_rtl28xxu`) while the dongle is in use, and reattaches it afterwards. A system `librtlsdr` built without that fails to open the dongle until you blacklist the module.
On Linux, add a udev rule (before `70-uaccess.rules`) if you don't have permission to use the dongle, and replug it:
```text title="/etc/udev/rules.d/60-rtlsdr.rules"
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838|2832", MODE="0660", TAG+="uaccess"
```

Run `sudo udevadm control --reload-rules` to reload udev rules
"""
from .._ext import rtl_sdr as ext, utils

RTLSDR = utils.import_device(__name__, ".rtlsdr", "RTLSDR", ext.LIB)
Device = RTLSDR

def find():
    """[DeviceInfo] of the RTL-SDR dongles attached"""
    from ..devices import DeviceInfo
    from . import lib
    found = []
    units = [lib.rtl_sdr.device_info(i) for i in range(lib.rtl_sdr.device_count())]
    serials = [u[3] for u in units]
    for i, (name, manufacturer, product, serial) in enumerate(units):
        # Many dongles share a serial (00000001): those are opened by their position instead
        args = {"serial": serial} if serial and serials.count(serial) == 1 else {"index": i}
        found.append(DeviceInfo("rtl_sdr", name or f"{manufacturer} {product}".strip(), serial or None, i, args,
                                {"manufacturer": manufacturer, "product": product}))
    return found
