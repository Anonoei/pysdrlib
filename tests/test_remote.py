"""Tests for the remote client/server (needs pyzmq). Everything runs against the sim device."""
import time

import numpy as np
import pytest

zmq = pytest.importorskip("zmq")

import pysdrlib
from pysdrlib import err
from pysdrlib.base import convert
from pysdrlib.remote import Client, RemoteDevice, Server, protocol
from pysdrlib.remote.cli import main as cli


@pytest.fixture(params=["tcp", "ipc"])
def server(request, tmp_path):
    bind = "tcp://127.0.0.1:0" if request.param == "tcp" else f"ipc://{tmp_path}/c2"
    with Server(bind, devices=["sim"], lease=0) as s:
        yield s


@pytest.fixture
def dev(server):
    d = pysdrlib.device("remote", server.endpoint, "sim")
    d.open()
    yield d
    if d.state["open"]:
        d.close()


class TestProtocol:
    def test_block_roundtrip(self):
        x = (np.arange(10) + 1j * np.arange(10)).astype(np.complex64)
        header, payload = protocol.pack_block(7, 1.5, x)
        seq, ts, y = protocol.unpack_block(header, memoryview(payload).cast("B"))
        assert seq == 7 and ts == 1.5 and y.dtype == np.complex64 and np.array_equal(x, y)

    @pytest.mark.parametrize("name", list(protocol.WIRE_FORMATS))
    def test_every_wire_format_roundtrips(self, name):
        fmt = protocol.wire_format(name)
        rng = np.random.default_rng(0)
        x = (rng.uniform(-0.9, 0.9, 1000) + 1j * rng.uniform(-0.9, 0.9, 1000)).astype(np.complex64)
        header, payload = protocol.pack_block(3, 0.0, x, fmt)
        seq, _, y = protocol.unpack_block(header, memoryview(payload).cast("B"))
        assert seq == 3 and y.dtype == np.complex64 and y.shape == x.shape
        assert np.max(np.abs(x - y)) <= 1.5 / fmt.full_scale if fmt.kind != "float" else np.array_equal(x, y)
        assert memoryview(payload).nbytes == 1000 * fmt.bytes_per_sample

    def test_quantized_values_are_a_fixed_point(self):
        # what a device's native format can represent survives the wire exactly, in every layout
        for fmt in (convert.INT8, convert.UINT8, convert.INT12, convert.INT12_PACKED, convert.INT16):
            raw = fmt.from_complex64(np.linspace(-0.99, 0.99, 400).astype(np.float32).view(np.complex64)[:100])
            x = fmt.to_complex64(raw)
            header, payload = protocol.pack_block(0, 0.0, x, fmt)
            assert np.array_equal(protocol.unpack_block(header, memoryview(payload).cast("B"))[2], x), fmt

    def test_unknown_wire_format(self):
        with pytest.raises(ValueError, match="unknown wire format"):
            protocol.wire_format("int3")

    def test_bad_block(self):
        with pytest.raises(err.ProtocolError):
            protocol.unpack_block(b"short", b"")

    def test_error_roundtrip(self):
        with pytest.raises(err.NotOpen):
            protocol.raise_error({"type": "NotOpen", "msg": "x"})
        with pytest.raises(err.RemoteError, match="Weird: x"):
            protocol.raise_error({"type": "Weird", "msg": "x"})


