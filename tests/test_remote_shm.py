"""Several clients on one radio through shared memory (pysdrlib.remote.shm), against a server hosting the simulated device.

The simulated device's tone is phase-continuous, so the sample at a stream index is the same for every client: what
two clients read at the same index must be equal, however and whenever they got it.
"""
import os
import subprocess
import sys
import time

import numpy as np
import pytest

import pysdrlib
from pysdrlib import err
from pysdrlib.remote import Server, shm

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="ZeroMQ has no ipc:// transport on Windows")


def segments():
    """The shared-memory segments pysdrlib has made that are still there"""
    try:
        return sorted(f for f in os.listdir("/dev/shm") if f.startswith("psdr-"))
    except FileNotFoundError:
        return []


@pytest.fixture
def server(tmp_path):
    before = segments()
    with Server(f"ipc://{tmp_path}/c2", devices=["sim"], lease=0) as s:
        yield s
    assert segments() == before, "the server left shared memory behind"


def open_sim(server, realtime=True, **kw):
    """The simulated device on the server: paced like a radio (so clients read near each other), or flooding"""
    dev = pysdrlib.device("remote", server.endpoint, "sim", init_kwargs={"realtime": realtime, "block_samples": 8192}, **kw)
    dev.open()
    return dev


def view(server, device_id, **kw):
    dev = pysdrlib.device("remote", server.endpoint, id=device_id, **kw)
    dev.open()
    return dev


def collect(dev, blocks, count=4096):
    """{stream index: sample} of `blocks` reads"""
    got = {}
    for _ in range(blocks):
        x, m = dev.read(count, timeout=5, meta=True)
        got.update(zip(range(m.index, m.index + count), x.tolist()) if not m.gaps else ())
    return got


class TestTransport:
    def test_a_local_client_uses_shared_memory(self, server):
        dev = open_sim(server)
        try:
            dev.initialize(cf=100e6, Fs=10e6)
            dev.start_rx()
            assert dev._shm_mode and len(dev.read(4096, timeout=5)) == 4096 and list(dev._rings) == [0]
        finally:
            dev.close()

    def test_the_socket_can_be_chosen(self, server):
        dev = open_sim(server, transport="zmq")
        try:
            dev.initialize(cf=100e6, Fs=10e6)
            dev.start_rx()
            assert not dev._shm_mode and len(dev.read(4096, timeout=5)) == 4096 and not dev._rings
        finally:
            dev.close()

    def test_a_bad_transport_is_refused(self, server):
        with pytest.raises(ValueError):
            pysdrlib.device("remote", server.endpoint, "sim", transport="carrier pigeon")

    def test_shared_memory_can_be_demanded_of_a_server_that_has_none(self, tmp_path):
        with Server(f"ipc://{tmp_path}/c2", devices=["sim"], lease=0, shm=False) as server:
            dev = pysdrlib.device("remote", server.endpoint, "sim", transport="shm")
            with pytest.raises(err.NotSupported):
                dev.open()
            auto = open_sim(server)   # while "auto" takes the socket
            try:
                auto.initialize(cf=100e6, Fs=10e6)
                auto.start_rx()
                assert not auto._shm_mode and len(auto.read(4096, timeout=5)) == 4096
            finally:
                auto.close()

    def test_the_server_only_does_the_work_somebody_uses(self, server):
        dev = open_sim(server)
        try:
            dev.initialize(cf=100e6, Fs=10e6)
            dev.start_rx()
            dev.read(4096, timeout=5)
            worker = server.workers[dev.id]
            assert worker._subs.get(b"shm:" + dev.id.encode()) == 1 and not worker._subs.get(dev.id.encode())
        finally:
            dev.close()


class TestSharing:
    def test_clients_read_the_same_stream_at_their_own_pace(self, server):
        ctl = open_sim(server)
        try:
            ctl.initialize(cf=100e6, Fs=2e6)
            ctl.start_rx()
            a, b = view(server, ctl.id), view(server, ctl.id)
            assert a._shm_mode and b._shm_mode
            time.sleep(0.05)
            got_ctl, got_a, got_b = collect(ctl, 30), collect(a, 10), collect(b, 20)
            assert len(server.workers[ctl.id]._rings) == 1                  # one copy, three readers
            assert server.workers[ctl.id]._subs[b"shm:" + ctl.id.encode()] == 3
            for x, y in ((got_ctl, got_a), (got_ctl, got_b), (got_a, got_b)):
                common = sorted(set(x) & set(y))
                assert len(common) > 1000 and all(x[i] == y[i] for i in common[::97])
            a.close()
            b.close()
        finally:
            ctl.close()

    def test_a_socket_client_and_a_shared_memory_one_see_the_same_samples(self, server):
        ctl = open_sim(server)
        try:
            ctl.initialize(cf=100e6, Fs=2e6)
            ctl.start_rx()
            sock = view(server, ctl.id, transport="zmq")
            time.sleep(0.05)
            got_ctl, got_sock = collect(ctl, 30), collect(sock, 30)
            worker = server.workers[ctl.id]
            assert worker._subs[ctl.id.encode()] == 1 and worker._subs[b"shm:" + ctl.id.encode()] == 1
            common = sorted(set(got_ctl) & set(got_sock))
            assert len(common) > 1000 and all(got_ctl[i] == got_sock[i] for i in common[::97])
            sock.close()
        finally:
            ctl.close()

    def test_a_client_that_leaves_is_forgotten(self, server):
        ctl = open_sim(server)
        try:
            ctl.initialize(cf=100e6, Fs=10e6)
            ctl.start_rx()
            v = view(server, ctl.id)
            v.read(4096, timeout=5)
            v.close()
            worker = server.workers[ctl.id]
            for _ in range(100):
                ctl.read(4096, timeout=5)
                if worker._subs.get(b"shm:" + ctl.id.encode()) == 1:
                    break
            assert worker._subs[b"shm:" + ctl.id.encode()] == 1
        finally:
            ctl.close()

    def test_a_restarted_stream_starts_over_for_everyone(self, server):
        ctl = open_sim(server)
        try:
            ctl.initialize(cf=100e6, Fs=10e6)
            ctl.start_rx()
            v = view(server, ctl.id)
            ctl.read(8192, timeout=5)
            v.read(8192, timeout=5)
            first = ctl._rings[0].name
            ctl.stop_rx()
            ctl.start_rx()
            x, m = ctl.read(4096, timeout=5, meta=True)
            assert m.index == 0 and ctl._rings[0].name != first            # its own new ring, from its first sample
            for _ in range(200):   # the viewer first reads what it had not yet of the old stream, then follows the new one
                y, mv = v.read(4096, timeout=5, meta=True)
                if v._rings[0].name == ctl._rings[0].name:
                    break
            assert v._rings[0].name == ctl._rings[0].name and mv.index == 0
            v.close()
        finally:
            ctl.close()


