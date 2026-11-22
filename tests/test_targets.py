"""Target strings, PYSDRLIB_SERVERS and pysdrlib.discover"""
import time

import pytest

import pysdrlib
from pysdrlib import targets
from pysdrlib.targets import Target, parse_target


@pytest.mark.parametrize("text, expect", [
    ("hackrf", Target(None, "hackrf")),
    ("hackrf@0000a06", Target(None, "hackrf", "0000a06")),
    ("radiohost/hackrf-0", Target("tcp://radiohost:5555", "hackrf-0")),
    ("radiohost:5556/hackrf@a06", Target("tcp://radiohost:5556", "hackrf", "a06")),
    ("tcp://10.0.0.5/uhd@31A2F7", Target("tcp://10.0.0.5:5555", "uhd", "31A2F7")),
    ("tcp://10.0.0.5:6000/uhd", Target("tcp://10.0.0.5:6000", "uhd")),
    ("ipc:///run/pysdrlib/c2/sim-0", Target("ipc:///run/pysdrlib/c2", "sim-0")),
    ("tcp://radiohost", Target("tcp://radiohost:5555", None)),
    ("radiohost/", Target("tcp://radiohost:5555", None)),
])
def test_parse(text, expect):
    assert parse_target(text) == expect


@pytest.mark.parametrize("text", ["hackrf", "hackrf@a06", "radiohost/hackrf-0", "radiohost:5556/hackrf@a06",
                                  "ipc:///run/c2/sim-0"])
def test_round_trip(text):
    t = parse_target(text)
    assert str(t) == text and parse_target(str(t)) == t


@pytest.mark.parametrize("text", ["", "  ", "@a06", "tcp:/hackrf", "://host:5555", "tcp://", "tcp://host:5555/a/b",
                                  "udp://host/sim", "host:99999/sim", "host:port/sim", "a b/sim", "ipc:///sim"])
def test_bad_targets(text):
    with pytest.raises(ValueError):
        parse_target(text)


@pytest.mark.parametrize("server", ["", "tcp://", "://host", "tcp://a/b", "tcp://host:0", "host:", "ipc://"])
def test_bad_servers(server):
    with pytest.raises(ValueError):
        targets.endpoint(server)


def test_ipv6_server():
    assert targets.endpoint("[::1]") == "tcp://[::1]:5555"
    assert parse_target("[fe80::1]:6000/sim-0") == Target("tcp://[fe80::1]:6000", "sim-0")


def test_tcpiq_lists_its_formats():
    from pysdrlib import tcpiq
    from pysdrlib.base import convert
    assert "cf32" in tcpiq.FORMATS and "cu8" in tcpiq.FORMATS and "ci16_be" in tcpiq.FORMATS
    for name in tcpiq.FORMATS:
        convert.sample_format(name)


def test_servers_from_the_environment(monkeypatch):
    monkeypatch.setenv("PYSDRLIB_SERVERS", "radiohost, 10.0.0.5:5556  tcp://x:7,ipc:///tmp/c2")
    assert targets.servers() == ["tcp://radiohost:5555", "tcp://10.0.0.5:5556", "tcp://x:7", "ipc:///tmp/c2"]
    monkeypatch.delenv("PYSDRLIB_SERVERS")
    assert targets.servers() == []


def test_local(monkeypatch):
    monkeypatch.delenv("PYSDRLIB_SERVERS", raising=False)
    sims = [r for r in pysdrlib.discover(virtual=True) if r.driver == "sim"]
    assert sims and sims[0].target == "sim" and sims[0].state == "attached" and sims[0].server is None
    with pysdrlib.open(sims[0]) as sdr:
        assert sdr.state["open"]


zmq = pytest.importorskip("zmq")
from pysdrlib.remote import Server  # noqa: E402


@pytest.fixture
def server():
    with Server("tcp://127.0.0.1:0", devices=["sim"], lease=0) as s:
        yield s


def test_discover_and_open_on_a_server(server, monkeypatch):
    monkeypatch.setenv("PYSDRLIB_SERVERS", server.endpoint)
    found = [r for r in pysdrlib.discover(local=False) if r.driver == "sim"]
    assert [r.state for r in found] == ["openable"]
    where = server.endpoint[len("tcp://"):]
    assert found[0].target == f"{where}/sim" and found[0].server == server.endpoint

    ctl = pysdrlib.open(found[0].target)   # opened there: its controller
    try:
        assert ctl.is_controller
        hosted = [r for r in pysdrlib.discover(local=False) if r.state == "hosted"]
        assert len(hosted) == 1 and hosted[0].controlled and hosted[0].id == ctl.id
        assert hosted[0].target == f"{where}/{ctl.id}" and "on " + where in hosted[0].label
        viewer = pysdrlib.open(hosted[0])   # attached, as a viewer
        try:
            assert viewer.id == ctl.id and not viewer.is_controller
        finally:
            viewer.close()
    finally:
        ctl.close()


def test_a_server_that_does_not_answer(server):
    errors = []
    t = time.monotonic()
    found = pysdrlib.discover(servers=[server.endpoint, "tcp://127.0.0.1:1"], local=False, timeout=0.5, errors=errors)
    assert time.monotonic() - t < 2.0   # asked at once
    assert any(r.driver == "sim" for r in found)
    assert [s for s, _ in errors] == ["tcp://127.0.0.1:1"]
