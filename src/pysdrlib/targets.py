"""Finding radios, here and on servers, and naming each with one string `pysdrlib.open` takes

    for r in pysdrlib.discover():          # attached here, and on the servers in PYSDRLIB_SERVERS
        print(r.label, r.target, r.state, r.controlled)
    sdr = pysdrlib.open(r.target)

A target is `[server/]device`:

| Target | Opens |
| --- | --- |
| `<driver>` | the first radio of that driver attached here |
| `<driver>@<serial>` | the one with that serial (or the end of it) |
| `radiohost/<driver>-0` | the device a server hosts as `<driver>-0`: attach to it (receive; `claim=True` to control it) |
| `radiohost:5556/<driver>@<serial>` | a radio attached to that server: it opens it, and you control it |
| `radiohost/<driver>` | any radio of that driver the server has (or can open) |
| `tcp://10.0.0.5/<driver>@<serial>` | the same with a scheme |
| `ipc:///run/pysdrlib/c2/sim-0` | over a local socket (its path ends at the last `/`, so an ipc target names a device) |

A server is `host`, `host:port` (port 5555 when left out) or a ZeroMQ endpoint (`tcp://...`, `ipc://...`).
`PYSDRLIB_SERVERS` lists the servers `discover` asks by default, separated by commas or whitespace.
"""
import concurrent.futures
import os
import re
from dataclasses import dataclass, field
from typing import Optional

from . import logger

SERVERS_ENV = "PYSDRLIB_SERVERS"
DEFAULT_PORT = 5555

_log = logger.new("targets")


@dataclass(frozen=True)
class Target:
    """A parsed target: `server` (a ZeroMQ endpoint, None: this machine), `device` (a driver name, or the id of a device a
    server hosts; None: whatever the server picks) and `serial`"""
    server: Optional[str]
    device: Optional[str]
    serial: Optional[str] = None

    def __str__(self):
        return format_target(self.server, self.device, self.serial)


def endpoint(server: str) -> str:
    """A server as a ZeroMQ endpoint: "radiohost" -> "tcp://radiohost:5555", "radiohost:5556" -> "tcp://radiohost:5556";
    an endpoint (tcp://, ipc://...) is kept, with the default port added to a tcp:// one without"""
    server = server.strip().rstrip("/")
    if not server:
        raise ValueError("an empty server address")
    if "://" not in server:
        server = "tcp://" + server
    scheme, rest = server.split("://", 1)
    if scheme == "tcp" and not re.search(r":\d+$", rest):
        server = f"{server}:{DEFAULT_PORT}"
    return server


def short(server: str) -> str:
    """How a server is written in a target: "tcp://radiohost:5555" -> "radiohost", "tcp://host:5556" -> "host:5556"
    (other endpoints as they are)"""
    server = endpoint(server)
    if server.startswith("tcp://"):
        rest = server[len("tcp://"):]
        return rest[:-len(f":{DEFAULT_PORT}")] if rest.endswith(f":{DEFAULT_PORT}") else rest
    return server


def parse_target(text: str) -> Target:
    """`[server/]device[@serial]` -> Target (see the module's table). A server alone ("tcp://radiohost", "radiohost/") has
    no device: the server picks"""
    text = str(text).strip()
    if not text:
        raise ValueError("an empty target")
    scheme_only = "://" in text and "/" not in text.split("://", 1)[1]
    if scheme_only:
        server, device = text, ""
    elif "/" in text:
        server, device = text.rsplit("/", 1)
    else:
        server, device = None, text
    serial = None
    if "@" in device:
        device, serial = device.split("@", 1)
        serial = serial or None
    if server is not None:
        server = endpoint(server)
    if server is None and not device:
        raise ValueError(f"target {text!r} names no device")
    return Target(server, device or None, serial)


def format_target(server: Optional[str], device: Optional[str], serial: Optional[str] = None) -> str:
    """The target string for a device (None: whatever the server picks) on `server` (None: this machine)"""
    name = (device or "") + (f"@{serial}" if serial else "")
    return name if server is None else f"{short(server)}/{name}"


def servers(value: Optional[str] = None) -> list:
    """The server endpoints in `value`, or in $PYSDRLIB_SERVERS when it is None: addresses separated by commas or
    whitespace"""
    if value is None:
        value = os.environ.get(SERVERS_ENV, "")
    return [endpoint(s) for s in re.split(r"[,\s]+", value) if s]


_servers = servers   # (discover's argument has the name)


