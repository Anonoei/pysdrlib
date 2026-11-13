"""Drivers from outside pysdrlib (`pysdrlib.register`, the "pysdrlib.drivers" entry points), `find`'s addresses, and tcpiq.

No hardware: the plugins are classes made here, and tcpiq talks to a server on the loopback.
"""
import importlib
import socket
import sys
import threading
import time
import tomllib
from importlib.metadata import EntryPoint

import numpy as np
import pytest

import pysdrlib
from pysdrlib import devices, err, scaffold, testing
from pysdrlib.base import convert
from pysdrlib.sim import Sim
from pysdrlib.tcpiq import TcpIq


class Plugin(Sim):
    """A driver as an outside package would write it: a Device subclass"""
    NAME = "Plugin"

    @classmethod
    def find(cls, addresses=None, timeout=None):
        seen = {"addresses": addresses, "timeout": timeout}
        return [pysdrlib.DeviceInfo("plugin", "Plugin radio", None, 0, {}, seen)]


@pytest.fixture(autouse=True)
def clean_registry(monkeypatch):
    """Every test starts with no registrations, no entry points read, and leaves none behind"""
    monkeypatch.setattr(devices, "_registered", {})
    monkeypatch.setattr(devices, "_entry_points_read", False)
    monkeypatch.delenv("PYSDRLIB_NO_PLUGINS", raising=False)
    monkeypatch.setattr(devices.importlib.metadata, "entry_points", lambda group: [])


def entry_points(monkeypatch, **points):
    monkeypatch.setattr(devices, "_entry_points_read", False)   # read afresh (they are read once per process)
    monkeypatch.setattr(devices.importlib.metadata, "entry_points",
                        lambda group: [EntryPoint(name, value, group) for name, value in points.items()])


# ---------------------------------------------------------------- register
class TestRegister:
    def test_decorator_registers_and_returns_the_class(self):
        @pysdrlib.register("plug")
        class Mine(Sim):
            pass
        assert "plug" in devices.ls()
        assert devices.get("plug").Device is Mine
        assert isinstance(pysdrlib.device("plug"), Mine)

    def test_call_with_a_class_or_a_lazy_string(self):
        assert pysdrlib.register("one", Plugin) is Plugin
        assert pysdrlib.register("two", "pysdrlib.sim:Sim") == "pysdrlib.sim:Sim"
        assert {"one", "two"} <= set(devices.ls())
        assert devices.get("two").Device is Sim
        assert devices.get("two").__name__ == "pysdrlib.drivers.two"

    def test_a_module_string_uses_its_Device_and_find(self):
        pysdrlib.register("modular", "pysdrlib.sim")
        driver = devices.get("modular")
        assert driver.Device is Sim and driver.find().__len__() == 1

    def test_lazy_string_is_not_imported_until_used(self):
        pysdrlib.register("later", "no_such_package_xyz:Thing")
        assert "later" in devices.ls()   # listing doesn't import
        with pytest.raises(err.NoDevice, match="later.*failed to load"):
            devices.get("later")

    def test_target_that_is_not_a_device_is_refused(self):
        with pytest.raises(TypeError):
            pysdrlib.register("bad", int)
        with pytest.raises(TypeError):
            pysdrlib.register("bad", 5)
        pysdrlib.register("worse", "pysdrlib.devices:ls")
        with pytest.raises(err.NoDevice, match="not a pysdrlib Device"):
            devices.get("worse")

    @pytest.mark.parametrize("name", ["", "Upper", "1st", "with-dash", "a b", None])
    def test_bad_names(self, name):
        with pytest.raises(ValueError):
            pysdrlib.register(name, Plugin)

    def test_a_shipped_driver_cant_be_taken_unless_asked(self):
        # "sim", not a hardware driver: those need a compiled extension, which not every platform has
        with pytest.raises(ValueError, match="sim"):
            pysdrlib.register("sim", Plugin)
        pysdrlib.register("sim", Plugin, replace=True)
        assert devices.get("sim").Device is Plugin
        assert devices.unregister("sim") is True
        assert devices.get("sim").__name__ == "pysdrlib.sim"   # the shipped one is back

    def test_two_registrations_of_one_name(self):
        pysdrlib.register("dup", Plugin)
        pysdrlib.register("dup", Plugin)   # the same again is harmless (a module imported twice)
        with pytest.raises(ValueError, match="already taken"):
            pysdrlib.register("dup", Sim)
        pysdrlib.register("dup", Sim, replace=True)
        assert devices.get("dup").Device is Sim

    def test_unregister(self):
        pysdrlib.register("gone", Plugin)
        assert devices.unregister("gone") and "gone" not in devices.ls()
        assert devices.unregister("gone") is False
        with pytest.raises(err.NoDevice, match="gone"):
            devices.get("gone")

    def test_file_and_remote_stay_out_of_ls(self):
        assert not {"file", "remote"} & set(devices.ls())

    def test_a_plugin_driver_passes_the_conformance_checks(self):
        pysdrlib.register("plug", Plugin)
        problems = testing.check_package("plug")
        assert not testing.errors(problems), "\n".join(map(str, problems))

    def test_open_by_name_goes_to_the_class(self):
        class Takes(Sim):
            def _open(self, host=None, port=0):
                self.target = (host, port)
                return super()._open()
        pysdrlib.register("takes", Takes)
        sdr = pysdrlib.open("takes", host="10.0.0.5", port=1234)
        assert sdr.target == ("10.0.0.5", 1234) and sdr.state["open"]
        sdr.close()


