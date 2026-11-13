"""Build and load helpers for the UHD (USRP) extension.

The extension uses UHD's C API and is compiled WITHOUT linking libuhd (only the vendored uhd.h is needed), so
packaging never builds or needs the library and wheels build anywhere. libuhd is found or built on the user's
machine when `pysdrlib build uhd` is run (or, with PYSDRLIB_BUILD=auto, the first time
`pysdrlib.uhd` is imported); see `utils.VendorLibrary`. The bundled copy is a trimmed snapshot of
UHD's sources (see tools/vendor.py), built for the B2xx family only (B200/B210/B200mini). Building it takes a minute or
more and needs cmake, a C++17 compiler, Boost and libusb; it needs no network and no Python.
"""
import os

from . import utils

# libuhd prints its own "[INFO] [B200] ..." lines to the console. It reads this when it loads, so keep it to warnings and
# errors unless the user chose a level (the variable, or `pysdrlib server --uhd-log`)
os.environ.setdefault("UHD_LOG_CONSOLE_LEVEL", "warning")

VENDOR = "src/pysdrlib/uhd/vendor"   # relative to the repository root, for setup.py

LIB = utils.VendorLibrary(
    driver="uhd", note="takes a minute or more",
    name="uhd",
    source=utils.PATH_MOD / "uhd" / "vendor",   # the snapshot's CMakeLists.txt (it includes sources.cmake)
    files=("libuhd.so.4", "libuhd.so", "libuhd.4.dylib", "libuhd.dylib", "uhd.dll"),
    build_globs=("libuhd.so*", "libuhd*.dylib", "uhd.dll"),
    missing_hints=("libuhd", "uhd_"),
    help=("Install UHD from your package manager (e.g. `apt install libuhd-dev`), or install cmake, a C++17 "
          "compiler, Boost and the libusb-1.0 development files (e.g. `apt install cmake build-essential "
          "libboost-all-dev libusb-1.0-0-dev`) so pysdrlib can build its bundled copy."),
)

mk_lib = LIB.build
load_library = LIB.load

def get():
    # uhd.h includes uhd/version.hpp, which UHD's build generates: the snapshot keeps that copy under generated/
    return utils.pybind_extension("uhd", f"{VENDOR}/src/host/include", "uhd.h", extra_sources=("uhd_shim.c",),
                                  include_dirs=[f"{VENDOR}/generated/include"])
