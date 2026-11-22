"""Streams in shared memory (pysdrlib.shm): ShmStream publishes, the "shm" driver receives"""
import json
import os
import subprocess
import sys
import time

import numpy as np
import pytest

import pysdrlib
from pysdrlib import err
from pysdrlib.shm import ShmStream, stream

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the ring needs POSIX shared memory semantics here")


def ramp(first, n):
    return (np.arange(first, first + n) % 1_000_000).astype(np.complex64)


@pytest.fixture
def d(tmp_path):
    return str(tmp_path / "streams")


def opened(d, name="t.ch1"):
    sdr = pysdrlib.open("shm", name=name, directory=d)
    sdr.initialize()
    sdr.start_rx()
    return sdr


def read(sdr, n=1000):
    return sdr.read(n, timeout=5, meta=True)


class TestPublishing:
    def test_the_manifest_says_what_is_published(self, d):
        with ShmStream("t.ch1", Fs=250e3, cf=433.92e6, directory=d, bw=200e3) as out:
            m = json.load(open(os.path.join(d, "t.ch1.json")))
            assert m["format"] == stream.FORMAT and m["name"] == "t.ch1" and m["segment"] == out.writer.name
            assert (m["Fs"], m["cf"], m["gen"], m["pid"]) == (250e3, 433.92e6, 0, os.getpid())
            assert m["info"] == {"bw": 200e3} and m["wall_time"] is None
            out.publish(ramp(0, 10), 5, wall_time=1.7e9)
            m = stream.read_manifest(out.path)
            assert (m["wall_time"], m["wall_index"]) == (1.7e9, 5)
        assert not os.path.exists(os.path.join(d, "t.ch1.json"))
        assert stream.streams(d) == []

    @pytest.mark.parametrize("name", ["a/b", "a@b", "", ".hidden", "a b"])
    def test_a_name_that_cant_be_a_target_is_refused(self, d, name):
        with pytest.raises(ValueError):
            ShmStream(name, Fs=1e6, cf=1e8, directory=d)

    def test_a_name_published_by_a_running_process_is_taken(self, d):
        os.makedirs(d)
        with open(os.path.join(d, "t.ch1.json"), "w") as f:   # pid 1 is always running
            json.dump({"format": 1, "name": "t.ch1", "segment": "psdr-none", "gen": 4, "Fs": 1, "cf": 1, "pid": 1}, f)
        with pytest.raises(FileExistsError):
            ShmStream("t.ch1", Fs=1e6, cf=1e8, directory=d)

    def test_a_name_left_by_a_dead_process_is_reused_at_the_next_generation(self, d):
        os.makedirs(d)
        dead = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True)
        with open(os.path.join(d, "t.ch1.json"), "w") as f:
            json.dump({"format": 1, "name": "t.ch1", "segment": "psdr-none", "gen": 4, "Fs": 1, "cf": 1,
                       "pid": int(dead.stdout)}, f)
        assert stream.streams(d) == [] and len(stream.streams(d, live=False)) == 1
        with ShmStream("t.ch1", Fs=1e6, cf=1e8, directory=d) as out:
            assert out.gen == 5 and out.writer.ring.stream_id == 5

    def test_a_manifest_of_another_format_is_not_read(self, d):
        os.makedirs(d)
        with open(os.path.join(d, "x.json"), "w") as f:
            json.dump({"format": 2, "name": "x", "segment": "s", "pid": os.getpid()}, f)
        assert stream.streams(d) == []

    def test_samples_out_of_order_are_refused(self, d):
        with ShmStream("t.ch1", Fs=1e6, cf=1e8, directory=d) as out:
            out.publish(ramp(0, 10), 100)
            with pytest.raises(ValueError):
                out.publish(ramp(0, 10), 50)


class TestFinding:
    def test_find_lists_the_streams_published(self, d, monkeypatch):
        monkeypatch.setenv(stream.DIR_ENV, d)
        with ShmStream("t.ch1", Fs=250e3, cf=433.92e6, label="ISM"), ShmStream("t.ch2", Fs=1e6, cf=1e8):
            found = [i for i in pysdrlib.find() if i.driver == "shm"]
            assert [i.serial for i in found] == ["t.ch1", "t.ch2"]
            assert found[0].extra["info"] == {"label": "ISM"} and "433.92 MHz" in found[0].label
            sdr = found[0].open()
            try:
                assert sdr.name == "t.ch1" and sdr.get_rx_sample_rate() == 250e3
            finally:
                sdr.close()

    def test_a_target_string_opens_a_stream_by_the_end_of_its_name(self, d, monkeypatch):
        monkeypatch.setenv(stream.DIR_ENV, d)
        with ShmStream("rpps.ch3", Fs=1e6, cf=1e8):
            sdr = pysdrlib.open("shm@ch3")
            try:
                assert sdr.name == "rpps.ch3"
            finally:
                sdr.close()

    def test_opening_what_isnt_there_says_what_is(self, d):
        with pytest.raises(err.NoDevice) as e:
            pysdrlib.open("shm", directory=d)
        assert e.value.reason == "none_attached"
        with ShmStream("t.ch1", Fs=1e6, cf=1e8, directory=d), ShmStream("t.ch2", Fs=1e6, cf=1e8, directory=d):
            with pytest.raises(err.NoDevice, match="t.ch1, t.ch2"):
                pysdrlib.open("shm", directory=d)
            with pytest.raises(err.NoDevice, match="t.ch1"):
                pysdrlib.open("shm", name="t.ch9", directory=d)


