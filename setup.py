import sys
from pathlib import Path
import importlib

from setuptools import setup, Extension
from Cython.Build import cythonize

import numpy as np

# python3 setup.py build_ext --inplace

# ext_modules = [
#     Extension(
#         name="pysdrlib.hackrf.lib.hackrf",
#         sources=["src/pysdrlib/hackrf/lib/hackrf.pyx"],
#         include_dirs=["src/pysdrlib/hackrf/data/libhackrf", np.get_include()],
#         depends=[
#             "src/pysdrlib/hackrf/lib/chackrf.pxd",
#             "src/pysdrlib/hackrf/data/libhackrf/hackrf.c",
#             "src/pysdrlib/hackrf/data/libhackrf/hackrf.h",
#         ],
#         libraries=["hackrf"],
#         optional=True
#     )
# ]

sys.path.insert(0, str(Path(__file__).parent / "src"))
import pysdrlib._ext.hackrf as ext_hackrf
# ext_hackrf = importlib.import_module(".hackrf.ext", str(Path(__file__).parent / "src" / "pysdrlib"))

ext_modules = cythonize([
    ext_hackrf.get()
])

# package_data = {
#     "": ['*.pyx', '*.pxd', '*.h', '*.c'],
# }

setup(
    name="pysdrlib",
    include_package_data=True,
    ext_modules=ext_modules,
    # package_data=package_data
)
