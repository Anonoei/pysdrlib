"""Build and load helpers for the bladeRF extension.

The extension is compiled WITHOUT linking libbladeRF (only the vendored libbladeRF.h is needed), so packaging never builds or
needs the library and wheels build anywhere. libbladeRF is found or built on the user's machine when `pysdrlib build bladerf`
is run (or, with PYSDRLIB_BUILD=auto, the first time `pysdrlib.bladerf` is imported); see `utils.VendorLibrary`. The bundled
copy is a trimmed snapshot of bladeRF's sources (see tools/vendor.py); building it takes under a minute and needs cmake, a C
compiler, `patch` and libusb. It needs no network and no Python.
"""
from . import utils

VENDOR = "src/pysdrlib/bladerf/vendor"   # relative to the repository root, for setup.py

LIB = utils.VendorLibrary(
    driver="bladerf",
    name="bladeRF",
    source=utils.PATH_MOD / "bladerf" / "vendor",   # the snapshot's CMakeLists.txt
    files=("libbladeRF.so.2", "libbladeRF.so", "libbladeRF.2.dylib", "libbladeRF.dylib", "bladeRF.dll"),
    build_globs=("libbladeRF.so*", "libbladeRF*.dylib", "bladeRF.dll"),
    missing_hints=("libbladerf", "bladerf_"),
    help=("Install libbladeRF from your package manager (e.g. `apt install libbladerf2`), or install cmake, a C compiler, "
          "`patch` and the libusb-1.0 development files (e.g. `apt install cmake build-essential patch libusb-1.0-0-dev`) "
          "so pysdrlib can build its bundled copy."),
)

mk_lib = LIB.build
load_library = LIB.load

def get():
    return utils.pybind_extension("bladerf", f"{VENDOR}/host/libraries/libbladeRF/include", "libbladeRF.h")
