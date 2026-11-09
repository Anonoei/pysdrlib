"""Drivers, and the devices attached to this machine

    pysdrlib.devices.ls()            # driver names
    pysdrlib.find()                  # [DeviceInfo]: the radios plugged in, for every driver whose library is there
    sdr = pysdrlib.open(pysdrlib.find()[0])          # or pysdrlib.open("<driver>", serial="...")
"""
import importlib
import pathlib
import re
from dataclasses import dataclass, field
from typing import Optional

from . import err, logger
from .base.device import Device

_log = logger.new("devices")

# Packages with a `Device` that aren't local hardware drivers: they read files, or reach a driver elsewhere. `get` and
# `device` load them ("remote" is `pysdrlib.device("remote", address, driver)`); `ls` doesn't list them.
NOT_DRIVERS = ("file", "remote")

def _has_device(path: pathlib.Path) -> bool:
    """The package declares `Device = <class>` in its __init__.py (read, not imported: importing a driver may build its
    vendor library). One without (a stub, `base`, `_native`...) isn't a device package."""
    init = path / "__init__.py"
    if path.name.startswith(("_", ".")) or not init.is_file():
        return False
    try:
        return re.search(r"^Device\s*=", init.read_text(encoding="utf-8"), re.MULTILINE) is not None
    except OSError:
        return False

def _packages():
    return sorted(p.name for p in pathlib.Path(__file__).parent.iterdir() if p.is_dir() and _has_device(p))

def ls():
    """Driver names: every package here whose `__init__.py` says `Device = ...`. Adding a driver needs no list edited."""
    return [name for name in _packages() if name not in NOT_DRIVERS]

def get(name: str):
    """The device package `name` (a driver, or "file" / "remote"). Raises err.NoDevice, naming the drivers there are,
    for one that doesn't exist"""
    if name not in _packages():
        raise err.NoDevice(f"no driver {name!r} (drivers: {', '.join(ls())})")
    return importlib.import_module(f".{name}", "pysdrlib")

def device(name: str, *args, **kwargs) -> Device:
    """A new, unopened device of driver `name` (see `ls`); the arguments go to its constructor.
    Call `open()` on it before use. `pysdrlib.open` is the shortcut that also finds and opens an attached radio"""
    return get(name).Device(*args, **kwargs)

@dataclass(frozen=True)
class DeviceInfo:
    """One attached radio. open_args: what `Device.open` needs to get this unit (its serial, or index)"""
    driver: str
    label: str
    serial: Optional[str] = None
    index: Optional[int] = None
    open_args: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)

    def open(self, **open_args) -> Device:
        """A Device for this unit, opened"""
        sdr = device(self.driver)
        sdr.open(**{**self.open_args, **open_args})
        return sdr

def _library_ready(name, build):
    """The driver's vendor library loads without being built first (or `build` allows building it)"""
    try:
        ext = importlib.import_module(f"._ext.{name}", "pysdrlib")
    except ModuleNotFoundError:
        return True   # no vendor library (simulated devices)
    lib = getattr(ext, "LIB", None)
    return build or lib is None or lib.available()

def find(driver: Optional[str] = None, build=False, virtual=False) -> list:
    """The radios attached to this machine, as DeviceInfo. Only drivers whose vendor library is installed or was
    built before are asked (importing a driver otherwise builds it; `build=True` allows that).
    virtual: also list simulated devices"""
    found = []
    for name in ([driver] if driver else ls()):
        if not _library_ready(name, build):
            _log.debug("find: skipping %s (its library isn't installed or built)", name)
            continue
        try:
            finder = getattr(get(name), "find", None)
            if finder is None:
                continue
            found += [info for info in finder() if virtual or not info.extra.get("virtual")]
        except Exception as exc:   # a driver that can't be loaded or asked has nothing attached we can use
            _log.debug("find: %s: %s", name, exc)
    return found

def open(what, serial=None, **open_args) -> Device:
    """An opened device: from a DeviceInfo (see `find`), or a driver name (`serial` picks the unit)"""
    if serial is not None:
        open_args["serial"] = serial
    if isinstance(what, DeviceInfo):
        return what.open(**open_args)
    sdr = device(what)
    sdr.open(**open_args)
    return sdr
