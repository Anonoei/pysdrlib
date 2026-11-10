"""Build and load helpers for the UHD (USRP) extension.

The extension uses UHD's C API and is compiled WITHOUT linking libuhd (only the vendored uhd.h is needed), so
packaging never builds or needs the library and wheels build anywhere. libuhd is found or built on the user's
machine when `pysdrlib build uhd` is run (or, with PYSDRLIB_BUILD=auto, the first time
`pysdrlib.uhd` is imported); see `utils.VendorLibrary`. The bundled copy is configured for
the B2xx family only (B200/B210/B200mini); building it takes several minutes and needs cmake, a C++17
compiler, Boost and libusb.
"""
import re

from . import utils

GENERATED = utils.PATH_MOD / "_native" / "uhd" / "generated"   # holds uhd/version.hpp (not committed)

LIB = utils.VendorLibrary(
    driver="uhd", note="takes several minutes",
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

def generate_version_header():
    """Write `uhd/version.hpp` into `GENERATED` and return the directory to put on the include path.

    `uhd.h` includes `uhd/version.hpp`, which UHD's CMake generates from `version.hpp.in`, so a fresh checkout of the
    vendored sources doesn't have it (a machine with libuhd-dev installed would pick up the system's copy, hiding
    that). This fills in the template the way CMake does, from the version in `UHDVersion.cmake`.
    """
    host = utils.PATH_MOD / "uhd" / "vendor" / "uhd" / "host"
    cmake = (host / "cmake" / "Modules" / "UHDVersion.cmake").read_text()
    version = {}
    for part in ("MAJOR", "API", "ABI", "PATCH"):
        match = re.search(rf"^set\(UHD_VERSION_{part}\s+(\d+)\)", cmake, re.MULTILINE)
        if match is None:
            raise RuntimeError(f"can't read UHD_VERSION_{part} from {host / 'cmake' / 'Modules' / 'UHDVersion.cmake'}")
        version[f"UHD_VERSION_{part}"] = match.group(1)
    # UHD_VERSION_ADDED is MAJOR * 1000000 + API * 10000 + ABI * 100 + PATCH, as version.hpp.in documents
    version["UHD_VERSION_ADDED"] = str(int(version["UHD_VERSION_MAJOR"]) * 1000000 + int(version["UHD_VERSION_API"]) * 10000
                                       + int(version["UHD_VERSION_ABI"]) * 100 + int(version["UHD_VERSION_PATCH"]))
    text = (host / "include" / "uhd" / "version.hpp.in").read_text()
    text = re.sub(r"@(\w+)@", lambda m: version[m.group(1)], text)   # KeyError: a variable this doesn't know
    out = GENERATED / "uhd" / "version.hpp"
    out.parent.mkdir(parents=True, exist_ok=True)
    if not out.exists() or out.read_text() != text:
        out.write_text(text)
    return GENERATED

def get():
    generated = generate_version_header()
    return utils.pybind_extension("uhd", "src/pysdrlib/uhd/vendor/uhd/host/include", "uhd.h",
                                  extra_sources=("uhd_shim.c",), include_dirs=[str(generated)])
