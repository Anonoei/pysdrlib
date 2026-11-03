"""Build and load helpers for the RX888 (ExtIO_sddc) extension.

Like HackRF, the extension is compiled WITHOUT linking the vendor library, so packaging never builds
it. `libsddc_core` (ExtIO_sddc's Core, built as a shared library) is found or built on the user's
machine the first time `pysdrlib.extio` is imported; see `utils.VendorLibrary`.
"""
from . import utils

LIB = utils.VendorLibrary(
    name="sddc_core",
    source=utils.PATH_MOD / "extio" / "vendor",   # CMake project wrapping the vendored ExtIO_sddc/Core
    files=("libsddc_core.so", "libsddc_core.dylib"),
    missing_hints=("sddc",),
    help=("Install cmake, a C++17 compiler and the libusb-1.0 and fftw3f development files (e.g. "
          "`apt install cmake libusb-1.0-0-dev libfftw3-dev`) so pysdrlib can build its bundled copy."),
)

mk_lib = LIB.build
load_library = LIB.load

def get():
    return utils.pybind_extension("extio", "src/pysdrlib/extio/vendor/ExtIO_sddc/Core", "RadioHandler.h")