# ---------------------------------------------------------------- entry points
class TestEntryPoints:
    def test_listed_without_importing(self, monkeypatch):
        entry_points(monkeypatch, ghost="no_such_package_xyz:Ghost")
        assert "ghost" in devices.ls()
        with pytest.raises(err.NoDevice, match="ghost.*entry point.*failed to load"):
            devices.get("ghost")

    def test_resolved_when_used(self, monkeypatch):
        entry_points(monkeypatch, plugin=f"{__name__}:Plugin")
        assert devices.get("plugin").Device is Plugin
        assert isinstance(pysdrlib.device("plugin"), Plugin)

    def test_found_by_find(self, monkeypatch):
        entry_points(monkeypatch, plugin=f"{__name__}:Plugin")
        assert [i.label for i in pysdrlib.find(virtual=True) if i.driver == "plugin"] == ["Plugin radio"]

    def test_a_broken_entry_point_does_not_break_find_or_ls(self, monkeypatch):
        entry_points(monkeypatch, ghost="no_such_package_xyz:Ghost", plugin=f"{__name__}:Plugin")
        assert {"ghost", "plugin"} <= set(devices.ls())
        drivers = {i.driver for i in pysdrlib.find(virtual=True)}
        assert "plugin" in drivers and "ghost" not in drivers and "sim" in drivers

    def test_cannot_shadow_a_shipped_driver(self, monkeypatch):
        entry_points(monkeypatch, sim=f"{__name__}:Plugin")
        assert devices.get("sim").__name__ == "pysdrlib.sim"
        assert "sim" not in devices._registered

    def test_bad_name_is_ignored(self, monkeypatch):
        entry_points(monkeypatch, **{"Bad-Name": f"{__name__}:Plugin"})
        assert "Bad-Name" not in devices.ls()

    def test_register_wins_over_an_entry_point(self, monkeypatch):
        pysdrlib.register("plugin", Sim)
        entry_points(monkeypatch, plugin=f"{__name__}:Plugin")
        assert devices.get("plugin").Device is Sim

    def test_can_be_turned_off(self, monkeypatch):
        entry_points(monkeypatch, plugin=f"{__name__}:Plugin")
        monkeypatch.setenv("PYSDRLIB_NO_PLUGINS", "1")
        assert "plugin" not in devices.ls()

    def test_a_failing_entry_point_lookup_is_survived(self, monkeypatch):
        def boom(group):
            raise RuntimeError("broken metadata")
        monkeypatch.setattr(devices.importlib.metadata, "entry_points", boom)
        assert "sim" in devices.ls()

    def test_a_server_can_host_one(self, monkeypatch):
        pytest.importorskip("zmq")
        from pysdrlib.remote import Server
        entry_points(monkeypatch, plugin=f"{__name__}:Plugin")
        with Server("tcp://127.0.0.1:0", lease=0) as server:
            assert "plugin" in server.types()
            worker, _ = server.open_device("plugin")
            assert isinstance(worker.device, Plugin)


