"""A script that ends with a device still open (an exception, Ctrl+C, or just forgetting) leaves it stopped and closed"""
import os
import subprocess
import sys

import pytest

SCRIPT = """
import sys
from pysdrlib.sim import Sim

class Noted(Sim):
    def _stop_rx(self):
        open(sys.argv[1], "a").write("stop_rx\\n"); super()._stop_rx()
    def _close(self):
        open(sys.argv[1], "a").write("close\\n"); super()._close()

sdr = Noted(realtime=False)
sdr.open()
sdr.initialize(cf=100e6, Fs=2e6)
sdr.start_rx()
%s
"""


def run(tmp_path, ending):
    marks = tmp_path / "marks"
    out = subprocess.run([sys.executable, "-I", "-c", SCRIPT % ending, str(marks)], capture_output=True, text=True, timeout=60,
                         env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)})
    return out, (marks.read_text().split() if marks.exists() else [])


def test_a_script_that_just_ends_still_stops_and_closes_the_device(tmp_path):
    out, marks = run(tmp_path, "")
    assert out.returncode == 0 and marks == ["stop_rx", "close"]


def test_a_script_that_dies_with_an_exception_does_too(tmp_path):
    out, marks = run(tmp_path, "raise RuntimeError('boom')")
    assert out.returncode == 1 and "boom" in out.stderr and marks == ["stop_rx", "close"]


def test_ctrl_c_does_too(tmp_path):
    out, marks = run(tmp_path, "raise KeyboardInterrupt")
    assert marks == ["stop_rx", "close"]


def test_a_device_the_script_closed_is_not_closed_again(tmp_path):
    out, marks = run(tmp_path, "sdr.stop_rx(); sdr.close()")
    assert out.returncode == 0 and marks == ["stop_rx", "close"]


def test_open_devices_do_not_keep_themselves_alive():
    import gc
    import weakref
    from pysdrlib.base import device as base
    from pysdrlib.sim import Sim
    sdr = Sim(realtime=False)
    sdr.open()
    assert sdr in base._OPEN
    ref = weakref.ref(sdr)
    sdr.close()
    assert sdr not in base._OPEN
    del sdr
    gc.collect()
    assert ref() is None
