"""`pysdrlib build` (pysdrlib/_ext/build.py): build the vendor libraries drivers need, on request"""
import argparse
from pathlib import Path

import pytest

from pysdrlib import devices
from pysdrlib._ext import build, utils
from pysdrlib import cli


@pytest.fixture
def libs(monkeypatch):
    """Every library missing, and building recorded instead of done"""
    made = {}
    for driver, lib in build.libraries().items():
        monkeypatch.setattr(lib, "locate", lambda: None)
        monkeypatch.setattr(lib, "available", lambda: False)
        monkeypatch.setattr(lib, "build", lambda driver=driver, lib=lib: made.setdefault(driver, Path("/built") / lib.files[0]))
    return made


def args(*drivers, all=False, force=False):
    return argparse.Namespace(drivers=list(drivers), all=all, force=force)


def test_every_driver_with_a_vendor_library_is_listed():
    assert {"hackrf", "rtl_sdr", "uhd", "extio"} <= set(build.libraries())
    assert all(lib.driver == driver for driver, lib in build.libraries().items())


def test_with_nothing_asked_it_says_where_each_stands_and_builds_nothing(libs, capsys):
    assert build.run(args()) == 0
    out = capsys.readouterr().out
    assert not libs and "missing: run `pysdrlib build uhd` (takes several minutes)" in out and "hackrf" in out


def test_a_driver_named_is_built(libs, capsys):
    assert build.run(args("hackrf")) == 0
    assert list(libs) == ["hackrf"] and "hackrf: built /built" in capsys.readouterr().out.replace("\\", "/")


def test_all_builds_what_is_missing_and_leaves_what_is_there(libs, monkeypatch, capsys):
    lib = build.libraries()["rtl_sdr"]
    monkeypatch.setattr(lib, "locate", lambda: ("system", "librtlsdr.so.0"))
    assert build.run(args(all=True)) == 0
    assert "rtl_sdr" not in libs and set(libs) == set(build.libraries()) - {"rtl_sdr"}
    assert "rtl_sdr: already available (system: librtlsdr.so.0)" in capsys.readouterr().out


def test_force_builds_again_and_says_which_library_is_still_loaded(libs, monkeypatch, capsys):
    lib = build.libraries()["hackrf"]
    monkeypatch.setattr(lib, "locate", lambda: ("system", "libhackrf.so.0"))
    assert build.run(args("hackrf", force=True)) == 0
    out = capsys.readouterr().out
    assert "hackrf" in libs and "still the one that gets loaded" in out


def test_a_driver_that_does_not_exist_is_an_error(libs, capsys):
    assert build.run(args("nosuch")) == 2
    assert "have: " in capsys.readouterr().err and not libs


def test_a_failed_build_says_why_and_the_others_still_go(libs, monkeypatch, capsys):
    def fail():
        raise RuntimeError("install cmake please")
    monkeypatch.setattr(build.libraries()["hackrf"], "build", fail)
    assert build.run(args("hackrf", "uhd")) == 1
    assert "install cmake please" in capsys.readouterr().err and list(libs) == ["uhd"]


def test_the_command_needs_no_pyzmq(libs, monkeypatch, capsys):
    import builtins
    real = builtins.__import__
    def no_zmq(name, *a, **k):
        if name == "zmq":
            raise ImportError("no zmq")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_zmq)
    with pytest.raises(SystemExit) as exit:
        cli.main(["build"])
    assert exit.value.code == 0 and "hackrf" in capsys.readouterr().out


class TestFindBuilding:
    """`find()` does not build; `find(build=True)` does, now"""
    def test_find_leaves_a_missing_library_alone(self, libs):
        assert devices._library_ready("uhd", build=False) is False and not libs

    def test_find_with_build_builds_it(self, monkeypatch):
        lib = build.libraries()["uhd"]
        loaded = []
        monkeypatch.setattr(lib, "available", lambda: False)
        monkeypatch.setattr(lib, "load", lambda build=None: loaded.append(build) or "built")
        assert devices._library_ready("uhd", build=True) is True and loaded == [True]

    def test_a_build_that_fails_just_leaves_the_driver_out(self, monkeypatch):
        lib = build.libraries()["uhd"]
        def fail(build=None):
            raise RuntimeError("no cmake")
        monkeypatch.setattr(lib, "available", lambda: False)
        monkeypatch.setattr(lib, "load", fail)
        assert devices._library_ready("uhd", build=True) is False
