"""Build and load helpers for the HackRF extension.

The extension is compiled WITHOUT linking libhackrf (only the vendored hackrf.h is needed), so
packaging never builds or needs the library and wheels build anywhere. libhackrf is found or built on
the user's machine when `pysdrlib build hackrf` is run (or, with PYSDRLIB_BUILD=auto, the first time
`pysdrlib.hackrf` is imported); see `utils.VendorLibrary`.
"""
from . import utils

LIB = utils.VendorLibrary(
    driver="hackrf",
    name="hackrf",
    source=utils.PATH_MOD / "hackrf" / "vendor" / "libhackrf",   # vendored libhackrf (CMake project)
    files=("libhackrf.so.0", "libhackrf.so", "libhackrf.0.dylib", "libhackrf.dylib", "hackrf.dll"),
    build_globs=("libhackrf.so*", "libhackrf*.dylib", "hackrf.dll"),
    missing_hints=("libhackrf", "hackrf_"),
    help=("Install libhackrf from your package manager (e.g. `apt install libhackrf0`), or install "
          "cmake and the libusb-1.0 development files (e.g. `apt install cmake libusb-1.0-0-dev`) "
          "so pysdrlib can build its bundled copy."),
)

mk_lib = LIB.build
load_library = LIB.load

def get():
    return utils.pybind_extension("hackrf", "src/pysdrlib/hackrf/vendor/libhackrf/src", "hackrf.h")