class TestRemoteDevice:
    def test_registered_and_is_a_device(self, server):
        d = pysdrlib.device("remote", server.endpoint, "sim")
        assert isinstance(d, pysdrlib.Device) and "remote" not in pysdrlib.devices.ls()

    def test_needs_exactly_one_target(self, server):
        with pytest.raises(ValueError):
            RemoteDevice(server.endpoint)
        with pytest.raises(ValueError):
            RemoteDevice(server.endpoint, "sim", id="sim-0")

    def test_settings_and_state(self, dev):
        dev.initialize(cf=433e6, Fs=2e6)
        assert dev.CF == 433e6 and dev.Fs == 2e6 and dev.get_freq() == 433e6
        assert dev.set_freq(1e9) == 1e9 and dev.CF == 1e9
        dev.Fs = 1e6
        assert dev.get_sample_rate() == 1e6
        assert dev.state["open"] and not dev.state["rx"]

    def test_device_specific_methods(self, dev):
        dev.initialize()
        assert dev.set_rx_if_gain(12) == 12          # not defined on RemoteDevice: resolved from capabilities
        assert "set_rx_if_gain" in dir(dev)
        assert dev.set_rx_gain(30) == {"if": 30, "bb": 0}   # generic layer still works alongside
        with pytest.raises(AttributeError):
            dev.set_nonexistent_gain(1)

    def test_remote_errors_keep_their_type(self, dev):
        with pytest.raises(err.NotOpen):
            RemoteDevice(dev.address, id=dev.id).start_rx()
        with pytest.raises(AttributeError):
            dev._call("_open")

    def test_capabilities(self, dev):
        caps = dev.capabilities
        assert caps["name"] == "Simulated" and not caps["can_transmit"]
        assert caps["config"]["GAIN"]["RX_IF_MAX"] == 40
        assert "get_samples" not in caps["methods"] and "open" not in caps["methods"]
        assert caps["methods"]["get_freq"]["readonly"] and not caps["methods"]["set_freq"]["readonly"]

    def test_rx_stream(self, dev):
        dev.initialize(cf=100e6, Fs=1e6)
        dev.start_rx()
        assert dev.state["rx"]
        x = dev.read(300_000, timeout=10)
        assert x.dtype == np.complex64 and x.size == 300_000
        assert abs(np.fft.fftfreq(x.size, 1e-6)[np.argmax(np.abs(np.fft.fft(x)))]) > 0   # the tone made it across
        assert dev.dropped_blocks == 0
        dev.stop_rx()
        assert not dev.state["rx"]

    def test_get_samples_empty_when_idle(self, dev):
        assert dev.get_samples().size == 0

    def test_close_removes_device(self, server, dev):
        dev_id = dev.id
        dev.close()
        assert not dev.state["open"]
        with Client(server.endpoint) as c:
            assert dev_id not in [d["id"] for d in c.list()]
        with pytest.raises(err.NotOpen):
            dev.set_freq(1e6)

    def test_context_manager(self, server):
        with pysdrlib.device("remote", server.endpoint, "sim") as d:
            assert d.state["open"]
        assert not d.state["open"]


class TestOwnership:
    def test_viewer_reads_but_cannot_control(self, server, dev):
        dev.initialize(cf=50e6, Fs=1e6)
        dev.start_rx()
        with RemoteDevice(server.endpoint, id=dev.id) as v:
            assert not v.is_controller
            assert v.CF == 50e6 and v.state["rx"] and v.get_freq() == 50e6
            assert v.read(100_000, timeout=10).size == 100_000
            for call in (lambda: v.set_freq(1e6), v.stop_rx, lambda: v.set_rx_if_gain(1)):
                with pytest.raises(err.NotController):
                    call()
        assert dev.state["open"]        # a viewer closing doesn't close the device
        assert dev.CF == 50e6

    def test_multiple_viewers_each_get_samples(self, server, dev):
        dev.initialize(Fs=1e6)
        dev.start_rx()
        with RemoteDevice(server.endpoint, id=dev.id) as a, RemoteDevice(server.endpoint, id=dev.id) as b:
            assert a.read(50_000, timeout=10).size == 50_000
            assert b.read(50_000, timeout=10).size == 50_000

    def test_one_controller(self, server, dev):
        with RemoteDevice(server.endpoint, id=dev.id) as v:
            with pytest.raises(err.Busy):
                v.claim()
            dev.release()
            v.claim()
            assert v.is_controller and v.set_freq(2e6) == 2e6
            with pytest.raises(err.NotController):
                dev.set_freq(3e6)
            v.release()

    def test_release_stops_streaming(self, server, dev):
        dev.initialize(Fs=1e6)
        dev.start_rx()
        dev.release()
        assert not dev.state["rx"] and not dev.is_controller

    def test_viewer_cannot_close_device(self, server, dev):
        v = RemoteDevice(server.endpoint, id=dev.id)
        v.open()
        v.close()
        with Client(server.endpoint) as c:
            assert dev.id in [d["id"] for d in c.list()]

    def test_attach_unknown(self, server):
        with pytest.raises(err.NoDevice):
            RemoteDevice(server.endpoint, id="sim-99").open()


