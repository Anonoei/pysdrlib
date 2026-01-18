import sys
from pathlib import Path
import importlib

from setuptools import setup, Extension
from Cython.Build import cythonize

import numpy as np

# python3 setup.py build_ext --inplace

sys.path.insert(0, str(Path(__file__).parent / "src"))
import pysdrlib._ext.hackrf as ext_hackrf

ext_modules = [
    ext_hackrf.get()
]

setup(
    name="pysdrlib",
    include_package_data=True,
    ext_modules=ext_modules,
)