@dataclass(frozen=True)
class Radio:
    """A radio `discover` found. target: what `pysdrlib.open` takes to open it. state: "attached" (nobody has it open:
    opening it makes you its controller), "hosted" (a server has it open: you attach to its stream; see `controlled`)
    or "openable" (a kind of device the server can open that isn't a radio it lists: simulated, network, files...).
    controlled: whether a client controls a hosted one (None otherwise), and `program` which. antennas: what is known of
    the receive chains ([{"port", "name", "antenna"}]: the antenna's description, or None)"""
    label: str
    target: str
    state: str
    driver: str
    serial: Optional[str] = None
    server: Optional[str] = None
    id: Optional[str] = None
    controlled: Optional[bool] = None
    program: Optional[str] = None
    antennas: tuple = ()
    extra: dict = field(default_factory=dict, compare=False)


def _chains(chains):
    return tuple({"port": c.get("port"), "name": c.get("name"), "antenna": c.get("antenna")}
                 for c in chains if c.get("direction", "rx") == "rx")


def _local_antennas(driver):
    try:
        from . import devices
        caps = devices.get(driver).Device().caps
        return _chains([{"direction": c.direction, "port": c.port, "name": c.name, "antenna": c.describe()}
                        for c in caps.chains])
    except Exception:
        return ()


def _local(virtual):
    from . import devices
    out = []
    for info in devices.find(virtual=virtual):
        out.append(Radio(label=info.label, target=format_target(None, info.driver, info.serial), state="attached",
                         driver=info.driver, serial=info.serial, antennas=_local_antennas(info.driver),
                         extra=dict(info.extra)))
    return out


def _remote(server, timeout):
    from .remote import Client
    with Client(server, timeout) as c:
        listing = c._c2.request("list")
    where = short(server)
    out = []
    for d in listing.get("devices", []):
        serial = d.get("serial")
        label = f"{d.get('name') or d['type']}{f' {serial}' if serial else ''} ({d['id']}) on {where}"
        out.append(Radio(label=label, target=format_target(server, d["id"]), state="hosted", driver=d["type"],
                         serial=serial, server=server, id=d["id"], controlled=bool(d.get("controlled")),
                         program=d.get("program"), antennas=_chains(d.get("chains", [])),
                         extra={k: d.get(k) for k in ("cf", "Fs", "state", "health") if k in d}))
    attached = set()
    for a in listing.get("available", []):
        attached.add(a["driver"])
        if a.get("hosted"):
            continue   # listed above, as the device that has it open
        out.append(Radio(label=f"{a['label']} on {where}", target=format_target(server, a["driver"], a.get("serial")),
                         state="attached", driver=a["driver"], serial=a.get("serial"), server=server))
    for t in listing.get("types", []):
        if t not in attached:
            out.append(Radio(label=f"{t} on {where}", target=format_target(server, t), state="openable", driver=t,
                             server=server))
    return out


def discover(servers=None, local=True, zeroconf=False, timeout=2.0, virtual=False, errors=None) -> list:
    """The radios attached here and on servers, as `Radio`s (each with the `target` `pysdrlib.open` takes):

        for r in pysdrlib.discover(servers=["radiohost", "10.0.0.5:5556"]):
            print(r.label, r.target, r.state, r.controlled, r.antennas)

    servers: addresses (see `endpoint`), or a string of them; None: $PYSDRLIB_SERVERS. Asked at once, each given
    `timeout` seconds; one that doesn't answer is left out (and, when `errors` is a list, (server, exception) is
    added to it). local: list the radios attached here (`pysdrlib.find`; `virtual` adds simulated ones).
    zeroconf: also the servers announcing themselves on the local network (needs zeroconf)"""
    if servers is None or isinstance(servers, str):
        addresses = _servers(servers)
    else:
        addresses = [endpoint(s) for s in servers]
    if zeroconf:
        from .remote import discovery
        for s in discovery.discover(timeout):
            addresses.append(endpoint(s["address"]))
    addresses = list(dict.fromkeys(addresses))   # once each, in order
    found = _local(virtual) if local else []
    if not addresses:
        return found
    try:
        import zmq  # noqa: F401
    except ImportError as exc:
        raise ImportError("asking servers needs pyzmq: pip install pysdrlib[remote]") from exc
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(16, len(addresses))) as pool:
        jobs = [(s, pool.submit(_remote, s, timeout)) for s in addresses]
        for server, job in jobs:
            try:
                found.extend(job.result())
            except Exception as exc:
                _log.info("discover: %s: %s: %s", server, type(exc).__name__, exc)
                if errors is not None:
                    errors.append((server, exc))
    return found
