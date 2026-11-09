import ctypes
import ctypes.util
import importlib
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

PATH_MOD = Path(__file__).parent.parent
PATH_LIB = PATH_MOD / "_ext" / "cache"

def rmdir(path: Path):
    for file in path.iterdir():
        if file.is_dir():
            rmdir(file)
            continue
        file.unlink()
    path.rmdir()

def is_installed(lib_name):
    libc_path = ctypes.util.find_library(lib_name)
    if libc_path is None:
        return False
    return True


# ------------------------------------------------------------------ vendor libraries, found or built on the host
# A device's compiled extension leaves the vendor library's symbols undefined (packaging never builds or
# links it). `VendorLibrary.load` makes them available when the device package is first imported:
#
#     1. the system library, if installed
#     2. a copy previously built from the vendored sources
#     3. otherwise build one now from the vendored sources (cmake, no network) and cache it

def user_cache(name: str) -> Path:
    """Per-user directory for the library pysdrlib builds (override the root with PYSDRLIB_CACHE)"""
    if os.environ.get("PYSDRLIB_CACHE"):
        return Path(os.environ["PYSDRLIB_CACHE"]) / name
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "pysdrlib" / name

def load_global(path):
    # RTLD_GLOBAL so the extension's undefined symbols resolve against it
    ctypes.CDLL(str(path), mode=getattr(ctypes, "RTLD_GLOBAL", 0))

def writable(path: Path) -> bool:
    """True if `path` (or the nearest existing parent) can be written"""
    while not path.exists() and path != path.parent:
        path = path.parent
    return os.access(path, os.W_OK)


@dataclass
class VendorLibrary:
    """A vendor library a device's extension needs, and how to find or build it"""
    name: str                       # `ctypes.util.find_library` name, and the cache directory
    source: Path                    # CMake project that builds it
    files: tuple                    # file names that are the library, preferred first
    help: str                       # what to tell the user when it can't be built
    build_globs: tuple = ()         # CMake output to cache (default: `files`)
    missing_hints: tuple = ()       # in an ImportError: the extension loaded but this library didn't

    def cache_dirs(self):
        """Where a built library may live: next to the package (dev checkouts), then the user cache"""
        return [PATH_LIB / self.name, user_cache(self.name)]

    def find_cached(self):
        """Path of a previously built library, or None"""
        for directory in self.cache_dirs():
            for file in self.files:
                if (directory / file).exists():
                    return directory / file
        return None

    def available(self) -> bool:
        """True if the library can be loaded without building it (installed, or built before)"""
        return bool(ctypes.util.find_library(self.name)) or self.find_cached() is not None

    def is_missing(self, exc: ImportError) -> bool:
        """True if `exc` means the extension is there but this library's symbols aren't"""
        msg = str(exc).lower()
        return any(s in msg for s in ("undefined symbol", "symbol not found", "dll load failed", *self.missing_hints))

    def build(self) -> Path:
        """Build from the vendored sources out of tree (the package is never modified) and cache the
        result. Returns the library's path. Raises RuntimeError with instructions."""
        if shutil.which("cmake") is None:
            raise RuntimeError(f"{self.name} was not found and building it needs `cmake`, which is not on PATH. {self.help}")
        if not (self.source / "CMakeLists.txt").exists():
            raise RuntimeError(f"The bundled {self.name} sources are missing (a source checkout needs "
                               f"`git submodule update --init`). {self.help}")
        target = next((d for d in self.cache_dirs() if writable(d)), None)
        if target is None:
            raise RuntimeError(f"No writable place to cache {self.name} (tried {self.cache_dirs()}); "
                               f"set PYSDRLIB_CACHE. {self.help}")

        with tempfile.TemporaryDirectory(prefix=f"pysdrlib-{self.name}-") as tmp:
            try:
                subprocess.check_call(["cmake", "-S", str(self.source), "-B", tmp, "-DCMAKE_BUILD_TYPE=Release"])
                subprocess.check_call(["cmake", "--build", tmp, "--config", "Release"])
            except (subprocess.CalledProcessError, OSError) as exc:
                raise RuntimeError(f"Building {self.name} failed ({exc}). {self.help}") from exc
            built = [p for glob in (self.build_globs or self.files) for p in Path(tmp).rglob(glob)]
            if not built:
                raise RuntimeError(f"The {self.name} build produced no library. {self.help}")
            target.mkdir(parents=True, exist_ok=True)
            for file in built:
                shutil.copy2(file, target / file.name)   # follows symlinks: soname files become real copies
        found = next((target / f for f in self.files if (target / f).exists()), None)
        if found is None:
            raise RuntimeError(f"Could not find the built {self.name} in {target}. {self.help}")
        return found

    def load(self) -> str:
        """Make the library's symbols available to the extension; returns a description of what was
        loaded. Builds the bundled copy only if neither a system nor a cached library can be loaded."""
        system = ctypes.util.find_library(self.name)
        if system:
            try:
                load_global(system)
                return f"system library {system}"
            except OSError:
                pass
        cached = self.find_cached()
        if cached is not None:
            try:
                load_global(cached)
                return f"cached {cached}"
            except OSError:
                pass
        built = self.build()
        try:
            load_global(built)
        except OSError as exc:
            raise RuntimeError(f"Built {built} but could not load it: {exc}. {self.help}") from exc
        return f"built {built}"


