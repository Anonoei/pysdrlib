"""Build and load helpers for the UHD (USRP) extension.

The extension uses UHD's C API and is compiled WITHOUT linking libuhd (only the vendored uhd.h is needed), so
packaging never builds or needs the library and wheels build anywhere. libuhd is found or built on the user's
machine the first time `pysdrlib.uhd` is imported; see `utils.VendorLibrary`. The bundled copy is configured for
the B2xx family only (B200/B210/B200mini); building it takes several minutes and needs cmake, a C++17
compiler, Boost and libusb.
"""
from . import utils

LIB = utils.VendorLibrary(
    name="uhd",
    source=utils.PATH_MOD / "uhd" / "vendor",   # CMake wrapper around the vendored EttusResearch/uhd
    files=("libuhd.so.4", "libuhd.so", "libuhd.4.dylib", "libuhd.dylib", "uhd.dll"),
    build_globs=("libuhd.so*", "libuhd*.dylib", "uhd.dll"),
    missing_hints=("libuhd", "uhd_"),
    help=("Install UHD from your package manager (e.g. `apt install libuhd-dev`), or install cmake, a C++17 "
          "compiler, Boost and the libusb-1.0 development files (e.g. `apt install cmake build-essential "
          "libboost-all-dev libusb-1.0-0-dev`) so pysdrlib can build its bundled copy (several minutes)."),
)

mk_lib = LIB.build
load_library = LIB.load

def get():
    return utils.pybind_extension("uhd", "src/pysdrlib/uhd/vendor/uhd/host/include", "uhd.h",
                                  extra_sources=("uhd_shim.c",))
