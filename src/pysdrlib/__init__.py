"""pysdrlib: Python wrappers for hardware SDRs. Every radio has the same API, and samples are always `complex64`.

Guides (installing, receiving, controls, files, remote radios, drivers of your own): https://anonoei.github.io/pysdrlib/

| Module | What it is |
| --- | --- |
| `pysdrlib.devices` | Finding and opening devices: `pysdrlib.find`, `pysdrlib.open`, `pysdrlib.device`, `pysdrlib.connect` (one a server hosts), `pysdrlib.register` (a driver from outside pysdrlib) |
| `pysdrlib.targets` | `pysdrlib.discover`: radios here and on servers (`PYSDRLIB_SERVERS`), each with the target string `pysdrlib.open` takes |
| `pysdrlib.base.device` | `pysdrlib.Device`, the API every driver implements (start here for method docs) |
| `pysdrlib.base.caps` | `pysdrlib.Capabilities`, `pysdrlib.Range` and `pysdrlib.Setting`: what a radio can do |
| `pysdrlib.base.stream` | `pysdrlib.RxMeta` and `pysdrlib.Event`: what `read(..., meta=True)` says about a block; `pysdrlib.RxContext`: how a channel receives (`Device.get_rx_context`) |
| `pysdrlib.base.formats` | `pysdrlib.Formats`: sample formats on disk and on the wire |
| `pysdrlib.<driver>`, one subpackage per supported radio | The hardware drivers (`pysdrlib.devices.ls()` lists them) |
| `pysdrlib.sim` | A simulated radio that needs no hardware |
| `pysdrlib.file` | `pysdrlib.File` and `pysdrlib.FileSink`: read and write sample files; a recording played back as a radio |
| `pysdrlib.remote` | Use a radio on another machine over ZeroMQ (`pip install pysdrlib[remote]`) |
| `pysdrlib.err`, `pysdrlib.warn` | Exceptions and warnings |
| `pysdrlib.testing`, `pysdrlib.contributing`, `pysdrlib.scaffold` | Adding a driver for a new radio: the checks, the guide, the skeleton |
"""

__version__ = "0.5.1"
__author__ = "Anonoei <to+dev@an0.us>"

from . import err
from . import warn
from .base.formats import Formats
from .base.device import Device
from . import devices
from .file import File, FileSink

from .devices import device, find, open, connect, register, DeviceInfo
from .targets import discover, Radio
from .base.caps import Capabilities, Range, Setting
from .base.stream import RxMeta, RxContext, Event

__all__ = [
    "err", "warn", "devices",
    "Device", "Formats", "File", "FileSink",
    "device", "find", "open", "connect", "register", "DeviceInfo", "discover", "Radio",
    "Capabilities", "Range", "Setting", "RxMeta", "RxContext", "Event",
]
