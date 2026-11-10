"""Tests for the remote client/server (needs pyzmq). Everything runs against the sim device."""
import sys
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
    if request.param == "ipc" and sys.platform == "win32":
        pytest.skip("ZeroMQ has no ipc:// transport on Windows")
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
        assert dev.get_rx_freq() == 433e6 and dev.get_rx_sample_rate() == 2e6 and dev.get_rx_freq() == 433e6
        assert dev.set_rx_freq(1e9) == 1e9 and dev.get_rx_freq() == 1e9
        dev.set_rx_sample_rate(1e6)
        assert dev.get_rx_sample_rate() == 1e6
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
        assert dev.caps.stage("rx", "IF").max == 40
        assert "get_samples" not in caps["methods"] and "open" not in caps["methods"]
        assert caps["methods"]["get_rx_freq"]["readonly"] and not caps["methods"]["set_rx_freq"]["readonly"]

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
            dev.set_rx_freq(1e6)

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
            assert v.get_rx_freq() == 50e6 and v.state["rx"] and v.get_rx_freq() == 50e6
            assert v.read(100_000, timeout=10).size == 100_000
            for call in (lambda: v.set_rx_freq(1e6), v.stop_rx, lambda: v.set_rx_if_gain(1)):
                with pytest.raises(err.NotController):
                    call()
        assert dev.state["open"]        # a viewer closing doesn't close the device
        assert dev.get_rx_freq() == 50e6

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
            assert v.is_controller and v.set_rx_freq(2e6) == 2e6
            with pytest.raises(err.NotController):
                dev.set_rx_freq(3e6)
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
                assert d.set_rx_freq(5e6) == 5e6

    def test_bad_bind(self):
        with pytest.raises(ValueError):
            Server("http://nope")

    def test_a_stalled_device_is_logged_once_and_the_stream_resumes(self):
        """The pump loop must neither die nor flood the log when the device raises StreamStalled"""
        import logging
        records = []
        class Collect(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())
        with Server("tcp://127.0.0.1:0", devices=["sim"]) as s:
            worker, _ = s.open_device("sim")
            worker.log.addHandler(Collect())
            sim, stalled, real = worker.device, [True], worker.device._rx_wait
            def rx_wait(*args, **kwargs):   # where a hardware device raises it
                if stalled[0]:
                    raise err.StreamStalled("sim stopped delivering")
                return real(*args, **kwargs)
            sim._rx_wait = rx_wait
            with RemoteDevice(s.endpoint, id=worker.id) as d:
                d.claim()
                d.initialize(cf=100e6, Fs=1e6)
                d.start_rx()
                time.sleep(0.5)
                assert sum("stopped delivering" in m for m in records) == 1
                stalled[0] = False   # data resumes
                deadline = time.time() + 5
                samples = d.get_samples(timeout=0.2)
                while len(samples) == 0 and time.time() < deadline:
                    samples = d.get_samples(timeout=0.2)
                d.stop_rx()
                assert len(samples) > 0, "the stream did not resume after the stall"


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
            d.set_rx_freq(1e6)


def test_heartbeat_keeps_control():
    with Server("tcp://127.0.0.1:0", devices=["sim"], lease=0.6) as s:
        with RemoteDevice(s.endpoint, "sim") as d:
            time.sleep(1.8)
            assert d.set_rx_freq(1e6) == 1e6


