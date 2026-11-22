"""Drivers, and the devices attached to this machine

    pysdrlib.devices.ls()            # driver names
    pysdrlib.find()                  # [DeviceInfo]: the radios plugged in, for every driver whose library is there
    pysdrlib.find(addresses=["192.168.10.2"])   # ... and ones on the network, for drivers that can reach those
    sdr = pysdrlib.connect("ipc:///tmp/pysdrlib", "<driver>")   # a radio hosted by a `pysdrlib server` (this machine or another)
    sdr = pysdrlib.open(pysdrlib.find()[0])          # or pysdrlib.open("<driver>", serial="...")

## Drivers from outside pysdrlib

A driver needs no change to this package. Register a `Device` subclass in a script,

    @pysdrlib.register("tcpiq")
    class TcpIq(pysdrlib.Device): ...

    sdr = pysdrlib.open("tcpiq", host="10.0.0.5", port=1234)      # `open` arguments go to the class's `_open`

or ship it in a package that declares an entry point, which makes it known to every program once installed:

    [project.entry-points."pysdrlib.drivers"]
    tcpiq = "my_package.tcpiq:TcpIq"

Entry points are only read (not imported) until a driver is used. A driver that fails to import is reported by `get`,
and skipped by `find`. A name is lower case letters, digits and underscores, and can't replace a driver pysdrlib ships
(unless `register(..., replace=True)`). `PYSDRLIB_NO_PLUGINS=1` ignores entry points.
"""
import importlib
import importlib.metadata
import inspect
import os
import pathlib
import re
import sys
import types
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

# Drivers that are a class of a package another driver is made of (not a package of their own): name -> (the package
# whose library they need, "module:Class"). UHD's network radios are `pysdrlib.uhd`'s X310 and X410 classes.
_ALIASES = {
    "x310": ("uhd", "pysdrlib.uhd:X310"),
    "x410": ("uhd", "pysdrlib.uhd:X410"),
}

def _shipped() -> set:
    return set(_packages()) | set(_ALIASES)

# --- drivers from outside this package (`register`, and the "pysdrlib.drivers" entry points) --- #
ENTRY_POINT_GROUP = "pysdrlib.drivers"
_NAME = re.compile(r"[a-z][a-z0-9_]*")

@dataclass
class _Registered:
    name: str
    target: object                  # a Device subclass, or "module:Class" (or "module", with a `Device` in it)
    source: str                     # where it came from, for messages
    module: Optional[types.ModuleType] = None   # what `get` returns, made when first asked for

_registered: dict = {}
_alias_entries = {name: _Registered(name, target, "pysdrlib") for name, (_, target) in _ALIASES.items()}
_entry_points_read = False

def _read_entry_points():
    """Know the drivers installed packages declare. Reads their names only: nothing is imported until one is used"""
    global _entry_points_read
    if _entry_points_read:
        return
    _entry_points_read = True
    if os.environ.get("PYSDRLIB_NO_PLUGINS", "") not in ("", "0"):
        return
    try:
        points = importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)
    except Exception as exc:   # a broken installation of some other package must not break this one
        _log.warning("could not read the %r entry points: %s", ENTRY_POINT_GROUP, exc)
        return
    shipped = _shipped()
    for point in points:
        if not _NAME.fullmatch(point.name):
            _log.warning("ignoring the driver %r (%s): a name is lower case letters, digits and underscores", point.name, point.value)
        elif point.name in shipped:
            _log.warning("ignoring the driver %r (%s): pysdrlib has a driver of that name", point.name, point.value)
        elif point.name in _registered:
            _log.debug("driver %r: %s is registered already, not using %s", point.name, _registered[point.name].source, point.value)
        else:
            _registered[point.name] = _Registered(point.name, point.value, f"entry point {point.value}")

