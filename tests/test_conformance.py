"""Every driver is put together the way pysdrlib expects (pysdrlib.testing), and the tools for adding one work.

No hardware or vendor library is needed: the checks read classes and their CAPS, and the scaffolder writes files.
"""
import importlib
import py_compile
import sys
import types

import pytest

import pysdrlib
from pysdrlib import devices, err, scaffold, testing
from pysdrlib.base.caps import Setting
from pysdrlib.base.device import Device
from pysdrlib.base.native import NativeDevice


# ---------------------------------------------------------------- the registry
def test_ls_finds_drivers_by_their_device_line():
    names = devices.ls()
    assert names == sorted(names)
    assert {"hackrf", "rtl_sdr", "uhd", "extio", "sim"} <= set(names)
    assert not {"base", "file", "remote", "bladerf"} & set(names)   # not drivers (or a stub with no Device yet)
    assert not [n for n in names if n.startswith("_")]

def test_get_unknown_driver_names_the_ones_there_are():
    with pytest.raises(err.NoDevice, match="nope.*drivers:.*sim"):
        devices.get("nope")
    with pytest.raises(err.NoDevice):
        pysdrlib.device("nope")

def test_extensions_come_from_the_ext_modules():
    from pysdrlib._ext import utils
    built = {e.name for e in utils.driver_extensions()}
    assert {"pysdrlib.hackrf.lib._hackrf", "pysdrlib.uhd.lib._uhd"} <= built


# ---------------------------------------------------------------- the checker on the drivers
@pytest.mark.parametrize("name", devices.ls())
def test_driver_package_conforms(name):
    try:
        problems = testing.check_package(name)
    except Exception as exc:   # pragma: no cover
        pytest.skip(f"{name} unavailable: {exc}")
    if any("cannot be imported" in p.message for p in problems):
        pytest.skip(f"{name} unavailable: {problems[0].message}")
    assert not testing.errors(problems), "\n".join(map(str, problems))


# ---------------------------------------------------------------- the checker catches mistakes
def _import_or_skip(name):
    """Import a driver module; skip when its extension isn't built (importing no longer builds it)"""
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        pytest.skip(f"{name} unavailable: {exc}")

def _driver(**attrs):
    """A copy of the simulated device (complete) with attributes changed"""
    from pysdrlib.sim import Sim
    return type("Probe", (Sim,), attrs)

def _messages(cls, level=testing.ERROR):
    return [str(p) for p in testing.check_driver(cls) if p.level == level]

def test_a_complete_driver_has_no_errors():
    from pysdrlib.sim import Sim
    assert testing.errors(testing.check_driver(Sim)) == []
    testing.assert_conforms(Sim)

def test_not_a_device():
    assert "not a pysdrlib Device" in str(testing.check_driver(int)[0])

def test_the_base_class_is_incomplete():
    class Bare(Device):
        CAPS = _driver().CAPS
    msgs = " ".join(_messages(Bare))
    for hook in ("_open", "_close", "_start_rx", "_get_samples", "_set_rx_freq", "_set_rx_sample_rate"):
        assert hook in msgs
    assert "NAME" in msgs

def test_native_device_needs_a_sample_format():
    class Raw(NativeDevice):
        NAME = "Raw"
        CAPS = _driver().CAPS
        def _open(self): ...
        def _set_rx_freq(self, freq): ...
        def _set_rx_sample_rate(self, Fs): ...
    assert any("SAMPLE_FORMAT" in m for m in _messages(Raw))

def test_a_misspelled_gain_setter_is_found():
    caps = _import_or_skip("pysdrlib.hackrf").Device.CAPS   # the HackRF's stages on a class without them all
    msgs = _messages(_driver(CAPS=caps))
    assert any("_set_rx_rf_gain" in m for m in msgs)

def test_settings_must_name_real_methods_and_defaults():
    bad = _driver(SETTINGS={"a": Setting(bool, "x", "_nothing_like_this"),
                            "b": Setting(str, "x", "_set_bias_t", choices=("p", "q"), default="z"),
                            "c": Setting(dict, "x", "_set_bias_t")})
    msgs = _messages(bad)
    assert any("'_nothing_like_this'" in m for m in msgs)
    assert any("default 'z'" in m for m in msgs)
    assert any("bool, int, float, str" in m for m in msgs)

