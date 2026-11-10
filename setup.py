import sys
from pathlib import Path

from setuptools import setup

# python3 setup.py build_ext --inplace

sys.path.insert(0, str(Path(__file__).parent / "src"))
import pysdrlib._ext.utils as ext_utils  # noqa: E402

# The samples extension, then every device's: any `_ext/<name>.py` with a get() (no list to keep up to date here)
ext_modules = [
    ext_utils.samples_extension(),
    *ext_utils.driver_extensions(),
]

setup(
    name="pysdrlib",
    include_package_data=True,
    ext_modules=ext_modules,
)