def register(name: str, target=None, *, replace=False):
    """Make a driver known by `name`, to `ls`, `get`, `find` and `open`. Use it as a decorator on a `Device` subclass, or
    give `target`: that class, or a "module:Class" string (imported only when the driver is used)

        @pysdrlib.register("tcpiq")
        class TcpIq(pysdrlib.Device): ...

        pysdrlib.register("tcpiq", "my_package.tcpiq:TcpIq")

    Raises ValueError for a name already taken (a shipped driver, or another registered) unless `replace`; registering the
    same target again is not an error. What `pysdrlib.find` lists for it is the class's `find()` classmethod, which lists
    nothing unless you write it: a source that can't be enumerated (a TCP stream) is opened by name, with
    `pysdrlib.open(name, ...)`"""
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError(f"{name!r}: a driver name is lower case letters, digits and underscores, starting with a letter")
    def add(target):
        if isinstance(target, type):
            if not issubclass(target, Device):
                raise TypeError(f"{target!r} is not a pysdrlib Device subclass")
        elif not isinstance(target, str) or not target:
            raise TypeError("a driver is a Device subclass, or a 'module:Class' string")
        taken = _registered.get(name)
        if (taken is not None or name in _shipped()) and not replace:
            if taken is not None and taken.target == target:
                return target
            raise ValueError(f"driver {name!r} is already taken by {taken.source if taken else 'pysdrlib'}; "
                             "pass replace=True to take it over")
        _registered[name] = _Registered(name, target, "register()")
        return target
    return add if target is None else add(target)

def unregister(name: str) -> bool:
    """Forget a driver made known by `register` or an entry point (a shipped driver it replaced is back). False if
    there was none"""
    return _registered.pop(name, None) is not None

def _resolve(entry: _Registered) -> types.ModuleType:
    """The driver of a registered entry as what `get` returns: an object with `Device` and `find`"""
    if entry.module is not None:
        return entry.module
    target, finder = entry.target, None
    try:
        if isinstance(target, str):
            module_name, _, attr = target.partition(":")
            module = importlib.import_module(module_name)
            if not attr:
                finder = getattr(module, "find", None)
            target = module
            for part in (attr or "Device").split("."):
                target = getattr(target, part)
    except Exception as exc:
        raise err.NoDevice(f"driver {entry.name!r} ({entry.source}) failed to load: {exc!r}") from exc
    if not (isinstance(target, type) and issubclass(target, Device)):
        raise err.NoDevice(f"driver {entry.name!r} ({entry.source}) is {target!r}, not a pysdrlib Device subclass")
    driver = types.ModuleType(f"pysdrlib.drivers.{entry.name}", target.__doc__)
    driver.Device = target
    driver.find = finder or target.find
    driver.__file__ = getattr(sys.modules.get(target.__module__), "__file__", None)
    entry.module = driver
    return driver

def ls():
    """Driver names: every package here whose `__init__.py` says `Device = ...` (adding one needs no list edited), and
    the ones `register`ed or installed with a "pysdrlib.drivers" entry point"""
    _read_entry_points()
    shipped = [name for name in _packages() if name not in NOT_DRIVERS]
    return sorted(set(shipped) | set(_ALIASES) | set(_registered))

def get(name: str):
    """The device package `name` (a driver, or "file" / "remote"): something with `Device` and `find`. Raises
    err.NoDevice, naming the drivers there are, for one that doesn't exist (or a registered one that can't be imported)"""
    _read_entry_points()
    if name in _registered:
        return _resolve(_registered[name])
    if name in _alias_entries:
        return _resolve(_alias_entries[name])
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
    if name in _registered:
        return True   # a driver from outside has no vendor library of ours; one it needs fails when it is imported or opened
    name = _ALIASES[name][0] if name in _ALIASES else name   # a class of another driver: that driver's library
    try:
        ext = importlib.import_module(f"._ext.{name}", "pysdrlib")
    except ModuleNotFoundError:
        return True   # no vendor library (simulated devices)
    lib = getattr(ext, "LIB", None)
    if lib is None or lib.available():
        return True
    if not build:
        return False
    try:
        lib.load(build=True)   # what `find(build=True)` allows: building the vendor library now
    except RuntimeError as exc:
        _log.debug("find: %s: %s", name, exc)
        return False
    return True

def _finder_args(finder, addresses, timeout):
    """The keywords of `find` that this driver's finder takes (a finder with none is called plain)"""
    try:
        params = inspect.signature(finder).parameters
    except (TypeError, ValueError):
        return {}
    any_keyword = any(p.kind is p.VAR_KEYWORD for p in params.values())
    wanted = {"addresses": addresses, "timeout": timeout}
    return {k: v for k, v in wanted.items() if v is not None and (k in params or any_keyword)}

