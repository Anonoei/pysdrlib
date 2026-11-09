"""Check that a driver is put together the way pysdrlib expects, without hardware or its vendor library

A driver is a `Device` subclass plus a package around it (see `pysdrlib.contributing`). Most mistakes in one show up
late and far from their cause: a gain stage whose hook is misspelled fails when a user sets that stage, a `Setting`
that names a method the class lacks fails when someone calls `set_setting`. `check_driver` finds them from the class
alone:

    from pysdrlib import testing
    problems = testing.check_driver(MyRadio)          # [Problem]; empty: nothing found
    testing.assert_conforms(MyRadio)                  # raises AssertionError listing the errors (warnings are fine)
    testing.check_package("my_radio")                 # the package around it: Device, find(), lib/, _ext/, README

`tests/test_conformance.py` runs both on every driver, so a new driver is checked as soon as it exists.

A `Problem` is an "error" (the driver is broken or incomplete) or a "warning" (it works, but something other drivers
provide is missing, or a declaration will be ignored).
"""
import importlib
import importlib.util
import inspect
from dataclasses import dataclass
from pathlib import Path

from . import devices
from .base import convert
from .base.device import Device
from .base.native import NativeDevice

ERROR, WARNING = "error", "warning"

# What a driver always supplies itself (the base class only raises or does nothing)
_REQUIRED_HOOKS = ("_open", "_close", "_start_rx", "_stop_rx", "_get_samples", "_set_freq", "_set_sample_rate")
_SETTING_KINDS = (bool, int, float, str)

@dataclass(frozen=True)
class Problem:
    level: str      # ERROR or WARNING
    where: str      # what it is about: "MyRadio.GAIN_RESERVE", "my_radio package"...
    message: str

    def __str__(self):
        return f"{self.level}: {self.where}: {self.message}"

def _overrides(cls, name):
    """The class (or one of its bases below `Device`) defines `name` itself"""
    return getattr(cls, name, None) is not getattr(Device, name, None)

def check_driver(cls) -> list:
    """[Problem] with the `Device` subclass `cls`. Reads class attributes and its CONFIG; creates and opens nothing"""
    problems = []
    name = getattr(cls, "__name__", str(cls))

    def add(level, where, message):
        problems.append(Problem(level, f"{name}.{where}" if where else name, message))

    if not (isinstance(cls, type) and issubclass(cls, Device)):
        add(ERROR, "", "is not a pysdrlib Device subclass")
        return problems

    if cls.NAME == Device.NAME:
        add(ERROR, "NAME", "is not set (it is what users and `caps.driver` call the device)")

    for hook in _REQUIRED_HOOKS:
        if not _overrides(cls, hook):
            add(ERROR, hook, "is not implemented (Device only raises NotImplementedError here)")

    if issubclass(cls, NativeDevice) and not isinstance(cls.SAMPLE_FORMAT, convert.SampleFormat):
        add(ERROR, "SAMPLE_FORMAT", "must be a convert.SampleFormat describing the raw stream the extension delivers")

    config = cls.CONFIG
    if config is None:
        add(ERROR, "CONFIG", "is not set (a ConfigDevice with the device's limits and gain stages)")
        return problems
    _check_limits(config, add)
    _check_gain(cls, config, add)
    _check_settings(cls, add)
    _check_signatures(cls, add)

    if cls.CAN_TRANSMIT:
        if not _overrides(cls, "_tx_write"):
            add(WARNING, "CAN_TRANSMIT", "is True but _tx_write is not implemented, so write() raises NotSupported")
        if not config.stages["TX"]:
            add(WARNING, "CONFIG", "has no TX gain stages for a device that transmits")
    elif config.stages["TX"]:
        add(WARNING, "CONFIG", "declares TX gain stages but CAN_TRANSMIT is False: they are ignored")
    if cls.RETUNE_SETTLE < 0 or cls.RETUNE_SAMPLES < 0:
        add(ERROR, "RETUNE_SETTLE", "and RETUNE_SAMPLES cannot be negative")
    return problems

def _check_limits(config, add):
    for lo, hi, default, what in (("FREQ_MIN", "FREQ_MAX", "DEFAULT_FREQ", "frequency"),
                                  ("SAMPLE_RATE_MIN", "SAMPLE_RATE_MAX", "DEFAULT_SAMPLE_RATE", "sample rate")):
        values = [getattr(config, n, None) for n in (lo, hi, default)]
        if None in values:
            add(ERROR, "CONFIG", f"needs {lo}, {hi} and DEFAULT[{default.removeprefix('DEFAULT_')!r}] ({what} limits)")
        elif not values[0] <= values[2] <= values[1]:
            add(ERROR, "CONFIG", f"the default {what} {values[2]:,} is outside {values[0]:,} - {values[1]:,}")