def test_gain_reserve_must_name_stages():
    assert any("GAIN_RESERVE" in m for m in _messages(_driver(GAIN_RESERVE={"rx": ("NOPE",)}, GAIN_BALANCE="mid")))

def test_a_transmitter_without_a_write_is_a_warning():
    msgs = _messages(_driver(CAN_TRANSMIT=True, _tx_write=Device._tx_write), testing.WARNING)
    assert any("_tx_write" in m for m in msgs)

def test_assert_conforms_lists_errors():
    class Bare(Device):
        CAPS = _driver().CAPS
    with pytest.raises(AssertionError, match="_open"):
        testing.assert_conforms(Bare)


# ---------------------------------------------------------------- the scaffolder
def test_names_are_validated(tmp_path):
    for bad in ("Bad", "1abc", "has-dash", "base", ""):
        with pytest.raises(ValueError):
            scaffold.new_driver(bad, root=tmp_path)
    with pytest.raises(ValueError):
        scaffold.new_driver("good", cls="not valid", root=tmp_path)
    assert not list(tmp_path.iterdir())

def test_camel_case():
    assert scaffold.camel("rtl_sdr") == "RtlSdr" and scaffold.camel("bladerf") == "Bladerf"

def test_scaffold_writes_the_listed_files_and_never_overwrites(tmp_path):
    written = scaffold.new_driver("probe_radio", root=tmp_path)
    rel = {p.relative_to(tmp_path).as_posix() for p in written}
    assert rel == {"probe_radio/__init__.py", "probe_radio/probe_radio.py",
                   "probe_radio/README.md", "probe_radio/lib/__init__.py",
                   "_ext/probe_radio.py", "_native/probe_radio/probe_radio_device.hpp",
                   "_native/probe_radio/probe_radio_device.cpp", "_native/probe_radio/probe_radio_pybind.cpp"}
    for path in written:
        text = path.read_text()
        assert "@" not in text.replace("@property", "").replace("@staticmethod", ""), path   # every token filled in
        if path.suffix == ".py":
            py_compile.compile(str(path), doraise=True)
    assert "class ProbeRadio(NativeDevice)" in (tmp_path / "probe_radio/probe_radio.py").read_text()
    with pytest.raises(FileExistsError):
        scaffold.new_driver("probe_radio", root=tmp_path)

def test_python_only_scaffold_has_no_extension_files(tmp_path):
    rel = {p.relative_to(tmp_path).as_posix() for p in scaffold.new_driver("probe_py", native=False, root=tmp_path)}
    assert rel == {"probe_py/__init__.py", "probe_py/probe_py.py", "probe_py/README.md"}

def test_unfinished_native_driver_is_left_out_of_the_build(tmp_path):
    scaffold.new_driver("probe_radio", root=tmp_path)
    get = (tmp_path / "_ext/probe_radio.py").read_text().split("def get()")[1]
    assert get.split("# return")[0].rstrip().endswith("return None")   # the real line is commented out until it compiles

def _load_generated(tmp_path, name, native):
    """Import a generated driver's modules as pysdrlib.<name> (a native one over a stand-in for its extension)"""
    scaffold.new_driver(name, native=native, root=tmp_path)
    sys.modules.pop(f"pysdrlib.{name}", None)
    pkg = types.ModuleType(f"pysdrlib.{name}")
    pkg.__path__ = [str(tmp_path / name)]
    pkg.__package__ = f"pysdrlib.{name}"
    sys.modules[pkg.__name__] = pkg
    if native:
        try:
            from pysdrlib.rtl_sdr import lib as real
        except ImportError as exc:   # the extension isn't built on this platform
            pytest.skip(f"rtl_sdr unavailable: {exc}")
        fake = types.ModuleType(f"pysdrlib.{name}.lib")
        fake.err = real.err
        setattr(fake, name, types.SimpleNamespace(device=None))
        sys.modules[fake.__name__] = fake
        pkg.lib = fake
    try:
        return importlib.import_module(f"pysdrlib.{name}.{name}")
    finally:
        for key in [k for k in sys.modules if k.startswith(f"pysdrlib.{name}")]:
            sys.modules.pop(key)

