import sys
import subprocess
import ctypes
import ctypes.util

from .hackrf import HackRF
Device = HackRF

from .lib import lib_load

def lib_path():
    _lib_path = None
    if sys.platform == "linux":
        libs = subprocess.check_output(["ldconfig", "-p"])
        paths = []
        for line in libs.splitlines():
            if b"libhackrf" in line:
                path = line.decode("utf-8").split("=>")[1][1:]
                paths.append(path)
        _lib_path = paths
    return _lib_path

def lib_init():
    lib_load(lib_path()[1])
    # from .lib.hackrf_info import hackrf_info
    # hackrf_info()
    from .lib.hackrf_transfer import hackrf_transfer
    hackrf_transfer()
