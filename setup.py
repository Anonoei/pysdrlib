from setuptools import setup, Extension
from Cython.Build import cythonize

import numpy

# python3 setup.py build_ext --inplace

ext_modules = cythonize([
    Extension(
        name="pysdrlib.hackrf.lib.hackrf",
        sources=["src/pysdrlib/hackrf/lib/hackrf.pyx"],
        include_dirs=["vendor/hackrf/host/libhackrf/src/", numpy.get_include()],
        depends=[
            "src/pysdrlib/hackrf/lib/chackrf.pxd",
            "vendor/hackrf/host/libhackrf/src/hackrf.h",
            "vendor/hackrf/host/libhackrf/src/hackrf.c",
        ],
        libraries=["hackrf"],
        optional=True
    )
])

package_data = {
    "": ["*.pyx", "*.pxd", "*.h", "*.c"],
}

setup(
    name="pysdrlib",
    include_package_data=True,
    ext_modules=ext_modules,
)
