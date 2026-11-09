"""Drivers, and the devices attached to this machine

    pysdrlib.devices.ls()            # driver names
    pysdrlib.find()                  # [DeviceInfo]: the radios plugged in, for every driver whose library is there
    sdr = pysdrlib.open(pysdrlib.find()[0])          # or pysdrlib.open("<driver>", serial="...")
"""
import importlib
import pathlib
from dataclasses import dataclass, field
from typing import Optional

from . import logger
from .base.device import Device

_log = logger.new("devices")

def ls():
    """Driver names"""
    vendors = []
    for file in (pathlib.Path(__file__).parent).iterdir():
        if file.is_dir():
            if file.name in ("_ext", "_native", "base", "file", "remote", "__pycache__"):
                continue
            vendors.append(file.name)
    return vendors

def get(name: str):
    try:
        module = importlib.import_module(f".{name}", "pysdrlib")
    except ModuleNotFoundError:
        return None
    return module

def device(name: str, *args, **kwargs) -> Device:
    return get(name).Device(*args, **kwargs) # type: ignore

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
