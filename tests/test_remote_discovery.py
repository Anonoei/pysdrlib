"""Finding servers with mDNS (pysdrlib.remote.discovery): needs no radio, only a network interface besides loopback"""
import sys

import pytest

pytest.importorskip("zmq")
pytest.importorskip("zeroconf")

from pysdrlib import err
from pysdrlib.remote import Client, Server, discover, discovery, protocol
from pysdrlib.remote.cli import main as cli

LISTEN = 1.5    # seconds discover() listens


def _lan_address():
    addresses = discovery._interface_addresses()
    if not addresses:
        pytest.skip("no network interface besides loopback to advertise on")
    return addresses[0]


@pytest.fixture
def advertised():
    """A server on one of this machine's own addresses (not every interface), announced as `pytest-discovery`"""
    with Server(f"tcp://{_lan_address()}:0", devices=["sim"], lease=0, advertise=True, name="pytest-discovery") as s:
        yield s


def _mine(found, server):
    return [f for f in found if f["name"] == server.advertised_name]


class TestAdvertise:
    def test_found_with_its_endpoint(self, advertised):
        mine = _mine(discover(LISTEN), advertised)
        assert len(mine) == 1
        s = mine[0]
        assert s["address"] == advertised.endpoint
        assert s["kind"] == "server"
        assert s["proto"] == protocol.VERSION
        assert s["version"]

    def test_client_asks_what_it_has(self, advertised):
        address = _mine(discover(LISTEN), advertised)[0]["address"]
        with Client(address) as c:
            assert c.types() == ["sim"]
            assert c.list() == []

    def test_gone_after_stop(self):
        server = Server(f"tcp://{_lan_address()}:0", lease=0, advertise=True, name="pytest-discovery-stop").start()
        name = server.advertised_name
        server.stop()
        assert not [f for f in discover(LISTEN) if f["name"] == name]

    def test_cli_discover(self, advertised, capsys):
        cli(["discover", "--timeout", str(LISTEN), "--list"])
        out = capsys.readouterr().out
        assert advertised.endpoint in out
        assert advertised.advertised_name in out
        assert "types: sim" in out


class TestNotAdvertisable:
    def test_loopback(self):
        with pytest.raises(discovery.NotAdvertisable, match="only reachable from this machine"):
            Server("tcp://127.0.0.1:0", advertise=True)

    @pytest.mark.skipif(sys.platform == "win32", reason="ZeroMQ has no ipc:// transport on Windows")
    def test_ipc(self, tmp_path):
        with pytest.raises(discovery.NotAdvertisable, match="only a tcp server"):
            Server(f"ipc://{tmp_path}/c2", advertise=True)

    def test_cli_says_why(self):
        with pytest.raises(SystemExit, match="--advertise: .*only reachable"):
            cli(["server", "--bind", "tcp://127.0.0.1:0", "--advertise", "--scan-interval", "0"])

    def test_not_advertising_by_default(self):
        with Server("tcp://127.0.0.1:0") as s:
            assert s.advertised_name is None


class TestHelpers:
    def test_reachable_first(self, monkeypatch):
        import ipaddress
        monkeypatch.setattr(discovery, "_local_networks", lambda: [ipaddress.ip_network("10.0.1.0/24")])
        assert discovery._reachable_first(["192.168.122.1", "10.0.1.2", "100.64.0.1"]) == \
            ["10.0.1.2", "192.168.122.1", "100.64.0.1"]

    def test_without_zeroconf(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "zeroconf", None)
        with pytest.raises(err.NotSupported, match="zeroconf"):
            discover(0)


class TestConnectAnywhere:
    def test_connects_to_an_advertised_server(self, advertised, monkeypatch):
        import pysdrlib
        real = discovery.discover
        monkeypatch.setattr(discovery, "discover",       # only this test's server, not others on the network
                            lambda *a, **k: [s for s in real(*a, **k) if s["name"] == advertised.advertised_name])
        sdr = pysdrlib.connect(device="sim", discover_timeout=LISTEN, init_kwargs={"realtime": False})
        try:
            assert sdr.address == advertised.endpoint and sdr.is_controller
        finally:
            sdr.close()

    def test_no_server_found(self, monkeypatch):
        import pysdrlib
        monkeypatch.setattr(discovery, "discover", lambda *a, **k: [])
        with pytest.raises(err.NoDevice, match="--advertise"):
            pysdrlib.connect(device="sim")
