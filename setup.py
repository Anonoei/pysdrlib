from setuptools import setup, Extension
from Cython.Build import cythonize

import numpy

# python3 setup.py build_ext --inplace

ext_modules = cythonize([
    Extension(
        name="pysdrlib.hackrf.lib.hackrf",
        sources=["src/pysdrlib/hackrf/lib/hackrf.pyx"],
        include_dirs=["vendor/hackrf/host/libhackrf/src/", numpy.get_include()],
        libraries=["hackrf"],
        depends=["src/pysdrlib/hackrf/lib/chackrf.pxd"]
    )
])

setup(
    name="pysdrlib",
    version="0.0.1",
    include_package_data=True,
    package_data={"": ["*.pyx", "*.pxd", "*.h", "*.c"]},
    ext_modules=ext_modules,
)
