from setuptools import setup, Extension
from setuptools.command.build_ext import build_ext
import subprocess
import sys

from src.pysdrlib.hackrf import lib_path as lib_path_hackrf

LIB_PATHS = {}

class Build_HackRF(build_ext):
    def run(self):
        print(f"Setting up hackrf on {sys.platform}")
        LIB_PATHS["hackrf"] = lib_path_hackrf()
        if LIB_PATHS.get("hackrf", None) is None:
            subprocess.run(['make', 'hackrf'], cwd="/src/hackrf", shell=True)
        else:
            print(f"libhackrf is already installed!")
        build_ext.run(self)
class Filter_HackRF(Extension):
    def __init__(self, name, sources, include_dirs):
        super().__init__(name, sources, include_dirs)

setup(
    name="pysdrlib",
    version="0.0.1",
    ext_modules=[
        Filter_HackRF(
            "filter_hackrf",
            sources=[
                "vendor/hackrf/host/libhackrf"
            ],
            include_dirs=["include", "src"]
        )
    ],
    cmdclass={"build_ext": Build_HackRF}
)