# ---------------------------------------------------------------- find(addresses, timeout)
class TestFindAddresses:
    @pytest.fixture
    def seen(self, monkeypatch):
        """Registered finders that record what they were asked: one takes both keywords, one none, one only timeout"""
        calls = {}
        def info(name):
            return pysdrlib.DeviceInfo(name, name, extra={"virtual": False})
        class Both(Sim):
            @classmethod
            def find(cls, addresses=None, timeout=None):
                calls["both"] = (addresses, timeout)
                return [info("both")]
        class Neither(Sim):
            @classmethod
            def find(cls):
                calls["neither"] = ()
                return [info("neither")]
        class Timeout(Sim):
            @classmethod
            def find(cls, timeout=None):
                calls["timeout"] = timeout
                return [info("timeout")]
        class Anything(Sim):
            @classmethod
            def find(cls, **kwargs):
                calls["anything"] = kwargs
                return [info("anything")]
        for name, cls in dict(both=Both, neither=Neither, timeout=Timeout, anything=Anything).items():
            pysdrlib.register(name, cls)
        return calls

    def test_plain_find_probes_no_network(self, seen):
        found = {i.driver for i in pysdrlib.find()}
        assert {"both", "neither", "timeout", "anything"} <= found
        assert seen == {"both": (None, None), "neither": (), "timeout": None, "anything": {}}

    def test_addresses_reach_drivers_that_take_them_and_the_rest_list_as_before(self, seen):
        found = {i.driver for i in pysdrlib.find(addresses=["192.168.10.2", "10.0.0.7:49152"], timeout=2.5)}
        assert {"both", "neither", "timeout", "anything"} <= found
        both = (["192.168.10.2", "10.0.0.7:49152"], 2.5)
        assert seen == {"both": both, "neither": (), "timeout": 2.5, "anything": {"addresses": both[0], "timeout": 2.5}}

    def test_one_address_may_be_a_string(self, seen):
        pysdrlib.find(addresses="192.168.10.2")
        assert seen["both"][0] == ["192.168.10.2"]

    def test_one_driver(self, seen):
        assert [i.driver for i in pysdrlib.find("both", addresses="1.2.3.4")] == ["both"]
        assert set(seen) == {"both"}

    def test_a_finder_that_raises_is_skipped(self):
        class Boom(Sim):
            @classmethod
            def find(cls, addresses=None):
                raise OSError("network is down")
        pysdrlib.register("boom", Boom)
        assert "boom" not in {i.driver for i in pysdrlib.find(addresses="1.2.3.4", virtual=True)}

    def test_shipped_finders_take_no_arguments_and_still_work(self):
        assert pysdrlib.find(addresses="192.168.10.2", timeout=1) is not None
        assert [i.driver for i in pysdrlib.find("sim", virtual=True, addresses="1.2.3.4")] == ["sim"]


# ---------------------------------------------------------------- tcpiq
@pytest.fixture
def server():
    """A TCP server on the loopback that sends `payload` to the first client, then holds the connection `hold` seconds"""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    state = {"payload": b"", "hold": 0.5}

    def serve():
        try:
            conn, _ = srv.accept()
        except OSError:
            return
        with conn:
            conn.sendall(state["payload"])
            time.sleep(state["hold"])

    threading.Thread(target=serve, daemon=True).start()
    state["port"] = srv.getsockname()[1]
    yield state
    srv.close()


def tone(n=100_000):
    return np.exp(2j * np.pi * 0.1 * np.arange(n)).astype(np.complex64)