def find(driver: Optional[str] = None, build=False, virtual=False, addresses=None, timeout=None) -> list:
    """The radios attached to this machine, as DeviceInfo. Only drivers whose vendor library is installed or was
    built before are asked (`pysdrlib build <driver>` builds one; `build=True` builds the ones that are missing now).
    virtual: also list simulated devices.
    addresses: network addresses (a host or "host:port", or a list of them) to look at as well. Only drivers for radios
    that can be on a network use them (their `find` takes `addresses`); the rest list what is attached here, as always.
    Without addresses nothing is probed over a network.
    timeout: seconds a driver may wait for a network answer (drivers whose `find` takes `timeout`)"""
    if isinstance(addresses, str):
        addresses = [addresses]
    addresses = list(addresses) if addresses else None
    found = []
    for name in ([driver] if driver else [n for n in ls() if n not in _ALIASES]):   # (a class of a driver is listed by it)
        if not _library_ready(name, build):
            _log.debug("find: skipping %s (its library isn't installed or built)", name)
            continue
        try:
            finder = getattr(get(name), "find", None)
            if finder is None:
                continue
            for info in finder(**_finder_args(finder, addresses, timeout)):
                if (virtual or not info.extra.get("virtual")) and info not in found:   # a driver may list another's units too
                    found.append(info)
        except Exception as exc:   # a driver that can't be loaded or asked has nothing attached we can use
            _log.debug("find: %s: %s", name, exc)
    return found

def connect(address: Optional[str] = None, device: Optional[str] = None, *, claim=False, reuse=False, **kwargs) -> Device:
    """An opened device hosted by a `pysdrlib server` at `address` (see `pysdrlib.remote`): on this machine, where it is shared
    through shared memory, or on another. It has the API of any other device.

        sdr = pysdrlib.connect("tcp://radiohost:5555", "<driver>")      # a new one there: you control it
        sdr = pysdrlib.connect("tcp://radiohost:5555", "<driver>-0")    # one it already hosts: you may receive from it
        sdr = pysdrlib.connect("tcp://radiohost:5555", "<driver>-0", claim=True)   # ... and control it if it is free

    device: a device type the server can open, or the id of a device it hosts (`pysdrlib client ADDRESS list` shows both).
        Left out, the server picks any radio that fits `serial`, `label`, `need` and `want`.
    claim: for a hosted device, take control of it (`err.Busy` if someone has it).
    reuse: for a type, take over a hosted device of that type nobody controls (one a client left open) before opening a new one.
    kwargs: the rest go to `pysdrlib.remote.client.RemoteDevice` (`transport`, `timeout`, `wire`, `init_kwargs`...), among
    them the ones that say who you are and what you need, so the server can pick a radio:

        sdr = pysdrlib.connect(addr, program="pyspecan", need={"freq": 433e6, "rate": 2e6}, want={"rate": 10e6})
        sdr = pysdrlib.connect(addr, "<driver>", serial="SERIAL", program="pyspecan")
        sdr.granted                         # what the radio can do nearest to what you wanted

    `program` names your program (the server shows it in `list`), `serial`/`label` choose a unit, `need`/`want` are dicts of
    `freq`, `rate`, `bandwidth`, `channels`, `tx`: no radio that cannot meet `need` is used (`err.NoDevice` says why each
    was left out), and of the rest the one nearest `want` is.
    Raises `err.NoDevice`, naming what the server has, for a device it neither hosts nor can open.

    address None: find the servers announcing themselves on the local network (`pysdrlib.remote.discover`, which needs
    zeroconf) and use the one with the best radio for the request, by the same rules a server picks among its own:

        sdr = pysdrlib.connect(need={"freq": 433e6}, want={"rate": 10e6}, program="pyspecan")   # wherever it fits best
        sdr = pysdrlib.connect(device="<driver>")      # a server that has one attached
        sdr.address                                     # the server it is on

    A radio attached comes before one a client left open, and a server on this machine before another; a server that
    fails to open it is passed over for the next. `discover_timeout` (default 2) is how many seconds to listen for servers.
    """
    try:
        from .remote import Client, RemoteDevice
    except ImportError as exc:
        raise ImportError("connecting to a server needs pyzmq: pip install pysdrlib[remote]") from exc
    if address is None:
        return _connect_anywhere(device, claim, reuse, **kwargs)
    with Client(address, kwargs.get("timeout", 5.0)) as client:
        hosted, types = client.list(), client.types()
    ids = {d["id"]: d for d in hosted}
    choosing = any(kwargs.get(k) is not None for k in ("serial", "label", "need", "want", "pick"))
    if device is None or (choosing and device not in ids):
        if device is not None and device not in types:
            raise err.NoDevice(f"{address} cannot open a device of type {device!r} (types: {', '.join(types) or 'none'})")
        dev = RemoteDevice(address, device, **kwargs)
        dev.open()
        return dev
    if device in ids:
        dev = RemoteDevice(address, id=device, **kwargs)
        dev.open()
        if claim:
            try:
                dev.claim()
            except Exception:
                dev.close()
                raise
        return dev
    if device not in types:
        raise err.NoDevice(f"{address} hosts no device {device!r} and cannot open one of that type "
                           f"(hosted: {', '.join(ids) or 'none'}; types: {', '.join(types) or 'none'})")
    free = [d["id"] for d in hosted if d["type"] == device and not d["controlled"]]
    if reuse and free:
        return connect(address, free[0], claim=True, **kwargs)
    dev = RemoteDevice(address, device, **kwargs)
    dev.open()
    return dev

