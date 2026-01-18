"""
pysdrlib

wrapper for hardware SDRs

Github: https://github.com/Anonoei/pysdrlib

PyPI: https://pypi.org/project/pysdrlib/
"""

__version__ = "0.1.2"
__author__ = "Anonoei <to+dev@an0.cx>"

from . import err
from . import warn
from .base.formats import Formats
from .base.device import Device
from . import devices
from .file import File
