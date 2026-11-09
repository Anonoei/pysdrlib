"""
pysdrlib

wrapper for hardware SDRs

Github: https://github.com/Anonoei/pysdrlib

PyPI: https://pypi.org/project/pysdrlib/
"""

__version__ = "0.3.1"
__author__ = "Anonoei <to+dev@an0.us>"

from . import err
from . import warn
from .base.formats import Formats
from .base.device import Device
from . import devices
from .file import File, FileSink

from .devices import device, find, open, DeviceInfo
from .base.caps import Capabilities, Range, Setting
from .base.stream import RxMeta, Event
