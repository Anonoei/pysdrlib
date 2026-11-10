"""Build and load helpers for the RTL-SDR extension.

Like HackRF, the extension is compiled WITHOUT linking librtlsdr (only the vendored rtl-sdr.h is needed),
so packaging never builds the library. librtlsdr is found or built on the user's machine (`pysdrlib build rtl_sdr`)
when `pysdrlib.rtl_sdr` is first needed; see `utils.VendorLibrary`.
"""
from . import utils

LIB = utils.VendorLibrary(
    driver="rtl_sdr",
    name="rtlsdr",
    source=utils.PATH_MOD / "rtl_sdr" / "vendor",   # CMake project wrapping the vendored librtlsdr
    files=("librtlsdr.so.0", "librtlsdr.so", "librtlsdr.0.dylib", "librtlsdr.dylib", "rtlsdr.dll"),
    build_globs=("librtlsdr.so*", "librtlsdr*.dylib", "rtlsdr.dll"),
    missing_hints=("librtlsdr", "rtlsdr_"),
    help=("Install librtlsdr from your package manager (e.g. `apt install librtlsdr0`), or install "
          "cmake and the libusb-1.0 development files (e.g. `apt install cmake libusb-1.0-0-dev`) "
          "so pysdrlib can build its bundled copy."),
)

mk_lib = LIB.build
load_library = LIB.load

def get():
    return utils.pybind_extension("rtl_sdr", "src/pysdrlib/rtl_sdr/vendor/librtlsdr/include", "rtl-sdr.h")