class TestCli:
    def test_list_info_set_rx_watch(self, server, tmp_path, capsys):
        cli(["client", server.endpoint, "list"])
        assert "sim" in capsys.readouterr().out

        worker, _ = server.open_device("sim")
        cli(["client", server.endpoint, "info", worker.id])
        assert '"name": "Simulated"' in capsys.readouterr().out

        cli(["client", server.endpoint, "set", worker.id, "rx_freq=200e6", "rx_if_gain=7"])
        out = capsys.readouterr().out
        assert "200000000.0" in out and "set_rx_if_gain(7) -> 7" in out
        assert worker.device.get_rx_freq() == 200e6
        assert worker.token is None     # `set` released control again

        out_file = tmp_path / "w.cfile"
        cli(["client", server.endpoint, "rx", "sim", "--rate", "1e6", "--set", "rx_if_gain=5", "-n", "50000", "-o", str(out_file)])
        assert np.fromfile(out_file, dtype=np.complex64).size == 50000

        worker.device.set_rx_sample_rate(1e6)
        worker.claim()
        worker.device.start_rx()
        worker.streaming.set()
        cli(["client", server.endpoint, "watch", worker.id, "-n", "30000", "-o", str(out_file)])
        assert np.fromfile(out_file, dtype=np.complex64).size == 30000

    def test_errors_are_one_line(self, server):
        with pytest.raises(SystemExit, match="no hosted device"):
            cli(["client", server.endpoint, "info", "sim-42"])
        with pytest.raises(SystemExit, match="no such file"):
            cli(["client", server.endpoint, "tx", "sim", "-i", "/nonexistent.cfile", "--init", "transmit=True"])

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

    @pytest.mark.parametrize("flag", ["--rx-wire", "--wire"])
    def test_cli_wire(self, server, tmp_path, capsys, flag):
        cli(["client", server.endpoint, "rx", "sim", flag, "int16", "--rate", "1e6", "-n", "20000", "-o", str(tmp_path / "w")])
        assert "wire=int16" in capsys.readouterr().out

    @pytest.mark.parametrize("wire, atol", [("native", 1 / 127), ("int16", 2 / 32768), ("complex64", 1e-7)])
    def test_cli_tx(self, server, tmp_path, capsys, monkeypatch, wire, atol):
        from pysdrlib.sim.sim import Sim
        made, init = [], Sim.__init__
        monkeypatch.setattr(Sim, "__init__", lambda self, *a, **k: (init(self, *a, **k), made.append(self))[0])   # the CLI closes its device
        a = np.exp(2j * np.pi * 0.01 * np.arange(150_000)).astype(np.complex64)   # more than one chunk
        a.tofile(tmp_path / "in.cfile")
        cli(["client", server.endpoint, "tx", "sim", "-i", str(tmp_path / "in.cfile"), "--init", "transmit=True",
             "--rate", "1e6", "--tx-wire", wire])
        out = capsys.readouterr().out
        assert "transmitted 150,000 samples" in out and "tx_wire=" + ("int8" if wire == "native" else wire) in out
        (sim,) = made
        assert np.allclose(np.concatenate(sim.transmitted[0]), a, atol=atol)


class TestGenericControlsRemotely:
    """The device-agnostic controls reach the hosted device; samples still come over the stream"""
    def test_caps_come_from_the_server(self, dev):
        assert dev.caps.driver == "Simulated" and set(dev.gains()) == {"IF", "BB"}

    def test_gain_and_correction_are_forwarded(self, dev):
        assert dev.set_gain(10, stage="IF") == 10 and dev.get_gain("IF") == 10
        assert dev.set_freq_correction(5.0) == 5.0 and dev.get_freq_correction() == 5.0

    def test_read_with_meta(self, dev):
        dev.initialize(cf=100e6, Fs=1e6)
        dev.start_rx()
        try:
            _, a = dev.read(1000, timeout=5, meta=True)
            _, b = dev.read(1000, timeout=5, meta=True)
        finally:
            dev.stop_rx()
        assert (a.index, b.index) == (0, 1000) and a.freq == 100e6

    def test_reading_is_not_a_remote_call(self, dev):
        for name in ("read", "read_into", "stream", "flush"):
            assert name not in dev.capabilities["methods"]

    def test_relative_gain_is_forwarded(self, dev):
        assert dev.set_gain_relative(1.0) == pytest.approx(1.0) and dev.get_gain_relative() == pytest.approx(1.0)


