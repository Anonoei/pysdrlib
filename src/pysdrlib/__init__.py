"""
# pysdrlib

Python wrappers for hardware SDRs

Github: https://github.com/Anonoei/pysdrlib

PyPI: https://pypi.org/project/pysdrlib/

## Quick start

    import pysdrlib

    for info in pysdrlib.find():                  # radios attached, for every driver whose library is available
        print(info.driver, info.label, info.serial)

    sdr = pysdrlib.open(pysdrlib.find()[0])       # opened; or pysdrlib.open("<driver name>")
    sdr.initialize(cf=100e6, Fs=2e6)
    sdr.set_rx_gain(0.5)                    # half of this radio's gain range, whatever the radio
    sdr.start_rx()
    samples = sdr.read(65536, timeout=1.0)        # exactly 65536 samples
    sdr.stop_rx()
    sdr.close()

## Where things are

| Module | What it is |
| --- | --- |
| `pysdrlib.devices` | Finding and opening devices: `pysdrlib.find`, `pysdrlib.open`, `pysdrlib.device`, `pysdrlib.connect` (one a server hosts), `pysdrlib.register` (a driver from outside pysdrlib) |
| `pysdrlib.base.device` | `pysdrlib.Device`, the API every driver implements (start here for method docs) |
| `pysdrlib.base.caps` | `pysdrlib.Capabilities`, `pysdrlib.Range` and `pysdrlib.Setting`: what a radio can do |
| `pysdrlib.<driver>`, one subpackage per supported radio | The hardware drivers |
| `pysdrlib.sim` | A simulated radio that needs no hardware |
| `pysdrlib.remote` | Use a radio on another machine over ZeroMQ (`pip install pysdrlib[remote]`) |
| `pysdrlib.file` | `pysdrlib.File` and `pysdrlib.FileSink`: read and write sample files |
| `pysdrlib.err`, `pysdrlib.warn` | Exceptions and warnings |
| `pysdrlib.contributing` | How to add a driver for a new radio |
"""

__version__ = "0.4.1"
__author__ = "Anonoei <to+dev@an0.us>"

from . import err
from . import warn
from .base.formats import Formats
from .base.device import Device
from . import devices
from .file import File, FileSink

from .devices import device, find, open, connect, register, DeviceInfo
from .base.caps import Capabilities, Range, Setting
from .base.stream import RxMeta, Event

__all__ = [
    "err", "warn", "devices",
    "Device", "Formats", "File", "FileSink",
    "device", "find", "open", "connect", "register", "DeviceInfo",
    "Capabilities", "Range", "Setting", "RxMeta", "Event",
]
