import sys
from pathlib import Path

from setuptools import setup

# python3 setup.py build_ext --inplace

sys.path.insert(0, str(Path(__file__).parent / "src"))
import pysdrlib._ext.utils as ext_utils  # noqa: E402
import pysdrlib._ext.hackrf as ext_hackrf  # noqa: E402
import pysdrlib._ext.extio as ext_extio  # noqa: E402
import pysdrlib._ext.rtl_sdr as ext_rtl_sdr  # noqa: E402

ext_modules = [
    ext_utils.samples_extension(),
    ext_hackrf.get(),
    ext_extio.get(),
    ext_rtl_sdr.get(),
]

setup(
    name="pysdrlib",
    include_package_data=True,
    ext_modules=ext_modules,
)