class TestServer:
    def test_unknown_or_disallowed_type(self, server):
        with pytest.raises(err.NoDevice):
            pysdrlib.device("remote", server.endpoint, "hackrf").open()
        with pytest.raises(err.NoDevice):
            pysdrlib.device("remote", server.endpoint, "nope").open()

    def test_failed_open_leaves_nothing_hosted(self, server):
        with pytest.raises(TypeError):
            pysdrlib.device("remote", server.endpoint, "sim", init_kwargs={"bogus": 1}).open()
        with Client(server.endpoint) as c:
            assert c.list() == []

    def test_list(self, server, dev):
        with Client(server.endpoint) as c:
            assert c.types() == ["sim"]
            (d,) = c.list()
            assert d["id"] == dev.id and d["controlled"] and d["name"] == "Simulated"

    def test_ids_increment(self, server):
        with pysdrlib.device("remote", server.endpoint, "sim") as a, pysdrlib.device("remote", server.endpoint, "sim") as b:
            assert (a.id, b.id) == ("sim-0", "sim-1")

    def test_constructor_kwargs(self, server):
        with pysdrlib.device("remote", server.endpoint, "sim", init_kwargs={"block_samples": 4096}) as d:
            d.initialize(Fs=1e6)
            d.start_rx()
            assert d.read(8192, timeout=10).size == 8192

    def test_port_range(self):
        with Server("tcp://127.0.0.1:0", port_range=(47000, 47100), devices=["sim"]) as s:
            worker, _ = s.open_device("sim")
            ports = [int(ep.rpartition(":")[2]) for ep in worker.endpoints.values()]
            assert all(47000 <= p <= 47100 for p in ports)

    def test_preopened_device_can_be_claimed(self):
        with Server("tcp://127.0.0.1:0", devices=["sim"]) as s:
            worker, token = s.open_device("sim")
            assert token is None
            with RemoteDevice(s.endpoint, id=worker.id) as d:
                d.claim()
                assert d.set_freq(5e6) == 5e6

    def test_bad_bind(self):
        with pytest.raises(ValueError):
            Server("http://nope")


def test_lease_expiry_releases_controller():
    with Server("tcp://127.0.0.1:0", devices=["sim"], lease=0.5) as s:
        d = RemoteDevice(s.endpoint, "sim")
        d.open()
        d.initialize(Fs=1e6)
        d.start_rx()
        d._beat_stop.set()          # stop the heartbeat: the controller "dies"
        d._beat.join()
        time.sleep(1.5)
        with Client(s.endpoint) as c:
            (info,) = c.list()
        assert not info["controlled"] and not info["state"]["rx"] and info["state"]["open"]
        with pytest.raises(err.NotController):
            d.set_freq(1e6)


def test_heartbeat_keeps_control():
    with Server("tcp://127.0.0.1:0", devices=["sim"], lease=0.6) as s:
        with RemoteDevice(s.endpoint, "sim") as d:
            time.sleep(1.8)
            assert d.set_freq(1e6) == 1e6