def _check_gain(cls, config, add):
    for direction in ("rx", "tx"):
        stages = config.stages[direction.upper()]
        for stage, spec in stages.items():
            if direction == "tx" and not cls.CAN_TRANSMIT:
                continue
            if not _overrides(cls, f"_set_{direction}_{stage.lower()}_gain"):
                add(ERROR, f"_set_{direction}_{stage.lower()}_gain",
                    f"is missing: it applies the {direction.upper()} gain stage {stage!r} (CONFIG declares it)")
            if spec.min > spec.max:
                add(ERROR, "CONFIG", f"{direction.upper()} stage {stage!r}: MIN {spec.min} is above MAX {spec.max}")
            if spec.default is None:
                add(WARNING, "CONFIG", f"{direction.upper()} stage {stage!r} has no default (DEFAULT.GAIN_{direction.upper()}_{stage})")
            elif not spec.min <= spec.default <= spec.max:
                add(ERROR, "CONFIG", f"{direction.upper()} stage {stage!r}: default {spec.default} is outside {spec.min} - {spec.max}")
    for direction, reserved in cls.GAIN_RESERVE.items():
        if direction not in ("rx", "tx"):
            add(ERROR, "GAIN_RESERVE", f"is keyed by 'rx' or 'tx', not {direction!r}")
            continue
        for stage in reserved:
            if stage not in config.stages[direction.upper()]:
                add(ERROR, "GAIN_RESERVE", f"names a {direction.upper()} stage {stage!r} that CONFIG does not declare")
    if cls.GAIN_BALANCE not in ("range", "db"):
        add(ERROR, "GAIN_BALANCE", f"is 'range' or 'db', not {cls.GAIN_BALANCE!r}")

def _required(fn):
    """Names of the positional parameters a call must give (after self), or None if it takes *args"""
    params = list(inspect.signature(fn).parameters.values())[1:]
    if any(p.kind is p.VAR_POSITIONAL for p in params):
        return None
    return [p.name for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD) and p.default is p.empty]

def _check_signatures(cls, add):
    """A hook a driver implements is called the way the base class's is: it must need the same arguments (it may take
    more, as keywords with defaults: `channel`, `direction` and `slot` are passed only to hooks that take them)"""
    for name, base in vars(Device).items():
        if not name.startswith("_") or name.startswith("__") or not inspect.isfunction(base):
            continue
        mine = inspect.getattr_static(cls, name, None)
        if not inspect.isfunction(mine) or mine is base or getattr(mine, "_generated", False):
            continue
        want, got = _required(base), _required(mine)
        if want is not None and got is not None and want != got:
            add(ERROR, name, f"needs ({', '.join(got)}) but is called with ({', '.join(want)}): give any others a default")

def _check_settings(cls, add):
    for key, setting in cls.SETTINGS.items():
        if setting.kind not in _SETTING_KINDS:
            add(ERROR, f"SETTINGS[{key!r}]", f"kind must be one of bool, int, float, str, not {setting.kind!r}")
        for attr in ("method", "getter"):
            hook = getattr(setting, attr)
            if hook and not callable(getattr(cls, hook, None)):
                add(ERROR, f"SETTINGS[{key!r}]", f"{attr} {hook!r} is not a method of the class")
        if setting.choices is not None and setting.default is not None and setting.default not in setting.choices:
            add(ERROR, f"SETTINGS[{key!r}]", f"default {setting.default!r} is not one of {setting.choices}")

def check_package(name) -> list:
    """[Problem] with the driver package `pysdrlib.<name>`: that it exposes `Device` and `find()`, and (for a driver on a
    compiled extension) has the `lib/` and `_ext/<name>.py` that load it. Also runs `check_driver` on its Device"""
    problems = []

    def add(level, message):
        problems.append(Problem(level, f"{name} package", message))

    try:
        module = devices.get(name)
    except Exception as exc:   # a missing library is the machine's problem, not the driver's: say what happened
        add(ERROR, f"cannot be imported: {exc}")
        return problems
    device = getattr(module, "Device", None)
    if device is None:
        add(ERROR, "does not define `Device` in its __init__.py")
        return problems
    problems += check_driver(device)
    if not callable(getattr(module, "find", None)):
        add(WARNING, "has no find(): pysdrlib.find() cannot list its units")
    folder = Path(module.__file__).parent
    if not (folder / "README.md").is_file():
        add(WARNING, "has no README.md (what it is, its specs, what to install)")
    if issubclass(device, NativeDevice):
        if importlib.util.find_spec(f"pysdrlib._ext.{name}") is None:
            add(ERROR, f"has no _ext/{name}.py (how its vendor library is found or built, and its extension)")
        else:
            ext = importlib.import_module(f"pysdrlib._ext.{name}")
            if getattr(ext, "LIB", None) is None:
                add(ERROR, f"_ext/{name}.py defines no LIB (a utils.VendorLibrary)")
        for needed in ("lib/__init__.py", "lib/err.py"):
            if not (folder / needed).is_file():
                add(ERROR, f"has no {needed} (the compiled extension's import and its error classes)")
    return problems

def errors(problems) -> list:
    return [p for p in problems if p.level == ERROR]

def assert_conforms(driver):
    """Raise AssertionError listing every error in `driver` (a Device class, or a driver package's name). Warnings pass"""
    problems = check_package(driver) if isinstance(driver, str) else check_driver(driver)
    bad = errors(problems)
    assert not bad, "driver does not conform:\n  " + "\n  ".join(map(str, bad))