def _connect_anywhere(device, claim, reuse, discover_timeout=2.0, **kwargs) -> Device:
    """`connect` with no address: the best server on the local network for the request"""
    from .remote import discovery
    servers = discovery.discover(discover_timeout)
    if not servers:
        raise err.NoDevice("no pysdrlib server announces itself on this network: start one with --advertise, or give its address")
    choices, hosting, reasons = discovery.rank(servers, device, kwargs.get("serial"), kwargs.get("label"), kwargs.get("need"),
                                               kwargs.get("want"), kwargs.get("timeout", 5.0))
    if hosting:
        if len(hosting) > 1:
            raise err.NoDevice(f"{device!r} is hosted by several servers, give the address of one: "
                               + ", ".join(f"{s['address']} ({s['name']})" for s in hosting))
        return connect(hosting[0]["address"], device, claim=claim, reuse=reuse, **kwargs)
    for server in choices:      # each fitting radio of every server, best first
        best = server["candidate"]
        # Open the radio that was ranked, and no other: if it was taken since, the server must not open its next best in
        # its place (another server's radio may be better than that), so it says Busy and the next radio here is tried.
        pick = {"pick": best["key"]} if best is not None and best.get("key") else {}
        try:
            dev = connect(server["address"], device, claim=claim, reuse=reuse, **kwargs, **pick)
        except (err._Error, TimeoutError, OSError) as e:
            what = f"{best['label']} " if best is not None else ""
            reasons.append(f"{server['address']} ({server['name']}): {what}{type(e).__name__}: {e}")
            continue
        _log.info("connected to %s on %s (%s)", dev.id, server["address"], server["name"])
        return dev
    raise err.NoDevice(f"no server on this network has a radio for the request: {' | '.join(reasons)}")

def open(what, serial=None, **open_args) -> Device:
    """An opened device: from a DeviceInfo (see `find`), a `Radio` (see `discover`), a driver name (`serial` picks the
    unit), or a target string (see `pysdrlib.targets`):

        pysdrlib.open("<driver>@<serial>")              # attached here, by (the end of) its serial
        pysdrlib.open("radiohost/<driver>-0")           # hosted by a server: attach to it
        pysdrlib.open("radiohost/<driver>@<serial>", program="pyspecan")   # attached to a server: open it there

    For a device on a server, the keywords go to `connect` (`claim`, `reuse`, `program`, `need`, `timeout`...); here,
    to `Device.open`"""
    from . import targets
    if isinstance(what, targets.Radio):
        what = what.target
    if isinstance(what, DeviceInfo):
        if serial is not None:
            open_args["serial"] = serial
        return what.open(**open_args)
    if isinstance(what, str) and ("/" in what or "@" in what):
        t = targets.parse_target(what)
        serial = serial if serial is not None else t.serial
        if t.server is not None:
            if serial is not None:
                open_args["serial"] = serial
            return connect(t.server, t.device, **open_args)
        what = t.device
    if serial is not None:
        open_args["serial"] = serial
    sdr = device(what)
    sdr.open(**open_args)
    return sdr
