"""Build helpers for the HackRF pybind11 extension and, when the host lacks it, libhackrf.

libhackrf is built offline from the vendored sources (hackrf/_build/libhackrf, a
symlink to vendor/hackrf) and cached in _ext/cache/hackrf.
"""
import shutil
import subprocess
from pathlib import Path

from . import utils

PATH_CACHE = utils.PATH_LIB / "hackrf"
PATH_BUILD = utils.PATH_MOD / "hackrf" / "_build" / "build"

# Repo-relative paths for setuptools (it runs from the project root)
SRC_DIR = "src/pysdrlib/hackrf/lib"
HEADER_DIR = "src/pysdrlib/hackrf/_build/libhackrf/src"
SHARED_DIR = "src/pysdrlib/_ext"
# Extension lives at pysdrlib/hackrf/lib/_hackrf*.so
RPATH_CACHE = "$ORIGIN/../../_ext/cache/hackrf"


def cached() -> bool:
    return any(PATH_CACHE.glob("libhackrf.so*")) or any(PATH_CACHE.glob("libhackrf*.dylib")) \
        or any(PATH_CACHE.glob("hackrf*.dll"))


def mk_lib(force=False) -> Path:
    """Build libhackrf from the vendored source into the cache. No network needed.

    Returns the cache directory. Raises RuntimeError if the build can't run.
    """
    if cached() and not force:
        return PATH_CACHE

    for tool in ("cmake", "make"):
        if shutil.which(tool) is None:
            raise RuntimeError(f"Building libhackrf requires `{tool}`, which was not found on PATH. "
                               "Install it, or install libhackrf from your package manager.")
    if not (PATH_BUILD.parent / "libhackrf" / "CMakeLists.txt").exists():
        raise RuntimeError("Vendored libhackrf sources not found; "
                           "run `git submodule update --init vendor/hackrf`.")

    PATH_CACHE.mkdir(exist_ok=True, parents=True)
    PATH_BUILD.mkdir(exist_ok=True, parents=True)
    try:
        subprocess.check_call(["cmake", "../libhackrf"], cwd=PATH_BUILD)
        subprocess.check_call(["make"], cwd=PATH_BUILD)
        for file in (PATH_BUILD / "src").glob("libhackrf*"):
            shutil.move(str(file), PATH_CACHE / file.name)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Building libhackrf failed: {exc}") from exc
    finally:
        utils.rmdir(PATH_BUILD)
    return PATH_CACHE


def get():
    """The pybind11 Extension for setup.py. Builds libhackrf first if the host lacks it."""
    from pybind11.setup_helpers import Pybind11Extension

    library_dirs = []
    extra_link_args = []
    if not utils.is_installed("hackrf"):
        library_dirs = [str(mk_lib().relative_to(utils.PATH_MOD.parent.parent))]
        extra_link_args = [f"-Wl,-rpath,{RPATH_CACHE}"]

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
        library_dirs=library_dirs,
        extra_link_args=extra_link_args,
        libraries=["hackrf"],
        cxx_std=17,
    )
