import sys
import types
from pathlib import Path

from setuptools import setup

# python3 setup.py build_ext --inplace

# Only pysdrlib._ext is needed here, and it must load without the compiled modules this build makes: pysdrlib's own
# __init__ imports them, so it is stood in for by an empty package (the real one is never imported by the build)
_package = types.ModuleType("pysdrlib")
_package.__path__ = [str(Path(__file__).parent / "src" / "pysdrlib")]
sys.modules["pysdrlib"] = _package
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
