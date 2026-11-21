"""Finding servers on the local network with mDNS / DNS-SD (needs `zeroconf`: `pip install pysdrlib[remote]`)

A server started with `advertise=True` (`pysdrlib server --advertise`) announces itself as a `_pysdrlib._tcp` service;
`discover()` lists the ones that answer. The announcement only says where a server is (and its protocol version): ask it
what it has with `Client(address).list()` as usual. mDNS does not cross routers, so a server on another subnet or over a
VPN still has to be given by address.
"""
import ipaddress
import socket
import time

from .. import err, logger
from . import protocol

SERVICE = "_pysdrlib._tcp.local."

class NotAdvertisable(ValueError):
    """The server's endpoint is not one other machines could use (ipc://, loopback, or a host that does not resolve)"""

def _zeroconf():
    try:
        import zeroconf
    except ImportError as e:
        raise err.NotSupported("finding servers needs zeroconf: pip install pysdrlib[remote]") from e
    return zeroconf

def _short_host():
    return socket.gethostname().split(".")[0] or "pysdrlib"

def _interface_addresses():
    """Every IPv4 address of this machine's interfaces, loopback left out"""
    import ifaddr    # comes with zeroconf
    found = []
    for adapter in ifaddr.get_adapters():
        for ip in adapter.ips:
            if isinstance(ip.ip, str) and not ipaddress.ip_address(ip.ip).is_loopback and ip.ip not in found:
                found.append(ip.ip)
    return found

def _local_networks():
    """The IPv4 networks this machine's interfaces are on"""
    import ifaddr
    return [ipaddress.ip_network(f"{ip.ip}/{ip.network_prefix}", strict=False)
            for adapter in ifaddr.get_adapters() for ip in adapter.ips if isinstance(ip.ip, str)]

def _reachable_first(addresses):
    """`addresses` with the ones on a network this machine is on first (a server on several networks announces them all)"""
    nets = _local_networks()
    return sorted(addresses, key=lambda a: not any(ipaddress.ip_address(a) in n for n in nets))

def _addresses(endpoint):
    """-> (addresses to announce, port) for a bound C2 endpoint; NotAdvertisable when nobody else could reach it"""
    scheme, _, rest = endpoint.partition("://")
    if scheme != "tcp":
        raise NotAdvertisable(f"only a tcp server can be advertised, not {endpoint}")
    host, _, port = rest.rpartition(":")
    if host in ("*", "0.0.0.0"):
        addresses = _interface_addresses()
    else:
        try:
            addresses = [socket.gethostbyname(host)]
        except OSError as e:
            raise NotAdvertisable(f"cannot resolve {host!r} to advertise it: {e}") from e
    addresses = [a for a in addresses if not ipaddress.ip_address(a).is_loopback]
    if not addresses:
        raise NotAdvertisable(f"{endpoint} is only reachable from this machine; bind e.g. tcp://0.0.0.0:{port} to advertise it")
    return addresses, int(port)


class Advertiser:
    """Announces a server's C2 endpoint on the local network until `stop`"""
    def __init__(self, endpoint, name=None, kind="server"):
        """endpoint: the bound C2 endpoint (`Server.endpoint`). name: the instance name shown by `discover` (default: the
        host name, with " relay" for a relay; a name already taken gets a number added). kind: "server" or "relay"."""
        zc = _zeroconf()
        self.log = logger.new("remote.discovery")
        self.addresses, self.port = _addresses(endpoint)
        from .. import __version__
        name = (name or (_short_host() + (" relay" if kind == "relay" else ""))).replace(".", "-")
        self.info = zc.ServiceInfo(
            SERVICE, f"{name}.{SERVICE}",
            addresses=[socket.inet_aton(a) for a in self.addresses], port=self.port,
            properties={"proto": str(protocol.VERSION), "ver": __version__, "kind": kind},
            server=f"{_short_host()}.local.")
        self._zc = None

    @property
    def name(self):
        """The instance name in use (it may differ from the one asked for when that was taken)"""
        return self.info.name[:-len(SERVICE) - 1]

    def start(self):
        if self._zc is None:
            zc = _zeroconf()
            self._zc = zc.Zeroconf(interfaces=zc.InterfaceChoice.All, ip_version=zc.IPVersion.V4Only)
            self._zc.register_service(self.info, allow_name_change=True)
            self.log.info("advertising %r on %s port %d", self.name, ", ".join(self.addresses), self.port)
        return self

    def stop(self):
        """Withdraw the announcement (clients see the server go at once) and close"""
        if self._zc is not None:
            try:
                self._zc.unregister_service(self.info)
            finally:
                self._zc.close()
                self._zc = None