class TestCli:
    def test_list_info_set_rx_watch(self, server, tmp_path, capsys):
        cli(["client", server.endpoint, "list"])
        assert "sim" in capsys.readouterr().out

        worker, _ = server.open_device("sim")
        cli(["client", server.endpoint, "info", worker.id])
        assert '"name": "Simulated"' in capsys.readouterr().out

        cli(["client", server.endpoint, "set", worker.id, "freq=200e6", "rx_if_gain=7"])
        out = capsys.readouterr().out
        assert "200000000.0" in out and "set_rx_if_gain(7) -> 7" in out
        assert worker.device.get_freq() == 200e6
        assert worker.token is None     # `set` released control again

        out_file = tmp_path / "w.cfile"
        cli(["client", server.endpoint, "rx", "sim", "--rate", "1e6", "--set", "rx_if_gain=5", "-n", "50000", "-o", str(out_file)])
        assert np.fromfile(out_file, dtype=np.complex64).size == 50000

        worker.device.set_sample_rate(1e6)
        worker.claim()
        worker.device.start_rx()
        worker.streaming.set()
        cli(["client", server.endpoint, "watch", worker.id, "-n", "30000", "-o", str(out_file)])
        assert np.fromfile(out_file, dtype=np.complex64).size == 30000

    def test_errors_are_one_line(self, server):
        with pytest.raises(SystemExit, match="no hosted device"):
            cli(["client", server.endpoint, "info", "sim-42"])
        with pytest.raises(SystemExit, match="not implemented"):
            cli(["client", server.endpoint, "tx", "sim"])

    def test_unreachable(self):
        with pytest.raises(SystemExit, match="no reply"):
            cli(["client", "tcp://127.0.0.1:1", "--timeout", "0.3", "list"])


class TestWire:
    def test_native_is_the_default_and_smaller(self, dev):
        assert dev.capabilities["native_wire"] == "int8" and dev.wire == "int8"
        dev.initialize(Fs=1e6)
        dev.start_rx()
        dev.read(10_000, timeout=10)
        assert dev.wire == "int8"

    def test_native_is_lossless(self, server):
        # The sim's tone is exactly representable in int8, so what arrives equals the driver's own output
        with pysdrlib.device("remote", server.endpoint, "sim", init_kwargs={"block_samples": 4096}) as d:
            d.initialize(Fs=1e6)
            sim = server.workers[d.id].device
            expected = sim.SAMPLE_FORMAT.to_complex64(sim._raw)
            d.start_rx()
            assert np.array_equal(d.read(4096, timeout=10), expected)

    @pytest.mark.parametrize("name", ["native", "complex64", "int8", "int16", "int12p", "uint8"])
    def test_select_each_format(self, server, name):
        with pysdrlib.device("remote", server.endpoint, "sim", wire=name) as d:
            d.initialize(Fs=1e6)
            d.start_rx()
            x = d.read(50_000, timeout=10)
            assert x.dtype == np.complex64 and np.max(np.abs(x)) <= 1.0
            assert abs(np.abs(x).mean() - 0.5) < 0.02       # the sim's 0.5 amplitude tone, whatever the format

    def test_switch_while_streaming(self, dev):
        dev.initialize(Fs=1e6)
        dev.start_rx()
        dev.read(10_000, timeout=10)
        assert dev.set_wire("complex64") == "complex64" and dev.wire == "complex64"
        assert dev.read(300_000, timeout=10).size == 300_000
        assert dev.set_wire("native") == "int8"
        assert dev.read(300_000, timeout=10).size == 300_000
        assert dev.dropped_blocks == 0

    def test_viewer_sees_and_cannot_change(self, server, dev):
        dev.set_wire("int16")
        with RemoteDevice(server.endpoint, id=dev.id) as v:
            assert v.wire == "int16"
            with pytest.raises(err.NotController):
                v.set_wire("int8")

    def test_bad_format_rejected(self, dev):
        with pytest.raises(ValueError, match="unknown wire format"):
            dev.set_wire("int3")
        assert dev.wire == "int8"

    def test_server_default(self):
        with Server("tcp://127.0.0.1:0", devices=["sim"], wire="complex64") as s:
            with RemoteDevice(s.endpoint, "sim") as d:
                assert d.wire == "complex64"
        with pytest.raises(ValueError):
            Server("tcp://127.0.0.1:0", wire="int3")

    def test_cli_wire(self, server, tmp_path, capsys):
        cli(["client", server.endpoint, "rx", "sim", "--wire", "int16", "--rate", "1e6", "-n", "20000", "-o", str(tmp_path / "w")])
        assert "wire=int16" in capsys.readouterr().out