def import_device(package: str, module: str, attr: str, library: VendorLibrary):
    """`attr` from `module` (relative to `package`), finding or building `library` first if the
    extension it imports can't load without it. Raises ImportError with instructions."""
    def get():
        return getattr(importlib.import_module(module, package), attr)
    try:
        return get()
    except ImportError as first:
        if not library.is_missing(first):
            raise ImportError(
                f"{package}'s compiled extension is not available for this installation "
                "(e.g. an unsupported platform, or a source checkout that was never built). "
                "Install a wheel for your platform, or build it with `pip install .`."
            ) from first
        try:
            library.load()
        except RuntimeError as exc:
            raise ImportError(str(exc)) from first
        return get()


# ------------------------------------------------------------------ the extensions (for setup.py)
# All C++ lives under here: include/ is shared, and each device (and samples) has a folder of its own
NATIVE = "src/pysdrlib/_native"

# No fused multiply-add: the conversion must round exactly as the numpy reference does, on every CPU. And no
# FP traps, which nothing uses and which stop the encoder's clamps from being vectorised.
FP_FLAGS = [] if sys.platform == "win32" else ["-ffp-contract=off", "-fno-trapping-math"]

def pybind_extension(device: str, header_dir: str, header: str):
    """The pybind11 Extension for `pysdrlib.<device>.lib._<device>` (setup.py). Does no building and
    does not link the vendor library: its symbols stay undefined and are resolved when the library is
    loaded at import time. Sources are `<device>_pybind.cpp` and `<device>_device.{cpp,hpp}` in
    `src/pysdrlib/_native/<device>`, compiled against the vendor's `header` in `header_dir` (repo-relative)."""
    from pybind11.setup_helpers import Pybind11Extension

    src, shared = f"{NATIVE}/{device}", f"{NATIVE}/include"
    return Pybind11Extension(
        name=f"pysdrlib.{device}.lib._{device}",
        sources=[f"{src}/{device}_pybind.cpp", f"{src}/{device}_device.cpp"],
        include_dirs=[header_dir, shared, src],
        depends=[f"{src}/{device}_device.hpp", f"{header_dir}/{header}",
                 f"{shared}/hardware_wrapper.hpp", f"{shared}/hardware_numpy.hpp", f"{shared}/hardware_pybind.hpp",
                 f"{shared}/hardware_format.hpp", f"{shared}/hardware_convert.hpp", f"{shared}/hardware_rx.hpp"],
        # macOS leaves them undefined with a flag; Windows can't at all: skip the extension there rather
        # than fail the whole build (importing the device then explains it). Elsewhere errors are fatal.
        extra_link_args=["-undefined", "dynamic_lookup"] if sys.platform == "darwin" else [],
        extra_compile_args=FP_FLAGS,
        cxx_std=17,
        optional=sys.platform == "win32",
    )


def samples_extension():
    """The pybind11 Extension for `pysdrlib.base.lib._samples` (setup.py): the sample conversion and ring
    that pysdrlib.base.convert and pysdrlib.base.buffer call. It depends on no vendor library, so unlike the
    devices' it is always built and always works."""
    from pybind11.setup_helpers import Pybind11Extension

    src, shared = f"{NATIVE}/samples", f"{NATIVE}/include"
    return Pybind11Extension(
        name="pysdrlib.base.lib._samples",
        sources=[f"{src}/samples_pybind.cpp"],
        include_dirs=[shared],
        depends=[f"{shared}/hardware_format.hpp", f"{shared}/hardware_convert.hpp", f"{shared}/hardware_ring.hpp"],
        extra_compile_args=FP_FLAGS,
        cxx_std=17,
    )
