import sys
import subprocess
import ctypes
import ctypes.util

from .hackrf import HackRF
Device = HackRF

from .lib.hackrf import libhackrf

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
    path = lib_path()
    return libhackrf(path[1])
