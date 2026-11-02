"""Build and load helpers for the HackRF extension.

The extension is compiled WITHOUT linking libhackrf (only the vendored hackrf.h is needed), so
packaging never builds or needs the library and wheels build anywhere. libhackrf is resolved on
the user's machine, the first time `pysdrlib.hackrf` is imported (see `load_library`):

    1. the system library, if installed
    2. a copy previously built from the vendored sources
    3. otherwise build one now from the vendored sources (cmake, no network) and cache it
"""
import ctypes
import ctypes.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import utils

SOURCE = utils.PATH_MOD / "hackrf" / "_build" / "libhackrf"   # vendored libhackrf (CMake project)
CACHE_NAME = "hackrf"

# Repo-relative paths for setuptools (it runs from the project root)
SRC_DIR = "src/pysdrlib/hackrf/lib"
HEADER_DIR = "src/pysdrlib/hackrf/_build/libhackrf/src"
SHARED_DIR = "src/pysdrlib/_ext"

HELP = ("Install libhackrf from your package manager (e.g. `apt install libhackrf0`), or install "
        "cmake and the libusb-1.0 development files (e.g. `apt install cmake libusb-1.0-0-dev`) "
        "so pysdrlib can build its bundled copy.")


# ------------------------------------------------------------------ locating / loading
def user_cache() -> Path:
    """Per-user directory for the library pysdrlib builds (override with PYSDRLIB_CACHE)"""
    if os.environ.get("PYSDRLIB_CACHE"):
        return Path(os.environ["PYSDRLIB_CACHE"]) / CACHE_NAME
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "pysdrlib" / CACHE_NAME


def cache_dirs():
    """Where a built library may live: next to the package (dev checkouts), then the user cache"""
    return [utils.PATH_LIB / CACHE_NAME, user_cache()]


_PATTERNS = ("libhackrf.so.0", "libhackrf.so", "libhackrf.0.dylib", "libhackrf.dylib", "hackrf.dll")

def find_cached():
    """Path of a previously built libhackrf, or None"""
    for directory in cache_dirs():
        for pattern in _PATTERNS:
            if (directory / pattern).exists():
                return directory / pattern
    return None


def _load(path):
    # RTLD_GLOBAL so the extension's undefined hackrf_* symbols resolve against it
    ctypes.CDLL(str(path), mode=getattr(ctypes, "RTLD_GLOBAL", 0))


def load_library():
    """Make libhackrf's symbols available to the extension. Returns a description of what was loaded.
    Builds the bundled copy only if neither a system nor a cached library can be loaded.
    Raises RuntimeError (with instructions) if none works."""
    name = ctypes.util.find_library("hackrf")
    if name:
        try:
            _load(name)
            return f"system library {name}"
        except OSError:
            pass
    cached = find_cached()
    if cached is not None:
        try:
            _load(cached)
            return f"cached {cached}"
        except OSError:
            pass
    built = mk_lib()
    try:
        _load(built)
    except OSError as exc:
        raise RuntimeError(f"Built {built} but could not load it: {exc}. {HELP}") from exc
    return f"built {built}"


# ------------------------------------------------------------------ building (on the host, on demand)
def _writable(path: Path) -> bool:
    """True if `path` (or the nearest existing parent) can be written"""
    while not path.exists() and path != path.parent:
        path = path.parent
    return os.access(path, os.W_OK)


def mk_lib() -> Path:
    """Build libhackrf from the vendored sources (offline) and cache it. Returns the library path.

    The build happens in a temporary directory, so the package directory is never modified.
    Raises RuntimeError with instructions if the build can't be done.
    """
    if shutil.which("cmake") is None:
        raise RuntimeError(f"libhackrf was not found and building it needs `cmake`, which is not on PATH. {HELP}")
    if not (SOURCE / "CMakeLists.txt").exists():
        raise RuntimeError("The bundled libhackrf sources are missing (a source checkout needs "
                           f"`git submodule update --init vendor/hackrf`). {HELP}")

    target = next((d for d in cache_dirs() if _writable(d)), None)
    if target is None:
        raise RuntimeError(f"No writable place to cache libhackrf (tried {cache_dirs()}); "
                           f"set PYSDRLIB_CACHE. {HELP}")

    with tempfile.TemporaryDirectory(prefix="pysdrlib-libhackrf-") as tmp:
        try:
            subprocess.check_call(["cmake", "-S", str(SOURCE), "-B", tmp, "-DCMAKE_BUILD_TYPE=Release"])
            subprocess.check_call(["cmake", "--build", tmp, "--config", "Release"])
        except (subprocess.CalledProcessError, OSError) as exc:
            raise RuntimeError(f"Building libhackrf failed ({exc}). It needs the libusb-1.0 development "
                               f"files and a C compiler. {HELP}") from exc
        built = [p for pattern in ("libhackrf.so*", "libhackrf*.dylib", "hackrf.dll")
                 for p in Path(tmp).rglob(pattern)]
        if not built:
            raise RuntimeError(f"The libhackrf build produced no library. {HELP}")
        target.mkdir(parents=True, exist_ok=True)
        for file in built:
            shutil.copy2(file, target / file.name)   # follows symlinks: the soname files become real copies
    found = next((target / p for p in _PATTERNS if (target / p).exists()), None)
    if found is None:
        raise RuntimeError(f"Could not find the built libhackrf in {target}. {HELP}")
    return found


# ------------------------------------------------------------------ the extension (for setup.py)
def get():
    """The pybind11 Extension for setup.py. Does no building and does not link libhackrf: the
    hackrf_* symbols stay undefined and are resolved when the library is loaded at import time."""
    from pybind11.setup_helpers import Pybind11Extension

    extra_link_args = []
    if sys.platform == "darwin":
        extra_link_args = ["-undefined", "dynamic_lookup"]

    return Pybind11Extension(
        name="pysdrlib.hackrf.lib._hackrf",
        sources=[f"{SRC_DIR}/hackrf_pybind.cpp", f"{SRC_DIR}/hackrf_device.cpp"],
        include_dirs=[HEADER_DIR, SHARED_DIR, SRC_DIR],
        depends=[
            f"{SRC_DIR}/hackrf_device.hpp",
            f"{SHARED_DIR}/hardware_wrapper.hpp",
            f"{SHARED_DIR}/hardware_numpy.hpp",
            f"{HEADER_DIR}/hackrf.h",
        ],
        extra_link_args=extra_link_args,
        cxx_std=17,
        # Windows can't leave symbols undefined at link time: skip the extension there rather than
        # fail the whole build (importing pysdrlib.hackrf then explains it). Elsewhere errors are fatal.
        optional=sys.platform == "win32",
    )