@pytest.fixture
def dual(server):
    """A two-channel sim that can transmit, hosted by `server`; the client is its controller"""
    d = pysdrlib.device("remote", server.endpoint, "sim", init_kwargs={"channels": 2, "transmit": True})
    d.open()
    d.initialize(cf=100e6, Fs=1e6)
    yield d, server.workers[d.id].device
    for flag, stop in (("rx", d.stop_rx), ("tx", d.stop_tx)):
        if d.state["open"] and d.state[flag]:
            stop()
    if d.state["open"]:
        d.close()


class TestChannelsRemotely:
    def test_the_server_streams_the_channels_chosen(self, dual):
        d, sim = dual
        assert d.caps.channels == {"rx": 2, "tx": 2}
        d.start_rx(channels=(0, 1))
        assert d.rx_channels == (0, 1) and sim.rx_channels == (0, 1)
        x, metas = d.read(4096, timeout=5, meta=True, channel=(0, 1))
        assert x.shape == (2, 4096) and len({m.index for m in metas}) == 1
        turn = np.degrees(np.angle(np.vdot(x[0], x[1])))
        assert turn == pytest.approx(90, abs=3)        # the sim's channel 1 is channel 0 turned by 90 degrees

    def test_one_of_them(self, dual):
        d, _ = dual
        d.start_rx(channels=(1,))
        assert len(d.read(1000, timeout=5, channel=1)) == 1000
        with pytest.raises(err.InvalidValue):
            d.read(10, timeout=0.5, channel=0)

    def test_stream_object_and_switching_channels(self, dual):
        d, _ = dual
        with d.stream(2048, timeout=5, channel=(0, 1)) as s:
            assert s.read().shape == (2, 2048)
        with d.stream(2048, timeout=5) as s:       # back to channel 0 only... still streaming both is fine
            assert len(s.read()) == 2048

    def test_viewers_see_the_channels(self, server, dual):
        d, _ = dual
        d.start_rx(channels=(0, 1))
        with RemoteDevice(server.endpoint, id=d.id) as viewer:
            assert viewer.rx_channels == (0, 1)
            assert viewer.read(1000, timeout=5, channel=(0, 1)).shape == (2, 1000)


