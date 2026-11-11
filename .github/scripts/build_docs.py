"""Run pdoc with lazy symbol binding, so the docs build needs no vendor library for drivers that allow it.

A device's compiled extension leaves its vendor library's symbols undefined. Python's default dlopen flags
(RTLD_NOW) refuse to load it until that library is there, which for UHD means building the bundled copy (Boost,
several minutes) just to read docstrings. With RTLD_LAZY the extension loads and its functions bind only when
called, which pdoc never does. (An extension with an undefined *data* symbol, like extio's, still needs its
library; that one is small and PYSDRLIB_BUILD=auto builds it.)

usage: python .github/scripts/build_docs.py <pdoc arguments>
"""
import os
import runpy
import sys

sys.setdlopenflags(os.RTLD_LAZY | os.RTLD_GLOBAL)
sys.argv[0] = "pdoc"
runpy.run_module("pdoc", run_name="__main__", alter_sys=True)