class TestReceiving:
    def test_the_rate_and_frequency_are_the_publishers(self, d):
        with ShmStream("t.ch1", Fs=250e3, cf=433.92e6, directory=d):
            sdr = pysdrlib.open("shm", name="t.ch1", directory=d)
            try:
                with pytest.warns(UserWarning):
                    sdr.initialize(cf=100e6, Fs=2e6)
                assert (sdr.get_rx_freq(), sdr.get_rx_sample_rate()) == (433.92e6, 250e3)
                sdr.strict = True
                with pytest.raises(err.InvalidValue):
                    sdr.set_rx_sample_rate(2e6)
                assert not sdr.caps.channels["tx"]
            finally:
                sdr.close()

    def test_samples_arrive_in_order_with_the_publishers_gaps(self, d):
        with ShmStream("t.ch1", Fs=1e6, cf=1e8, directory=d) as out:
            sdr = opened(d)
            try:
                out.publish(ramp(1000, 1000), 1000)
                out.publish(ramp(2500, 1000), 2500)      # 500 lost before
                x, m = read(sdr, 2000)
                assert m.index == 0 and m.gaps == ((1000, 500),)
                assert np.array_equal(x, np.concatenate([ramp(1000, 1000), ramp(2500, 1000)]))
                assert x.dtype == np.complex64
            finally:
                sdr.close()

    def test_receiving_starts_with_what_is_published_next(self, d):
        with ShmStream("t.ch1", Fs=1e6, cf=1e8, directory=d) as out:
            out.publish(ramp(0, 1000), 0)
            sdr = opened(d)
            try:
                time.sleep(0.05)
                out.publish(ramp(1000, 500), 1000)
                x, m = read(sdr, 500)
                assert x[0] == 1000 and m.index == 0
            finally:
                sdr.close()

    def test_the_time_is_the_publishers(self, d):
        with ShmStream("t.ch1", Fs=1e6, cf=1e8, directory=d) as out:
            sdr = opened(d)
            try:
                out.publish(ramp(0, 1000), 0, wall_time=1.7e9)
                out.publish(ramp(1000, 1000), 1000)
                x, m = read(sdr, 1000)
                x, m2 = read(sdr, 1000)
                assert m.hw_time == pytest.approx(1.7e9) and m.wall_time == pytest.approx(1.7e9)
                assert m2.hw_time == pytest.approx(1.7e9 + 1e-3)
            finally:
                sdr.close()

    def test_a_retune_is_followed_with_an_event(self, d):
        with ShmStream("t.ch1", Fs=1e6, cf=1e8, directory=d) as out:
            sdr = opened(d)
            try:
                out.publish(ramp(0, 1000), 0)
                out.retune(Fs=2e6, cf=1.01e8, bw=1.5e6)
                out.publish(ramp(7, 1000), 7)
                x, m = read(sdr, 2000)
                assert np.array_equal(x, np.concatenate([ramp(0, 1000), ramp(7, 1000)]))
                assert m.gaps == () and m.next_index == 2000
                kinds = {e.kind: (e.offset, e.value) for e in m.events}
                assert kinds["rate"] == (1000, 2e6) and kinds["freq"] == (1000, 1.01e8)
                assert sdr.get_rx_sample_rate() == 2e6 and sdr.manifest["gen"] == 1
                assert sdr.manifest["info"] == {"bw": 1.5e6}
            finally:
                sdr.close()

    def test_a_closed_stream_is_lost_once_read(self, d):
        out = ShmStream("t.ch1", Fs=1e6, cf=1e8, directory=d)
        sdr = opened(d)
        try:
            out.publish(ramp(0, 1000), 0)
            out.close()
            x, _ = read(sdr, 1000)
            assert x[-1] == 999
            with pytest.raises(err.Lost, match="closed"):
                sdr.read(1000, timeout=5)
        finally:
            sdr.close()


PUBLISHER = """
import sys, time, numpy as np
from pysdrlib.shm import ShmStream
s = ShmStream("t.ch1", Fs=1e6, cf=1e8, directory=sys.argv[1])
s.publish(np.arange(1000).astype(np.complex64), 0)
print("ready", flush=True)
sys.stdin.readline()
s.publish(np.arange(1000, 2000).astype(np.complex64), 1000)
print("sent", flush=True)
sys.stdin.readline()
"""


class TestAnotherProcess:
    def test_a_stream_from_another_process_and_its_end(self, d):
        p = subprocess.Popen([sys.executable, "-I", "-c", PUBLISHER, d], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             text=True, env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)})
        try:
            assert p.stdout.readline().strip() == "ready"
            sdr = opened(d)
            try:
                p.stdin.write("\n"), p.stdin.flush()
                assert p.stdout.readline().strip() == "sent"
                x, _ = read(sdr, 1000)
                assert x[0] == 1000
                p.kill()   # gone without closing: the stream is lost, not stalled
                p.wait()
                with pytest.raises(err.Lost, match="gone|closed"):
                    sdr.read(1000, timeout=10)
            finally:
                sdr.close()
        finally:
            if p.poll() is None:
                p.kill()
            p.wait()
