"""`pysdrlib play`: look at a recording, play it, serve it"""
import signal
import socket
import subprocess
import sys
import time

import numpy as np
import pytest

import pysdrlib
from pysdrlib.cli import main

@pytest.fixture
def rec(tmp_path):
    path = tmp_path / "r.sigmf"
    with pysdrlib.FileSink(path, "ci8", rate=200e3, freq=433.92e6, hw="test", scale=True) as out:
        out.write(np.full(50_000, 0.5, np.complex64))
        out.retune(434e6)
        out.write(np.full(50_000, 0.05, np.complex64))
    return path

def test_info(rec, capsys):
    with pytest.raises(SystemExit) as e:
        main(["play", str(rec), "--info"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "format ci8t (SigMF ci8), 100,000 samples, 0.500 s at 200 kHz" in out
    assert "on test" in out and "2 capture(s): 433.92 MHz from 0, 434 MHz from 50,000" in out

def test_play_shows_the_power_as_it_goes(rec, capsys):
    with pytest.raises(SystemExit):
        main(["play", str(rec), "--fast", "--interval", "0.125"])
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if "dBFS" in line]
    assert len(lines) == 4 and "end of the recording" in out
    assert "433.92 MHz  power   -6.0 dBFS" in lines[0] and "434 MHz  power  -26.6 dBFS" in lines[-1]

def test_a_raw_file_needs_its_format_and_rate(tmp_path):
    path = tmp_path / "x.cfile"
    np.zeros(1000, np.complex64).tofile(path)
    with pytest.raises(SystemExit, match="fmt"):
        main(["play", str(path)])
    with pytest.raises(SystemExit, match="--rate"):
        main(["play", str(path), "--fmt", "cf32"])
    with pytest.raises(SystemExit) as e:
        main(["play", str(path), "--fmt", "cf32", "--rate", "1e6", "--fast"])
    assert e.value.code == 0

@pytest.mark.skipif(sys.platform == "win32", reason="SIGINT to a child process")
def test_serve(rec):
    pytest.importorskip("zmq")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    endpoint = f"tcp://127.0.0.1:{port}"
    proc = subprocess.Popen([sys.executable, "-m", "pysdrlib", "play", str(rec), "--serve", endpoint, "--loop"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        for line in proc.stdout:
            if "serving it as" in line:
                break
        else:
            pytest.fail("no `serving` line")
        dev = pysdrlib.device("remote", endpoint, id="file-0")
        dev.open()
        try:
            x, m = dev.read(10_000, timeout=5, meta=True)
            assert m.rate == 200e3 and m.freq in (433.92e6, 434e6) and len(x) == 10_000
        finally:
            dev.close()
    finally:
        proc.send_signal(signal.SIGINT)
        proc.wait(timeout=10)