def test_generated_native_driver_passes_the_checker(tmp_path):
    module = _load_generated(tmp_path, "probe_radio", True)
    problems = testing.check_driver(module.ProbeRadio)
    assert not testing.errors(problems), problems
    assert issubclass(module.ProbeRadio, NativeDevice)
    caps = module.ProbeRadio().caps
    assert list(caps.gains["rx"]) == ["MAIN"] and caps.driver == "ProbeRadio"

def test_generated_python_driver_passes_the_checker(tmp_path):
    module = _load_generated(tmp_path, "probe_py", False)
    assert not testing.errors(testing.check_driver(module.ProbePy))
    sdr = module.ProbePy()
    sdr.open()
    assert sdr.set_rx_freq(50e6) == 50e6 and sdr.set_rx_gain_db(10) == 10
    sdr.close()

def test_command_line(tmp_path, capsys):
    assert scaffold.main(["cli_probe", "--python-only", "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out.replace("\\", "/")
    assert "created cli_probe/cli_probe.py" in out and "check_package" in out
    assert scaffold.main(["cli_probe", "--python-only", "--root", str(tmp_path)]) == 1   # exists
    assert "already exists" in capsys.readouterr().err


# ---------------------------------------------------------------- generated error classes
@pytest.mark.parametrize("name", ["hackrf", "rtl_sdr", "uhd", "extio"])
def test_driver_errors_are_classes_of_their_base(name):
    lib_err = _import_or_skip(f"pysdrlib.{name}.lib.err")
    codes = [v for k, v in vars(lib_err).items() if k.isupper()]
    assert codes and all(issubclass(c, Exception) and c.__module__ == lib_err.__name__ for c in codes)
    assert len({c.__mro__[1] for c in codes}) == 1   # one base for all


# ---------------------------------------------------------------- stage setters made from CAPS
def test_stage_setters_are_made_from_caps():
    from pysdrlib.sim import Sim
    assert Sim.set_rx_if_gain._generated and Sim.set_rx_if_gain.__qualname__ == "Sim.set_rx_if_gain"
    assert "0-40" in Sim.set_rx_if_gain.__doc__ and "default" in Sim.set_rx_if_gain.__doc__
    sdr = Sim()
    sdr.open()
    assert sdr.set_rx_if_gain(24) == 24 and sdr.get_rx_gain_db("IF") == 24
    assert sdr.set_rx_bb_gain() == sdr.CAPS.stage("rx", "BB").default      # None: the default

def test_a_written_setter_is_kept():
    try:
        from pysdrlib.rtl_sdr.rtlsdr import RTLSDR
    except ImportError as exc:   # the extension isn't built on this platform
        pytest.skip(f"rtl_sdr unavailable: {exc}")
    assert not getattr(RTLSDR.set_rx_rf_gain, "_generated", False) and "automatic gain" in RTLSDR.set_rx_rf_gain.__doc__.lower()

def test_transmit_stages_only_for_transmitters():
    hackrf = _import_or_skip("pysdrlib.hackrf").Device.CAPS
    assert not hasattr(_driver(CAPS=hackrf), "set_tx_if_gain")
    tx = _driver(CAPS=hackrf, CAN_TRANSMIT=True)
    assert hasattr(tx, "set_tx_if_gain") and "0-47" in tx.set_tx_if_gain.__doc__

def test_a_stage_without_its_hook_says_so():
    hackrf = _import_or_skip("pysdrlib.hackrf").Device.CAPS
    sdr = _driver(CAPS=hackrf)()
    sdr.open()
    with pytest.raises(NotImplementedError, match="_set_rx_rf_gain"):
        sdr.set_rx_rf_gain(0)


# ---------------------------------------------------------------- hook signatures
def test_a_hook_that_needs_other_arguments_is_an_error():
    msgs = _messages(_driver(_set_rx_freq=lambda self, freq, extra: None, _set_bandwidth=lambda self, bw: None))
    assert any("_set_rx_freq" in m and "extra" in m for m in msgs)
    assert any("_set_bandwidth" in m for m in msgs)

def test_hooks_may_take_optional_extras():
    ok = _driver(_set_rx_freq=lambda self, freq, channel=0, more=1: None)
    assert not [m for m in _messages(ok) if "_set_rx_freq" in m]
