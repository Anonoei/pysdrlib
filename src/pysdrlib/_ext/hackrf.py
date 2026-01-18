import subprocess

from setuptools import Extension
import numpy as np

from . import utils

def mk_lib():
    path_build = utils.PATH_MOD / "hackrf" / "data" / "build"
    path_build.mkdir(exist_ok=True, parents=True)

    subprocess.check_call("cmake ../libhackrf", cwd=path_build, shell=True)
    subprocess.check_call("make", cwd=path_build, shell=True)

    for file in (path_build / "src").glob("libhackrf*"):
        file.rename(utils.PATH_LIB / file.name)

    utils.rmdir(path_build)
    # return utils.PATH_LIB
    return "src/pysdrlib/_ext/cache"

def get():
    library_dirs = None
    if not utils.is_installed("hackrf"):
        library_dirs = [str(mk_lib())]

    return Extension(
        name="pysdrlib.hackrf.lib.hackrf",
        sources=["src/pysdrlib/hackrf/lib/hackrf.pyx"],
        include_dirs=["src/pysdrlib/hackrf/data/libhackrf/src/", np.get_include()],
        depends=[
            "src/pysdrlib/hackrf/lib/chackrf.pxd",
            "src/pysdrlib/hackrf/data/libhackrf/src/hackrf.h",
            "src/pysdrlib/hackrf/data/libhackrf/src/hackrf.c",
        ],
        library_dirs=library_dirs,
        runtime_library_dirs=library_dirs,
        libraries=["hackrf"],
        optional=True
    )
