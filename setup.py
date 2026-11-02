import os
import sys
from pathlib import Path

from setuptools import setup

# python3 setup.py build_ext --inplace

sys.path.insert(0, str(Path(__file__).parent / "src"))
import pysdrlib._ext.hackrf as ext_hackrf  # noqa: E402

ext_modules = [
    ext_hackrf.get(),
]

# extio is in early development; opt in with PYSDRLIB_EXTIO=1
if os.environ.get("PYSDRLIB_EXTIO") == "1":
    import pysdrlib._ext.extio as ext_extio  # noqa: E402
    ext_modules.append(ext_extio.get())

setup(
    name="pysdrlib",
    include_package_data=True,
    ext_modules=ext_modules,
)