class TestTransmitRemotely:
    def test_rows_reach_their_channels(self, dual):
        d, sim = dual
        d.set_tx_wire("complex64")      # exact; the default (native) quantises to the device's own layout
        d.start_tx(channels=(0, 1))
        a = np.exp(2j * np.pi * 0.01 * np.arange(50_000)).astype(np.complex64)
        assert d.write(np.stack((a, -a))) == 50_000
        deadline = time.time() + 5
        while time.time() < deadline and sum(len(x) for x in sim.transmitted[1]) < 50_000:
            time.sleep(0.02)
        got0, got1 = np.concatenate(sim.transmitted[0]), np.concatenate(sim.transmitted[1])
        assert np.allclose(got0, a) and np.allclose(got1, -a)
        assert d.get_tx_stats(1)["written"] == 50_000

    def _sent(self, sim, n, channel=0):
        deadline = time.time() + 5
        while time.time() < deadline and sum(len(x) for x in sim.transmitted[channel]) < n:
            time.sleep(0.02)
        return np.concatenate(sim.transmitted[channel])

    def test_native_is_the_default_and_quantises_to_the_device_layout(self, dual):
        d, sim = dual
        assert d.tx_wire == "native" and d.tx_wire_name == d.capabilities["native_wire"] == "int8"
        a = np.exp(2j * np.pi * 0.01 * np.arange(20_000)).astype(np.complex64)
        d.start_tx()
        assert d.write(a) == 20_000
        got = self._sent(sim, 20_000)
        assert np.allclose(got, a, atol=1 / 127)
        grid = np.stack((got.real, got.imag)) * 128
        assert np.array_equal(grid, np.round(grid))    # on the int8 grid: that is what crossed the wire

    @pytest.mark.parametrize("name", ["complex64", "int16"])
    def test_other_formats(self, dual, name):
        d, sim = dual
        assert d.set_tx_wire(name) == name
        a = np.exp(2j * np.pi * 0.01 * np.arange(10_000)).astype(np.complex64)
        d.start_tx()
        assert d.write(a) == 10_000
        assert np.allclose(self._sent(sim, 10_000), a, atol=2 / 32768)

    def test_the_wire_can_change_mid_stream(self, dual):
        d, sim = dual
        a = np.full(4000, 0.5 + 0.25j, np.complex64)
        d.start_tx()
        d.set_tx_wire("complex64")
        d.write(a)
        d.set_tx_wire("native")
        d.write(a)
        assert np.allclose(self._sent(sim, 8000), a.tolist() * 2)

    def test_unknown_tx_wire(self, dual, server):
        d, _ = dual
        with pytest.raises(ValueError, match="unknown wire format"):
            d.set_tx_wire("int3")
        assert d.tx_wire == "native"
        with pytest.raises(ValueError, match="unknown wire format"):
            RemoteDevice(server.endpoint, "sim", tx_wire="int3")

    def test_transmit_while_receiving(self, dual):
        d, sim = dual
        d.start_rx(channels=(0, 1))
        d.start_tx(channels=(0,))
        sent = 0
        for _ in range(5):
            assert d.read(2000, timeout=5, channel=(0, 1)).shape == (2, 2000)
            sent += d.write(np.ones(3000, np.complex64))
        deadline = time.time() + 5
        while time.time() < deadline and sum(len(x) for x in sim.transmitted[0]) < sent:
            time.sleep(0.02)
        assert sent == 15_000 and sum(len(x) for x in sim.transmitted[0]) == 15_000
        assert d.state["rx"] and d.state["tx"]

    def test_only_the_controller_transmits(self, server, dual):
        d, sim = dual
        d.start_tx()
        with RemoteDevice(server.endpoint, id=d.id) as viewer:
            with pytest.raises(err.NotController):
                viewer.write(np.zeros(10, np.complex64))
        # a block on the TX socket without the controller's token is dropped
        worker = server.workers[d.id]
        push = zmq.Context.instance().socket(zmq.PUSH)
        push.connect(d._endpoint(worker.endpoints["tx"]))
        header, payload = protocol.pack_block(0, 0.0, np.ones(10, np.complex64))
        push.send_multipart([b"not-the-token", header, payload])
        deadline = time.time() + 2
        while time.time() < deadline and worker.tx_refused == 0:
            time.sleep(0.02)
        push.close(0)
        assert worker.tx_refused == 1 and not sim.transmitted[0]

    def test_receive_only_devices(self, dev):
        with pytest.raises(err.NotSupported):
            dev.start_tx()


def test_blocks_carry_the_channel_and_index_and_older_ones_still_read():
    header, payload = protocol.pack_block(7, 1.5, np.ones(4, np.complex64), channel=1, index=1234)
    seq, t, x, channel, index = protocol.unpack_block_ex(header, payload)
    assert (seq, channel, index, len(x)) == (7, 1, 1234, 4)
    old = protocol.HEADER_V2.pack(2, 2, 32, 32, 0, 0, 3, 0.0, 4)   # a version 2 server's block
    seq, _, x, channel, index = protocol.unpack_block_ex(old, payload)
    assert (seq, channel, index, len(x)) == (3, 0, None, 4)


class TestOverload:
    def test_a_server_faster_than_the_client_can_decode_does_not_stall_it(self, tmp_path):
        """A device flooding the server (not paced to real time) in the device's own wire layout, which costs the
        client a conversion per block: reads still return (with the lost blocks counted), instead of the client
        pulling blocks for as long as the server produces them"""
        with Server(f"ipc://{tmp_path}/c2", devices=["sim"], lease=0, wire="native") as server:
            dev = pysdrlib.device("remote", server.endpoint, "sim", init_kwargs={"realtime": False})
            dev.open()
            try:
                dev.initialize(cf=100e6, Fs=60e6)
                dev.start_rx()
                t0 = time.monotonic()
                for _ in range(5):
                    x, m = dev.read(65536, timeout=2, meta=True)
                    assert len(x) == 65536
                assert time.monotonic() - t0 < 2.0
            finally:
                dev.close()
