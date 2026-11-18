"""Build helper for the TCP IQ extension.

It talks to the operating system's sockets and nothing else, so unlike the radios' extensions there is no vendor
library to find or build (`LIB` is None): the extension is complete once compiled, on every platform.
"""
import sys

from . import utils

LIB = None   # no vendor library: `pysdrlib build` has nothing to do for it


def get():
    return utils.pybind_extension("tcpiq", None, None, libraries=["ws2_32"] if sys.platform == "win32" else [])