def discover(timeout=2.0, all_versions=False) -> list[dict]:
    """Servers announcing themselves on the local network, after listening for `timeout` seconds.

    Each is a dict: `name`, `address` (a C2 endpoint to give `Client` or `pysdrlib.connect`: one on a network this machine
    is on, when there is one), `addresses` (every endpoint it announced, for a server on several networks), `host`, `kind` ("server" or "relay"), `proto` and `version`.
    Servers speaking another protocol version than this pysdrlib are left out unless `all_versions`.
    """
    zc = _zeroconf()
    names = set()

    def changed(zeroconf, service_type, name, state_change):
        if state_change is zc.ServiceStateChange.Added:
            names.add(name)
        elif state_change is zc.ServiceStateChange.Removed:
            names.discard(name)

    found = []
    with zc.Zeroconf(interfaces=zc.InterfaceChoice.All, ip_version=zc.IPVersion.V4Only) as z:
        browser = zc.ServiceBrowser(z, SERVICE, handlers=[changed])
        try:
            time.sleep(timeout)
        finally:
            browser.cancel()
        for name in sorted(names):
            info = z.get_service_info(SERVICE, name, timeout=int(max(timeout, 0.5) * 1000))
            if info is None or not info.port:
                continue
            props = {k.decode(): (v.decode() if v is not None else None) for k, v in info.properties.items()}
            try:
                proto = int(props.get("proto") or 0)
            except ValueError:
                proto = 0
            if proto != protocol.VERSION and not all_versions:
                continue
            addresses = [f"tcp://{a}:{info.port}" for a in _reachable_first(info.parsed_addresses())]
            if not addresses:
                continue
            found.append({"name": name[:-len(SERVICE) - 1], "address": addresses[0], "addresses": addresses,
                          "host": (info.server or "").rstrip("."), "kind": props.get("kind") or "server",
                          "proto": proto, "version": props.get("ver")})
    return found


def _is_local(address):
    host = address.partition("://")[2].rpartition(":")[0]
    try:
        return ipaddress.ip_address(host).is_loopback or host in _interface_addresses()
    except ValueError:
        return False

def rank(servers, device=None, serial=None, label=None, need=None, want=None, timeout=5.0):
    """Ask each of `servers` (from `discover`) for its radios that fit the request, without opening any.

    -> (choices, hosting, reasons): `choices` every radio that fits on any server, best first (by `choose.rank`, then one
    attached before one a client left open, then one on this machine, which can share samples through shared memory), as
    dicts of its server and the `candidate` (from `Client.candidates`: open it with its `key` as `pick`). A server too old
    to be asked, or that has no `device` attached but can open one (as a plain `connect(address, device)` would), is there
    once with `candidate` None, after the rest; `hosting` the
    servers that host a device whose id is `device`; `reasons` why each other server was left out, as text.
    """
    from .client import Client
    ranked, hosting, reasons = [], [], []
    for i, server in enumerate(servers):
        where = f"{server['address']} ({server['name']})"
        try:
            with Client(server["address"], timeout) as c:
                if device is not None and device in {d["id"] for d in c.list()}:
                    hosting.append(server)
                    continue
                if device is not None and device not in c.types():
                    reasons.append(f"{where}: cannot open {device!r}")
                    continue
                choosing = any(v is not None for v in (serial, label, need, want))
                try:
                    offered = c.candidates(device, serial, label, need, want)
                except err.RemoteError as e:      # a server from before `choose`: it may still have one
                    if "unknown op" not in str(e):
                        raise
                    offered = [None]
                except err.NoDevice:
                    if choosing or device is None:
                        raise
                    offered = [None]    # none of the type attached, but it can open one (a driver nothing finds, like sim)
        except err.NoDevice as e:
            reasons.append(f"{where}: {e}")
            continue
        except (err._Error, TimeoutError, OSError) as e:
            reasons.append(f"{where}: {type(e).__name__}: {e}")
            continue
        local = _is_local(server["address"])
        for j, cand in enumerate(offered):
            key = (1,) if cand is None else (0, *cand["rank"], cand["kind"] == "hosted")
            ranked.append((key, not local, i, j, {**server, "candidate": cand}))
    ranked.sort(key=lambda r: r[:4])
    return [r[4] for r in ranked], hosting, reasons