class TestTcpIq:
    def test_is_a_shipped_driver_that_conforms(self):
        assert "tcpiq" in devices.ls()
        assert not testing.errors(testing.check_package("tcpiq"))
        assert pysdrlib.find("tcpiq") == []   # nothing to enumerate

    def test_reads_what_the_server_sends(self, server):
        x = tone()
        server["payload"] = x.tobytes()
        with pysdrlib.open("tcpiq", host="127.0.0.1", port=server["port"]) as sdr:
            sdr.initialize(cf=100e6, Fs=1e6)
            sdr.start_rx()
            y = sdr.read(65536, timeout=2.0)
            sdr.stop_rx()
        assert y.dtype == np.complex64 and np.array_equal(y, x[:65536])

    def test_other_sample_formats_and_split_samples(self, server):
        x = (0.5 * tone(40_000)).astype(np.complex64)
        raw = convert.INT16.from_complex64(x).tobytes()
        server["payload"] = raw[:1001] + b""   # ends mid-sample on purpose: whole samples only reach the ring
        sdr = TcpIq(sample_format=convert.INT16)
        sdr.open(host="127.0.0.1", port=server["port"])
        sdr.initialize(cf=1e6, Fs=1e6)
        sdr.start_rx()
        y = sdr.read(250, timeout=2.0)
        sdr.stop_rx()
        sdr.close()
        assert len(y) == 250 and np.allclose(y, x[:250], atol=2 / 32768)

    def test_end_of_stream_is_noticed(self, server):
        server["payload"] = tone(1000).tobytes()
        server["hold"] = 0.05
        sdr = TcpIq()
        sdr.open(host="127.0.0.1", port=server["port"])
        sdr.initialize(cf=1e6, Fs=1e6)
        sdr.start_rx()
        deadline = time.time() + 2
        while not sdr.eof and time.time() < deadline:
            time.sleep(0.01)
        assert sdr.eof and len(sdr.read(1000, timeout=1.0)) == 1000
        sdr.stop_rx()
        sdr.close()

    def test_needs_a_server_that_is_there(self):
        with pytest.raises(err.NoDevice, match="host"):
            pysdrlib.open("tcpiq")
        with socket.socket() as s:   # a port nothing listens on
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        with pytest.raises(err.NoDevice, match="cannot connect"):
            pysdrlib.open("tcpiq", host="127.0.0.1", port=port, timeout=1.0)


# ---------------------------------------------------------------- the out-of-tree scaffold
class TestOutOfTreeScaffold:
    def test_writes_a_project_with_an_entry_point(self, tmp_path):
        written = scaffold.new_driver("probe_ext", out_of_tree=True, root=tmp_path)
        rel = {p.relative_to(tmp_path).as_posix() for p in written}
        assert rel == {
            "pysdrlib-probe-ext/pyproject.toml", "pysdrlib-probe-ext/README.md", "pysdrlib-probe-ext/tests/test_probe_ext.py",
            "pysdrlib-probe-ext/src/pysdrlib_probe_ext/__init__.py", "pysdrlib-probe-ext/src/pysdrlib_probe_ext/probe_ext.py",
        }
        project = tomllib.loads((tmp_path / "pysdrlib-probe-ext/pyproject.toml").read_text())
        assert project["project"]["entry-points"]["pysdrlib.drivers"] == {"probe_ext": "pysdrlib_probe_ext:ProbeExt"}
        with pytest.raises(FileExistsError):
            scaffold.new_driver("probe_ext", out_of_tree=True, root=tmp_path)

    def test_the_generated_driver_conforms_and_is_found_through_its_entry_point(self, tmp_path, monkeypatch):
        scaffold.new_driver("probe_ext", out_of_tree=True, root=tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path / "pysdrlib-probe-ext/src"))
        try:
            module = importlib.import_module("pysdrlib_probe_ext")
            assert not testing.errors(testing.check_driver(module.ProbeExt))
            entry_points(monkeypatch, probe_ext="pysdrlib_probe_ext:ProbeExt")
            sdr = pysdrlib.open("probe_ext")
            assert isinstance(sdr, module.ProbeExt) and sdr.state["open"]
            sdr.close()
            problems = testing.check_package("probe_ext")
            assert not testing.errors(problems), problems
        finally:
            for key in [k for k in sys.modules if k.startswith("pysdrlib_probe_ext")]:
                sys.modules.pop(key)

    def test_a_name_pysdrlib_ships_is_refused(self, tmp_path):
        with pytest.raises(ValueError):
            scaffold.new_driver("hackrf", out_of_tree=True, root=tmp_path)

    def test_command_line(self, tmp_path, capsys):
        assert scaffold.main(["cli_ext", "--out-of-tree", "--root", str(tmp_path)]) == 0
        out = capsys.readouterr().out.replace("\\", "/")
        assert "created pysdrlib-cli-ext/pyproject.toml" in out and "pip install -e ." in out