class TestLosingSamples:
    def test_a_client_that_falls_behind_sees_the_loss_and_nothing_torn(self, tmp_path):
        with Server(f"ipc://{tmp_path}/c2", devices=["sim"], lease=0, shm_samples=1 << 15) as server:
            dev = open_sim(server, realtime=False)
            try:
                dev.initialize(cf=100e6, Fs=10e6)
                dev.start_rx()
                dev.read(4096, timeout=5)
                time.sleep(0.3)   # the (not real-time) device writes far more than the 32768-sample ring holds
                x, m = dev.read(4096, timeout=5, meta=True)
                assert m.dropped > 0 and m.index > 4096 and len(x) == 4096
                assert np.all(np.abs(x) > 0.49) and np.abs(x).max() <= 0.5 + 1e-3   # still the 0.5 tone, not garbage
            finally:
                dev.close()


class TestFallback:
    def test_a_segment_that_cannot_be_attached_means_the_socket(self, server, monkeypatch):
        """The server is somewhere /dev/shm is not shared: attaching fails, and the client takes blocks over the socket"""
        def refuse(name):
            raise FileNotFoundError(name)
        monkeypatch.setattr(shm, "ShmReader", refuse)
        dev = open_sim(server)
        try:
            dev.initialize(cf=100e6, Fs=10e6)
            dev.start_rx()
            assert len(dev.read(4096, timeout=5)) == 4096 and not dev._shm_mode
        finally:
            dev.close()

    def test_a_server_that_cannot_make_a_segment_means_the_socket(self, server, monkeypatch):
        def refuse(*a, **k):
            raise OSError("no room in /dev/shm")
        monkeypatch.setattr(shm, "ShmWriter", refuse)
        dev = open_sim(server)
        try:
            dev.initialize(cf=100e6, Fs=10e6)
            dev.start_rx()
            assert len(dev.read(4096, timeout=5)) == 4096 and not dev._shm_mode
        finally:
            dev.close()

    def test_demanding_shared_memory_does_not_fall_back(self, server, monkeypatch):
        monkeypatch.setattr(shm, "ShmReader", lambda name: (_ for _ in ()).throw(FileNotFoundError(name)))
        dev = open_sim(server, transport="shm")
        try:
            dev.initialize(cf=100e6, Fs=10e6)
            dev.start_rx()
            with pytest.raises(err.NotSupported):
                dev.read(4096, timeout=5)
        finally:
            dev.close()


class TestAnotherProcess:
    READER = """
import sys, numpy as np
from pysdrlib.remote import shm
r = shm.ShmReader(sys.argv[1])
out = np.empty(1000, np.complex64)
n, first = r.read(out, 0)
print(n, first, out[:n].real.astype(int).tolist()[:5], r.head)
r.close()
"""

    def test_a_process_of_its_own_reads_what_the_server_wrote_and_leaves_the_segment_alone(self):
        writer = shm.ShmWriter(1 << 18, 1)
        try:
            writer.publish((np.arange(1000) % 1000).astype(np.complex64), 0)
            for _ in range(2):   # and again: the first reader exiting must not have removed it
                out = subprocess.run([sys.executable, "-I", "-c", self.READER, writer.name], capture_output=True, text=True,
                                     timeout=60, env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)})
                assert out.returncode == 0, out.stderr
                assert out.stdout.strip() == "1000 0 [0, 1, 2, 3, 4] 1000", out.stdout
                assert "resource_tracker" not in out.stderr and "leaked" not in out.stderr
        finally:
            writer.destroy()
        assert not [s for s in segments() if s == writer.name]

    def test_a_segment_that_is_not_there_is_not_found(self):
        with pytest.raises(FileNotFoundError):
            shm.ShmReader("psdr-does-not-exist")
