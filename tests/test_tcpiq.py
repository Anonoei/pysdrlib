"""tcpiq: raw I/Q over TCP, as the client of a server that sends, or as the server a client sends to.

No hardware: everything talks over the loopback. The socket is read by the extension's own thread (no GIL), so these
need the built extension.
"""
import socket
import threading
import time

import numpy as np
import pytest

import pysdrlib
from pysdrlib import devices, err, testing
from pysdrlib.base import convert

TcpIq = pytest.importorskip("pysdrlib.tcpiq").TcpIq
pytest.importorskip("pysdrlib.tcpiq.lib")


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


@pytest.fixture
def listening():
    """A tcpiq device listening on a free loopback port, initialized at 1 MS/s"""
    sdr = pysdrlib.open("tcpiq", listen=True, host="127.0.0.1", port=0)
    sdr.initialize(cf=100e6, Fs=1e6)
    yield sdr
    sdr.close()


def tone(n=100_000):
    return np.exp(2j * np.pi * 0.1 * np.arange(n)).astype(np.complex64)


def client(sdr):
    return socket.create_connection(("127.0.0.1", sdr.port), timeout=5)


def wait_for(cond, timeout=2.0):
    deadline = time.time() + timeout
    while not cond() and time.time() < deadline:
        time.sleep(0.01)
    return cond()


class TestClient:
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

    @pytest.mark.parametrize("name", ["ci16", "ci8", "cu8", "cu16", "ci32", "cf64", "ci16_be", "ci4", "cf16", "cf16_be"])
    def test_other_sample_formats_and_split_samples(self, server, name):
        fmt = convert.sample_format(name)
        x = (0.5 * tone(40_000)).astype(np.complex64)
        raw = fmt.from_complex64(x).tobytes()
        cut = 250 * fmt.bytes_per_sample + 1
        server["payload"] = raw[:cut]   # ends mid-sample on purpose: whole samples only reach the ring
        sdr = pysdrlib.open("tcpiq", host="127.0.0.1", port=server["port"], sample_format=name)
        assert sdr.SAMPLE_FORMAT == fmt
        sdr.initialize(cf=1e6, Fs=1e6)
        sdr.start_rx()
        y = sdr.read(250, timeout=2.0)
        sdr.stop_rx()
        sdr.close()
        assert len(y) == 250 and np.allclose(y, x[:250], atol=2 / fmt.full_scale + 1e-6)

    def test_end_of_stream_is_noticed(self, server):
        server["payload"] = tone(1000).tobytes()
        server["hold"] = 0.05
        sdr = TcpIq()
        sdr.open(host="127.0.0.1", port=server["port"])
        sdr.initialize(cf=1e6, Fs=1e6)
        sdr.start_rx()
        assert wait_for(lambda: sdr.eof)
        assert len(sdr.read(1000, timeout=1.0)) == 1000
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


class TestServer:
    def test_receives_what_a_client_sends(self, listening):
        assert listening.port and listening.peer is None and listening.connections == 0
        listening.start_rx()
        x = tone()
        with client(listening) as c:
            c.sendall(x.tobytes())
            y = listening.read(len(x), timeout=2.0)
            assert listening.peer and listening.connections == 1
        assert np.array_equal(y, x)

    def test_samples_split_across_sends(self, listening):
        listening.start_rx()
        x = tone(10_000)
        raw = x.tobytes()
        with client(listening) as c:
            for i in range(0, len(raw), 1001):   # 1001 is no multiple of the 8 bytes of a sample
                c.sendall(raw[i:i + 1001])
                time.sleep(0.0005)
            y = listening.read(len(x), timeout=2.0)
        assert np.array_equal(y, x)

    def test_accepts_the_next_client_after_one_leaves(self, listening):
        listening.start_rx()
        a, b = tone(5000), (0.5 * tone(5000)).astype(np.complex64)
        with client(listening) as c:
            c.sendall(a.tobytes())
            assert np.array_equal(listening.read(len(a), timeout=2.0), a)
        assert wait_for(lambda: listening.peer is None)
        with client(listening) as c:
            c.sendall(b.tobytes())
            assert np.array_equal(listening.read(len(b), timeout=2.0), b)
        assert listening.connections == 2

    def test_a_sender_is_never_held_up_and_drops_are_reported(self, listening):
        """The extension drains the socket however slowly Python reads: the ring drops the oldest, with the usual warning"""
        listening.start_rx()
        x = tone(4_000_000)   # 32 MB, 8x the 0.5 s queue at 1 MS/s
        with client(listening) as c:
            c.settimeout(5)
            c.sendall(x.tobytes())   # would time out if the server stopped reading
            assert wait_for(lambda: listening.device.rx_stats()["total"] == len(x), timeout=5)
        with pytest.warns(UserWarning, match="dropped because they were not read"):   # warn.Dropped
            y = listening.read(1000, timeout=1.0)
        assert np.array_equal(y, x[len(x) - 500_000:][:1000])   # the newest 0.5 s survive

    def test_what_arrives_while_not_receiving_is_discarded(self, listening):
        early, late = tone(30_001), (0.5 * tone(1000)).astype(np.complex64)
        with client(listening) as c:
            c.sendall(early.tobytes()[:-3])   # a split sample: what follows must still line up
            assert wait_for(lambda: listening.connections == 1)
            time.sleep(0.2)
            listening.start_rx()
            c.sendall(early.tobytes()[-3:] + late.tobytes())
            y = listening.read(1 + len(late), timeout=2.0)
        assert np.array_equal(y, np.concatenate([early[-1:], late]))   # the split one was completed while receiving

    def test_a_port_in_use_is_refused(self, listening):
        with pytest.raises(err.NoDevice, match="cannot listen"):
            pysdrlib.open("tcpiq", listen=True, host="127.0.0.1", port=listening.port)

    def test_close_while_a_client_is_connected(self):
        sdr = pysdrlib.open("tcpiq", listen=True, host="127.0.0.1", port=0)
        sdr.initialize(cf=1e6, Fs=1e6)
        sdr.start_rx()
        with client(sdr) as c:
            c.sendall(tone(1000).tobytes())
            assert wait_for(lambda: sdr.connections == 1)
            sdr.close()   # joins the thread; must not hang
        assert not sdr.state["open"]


def rtl_header(tuner=5, gains=29):
    return b"RTL0" + tuner.to_bytes(4, "big") + gains.to_bytes(4, "big")


@pytest.fixture
def pieces():
    """A TCP server that sends its `chunks` to the first client one at a time, a pause between them (so the receiver
    sees them split), then holds the connection"""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    state = {"chunks": []}

    def serve():
        try:
            conn, _ = srv.accept()
        except OSError:
            return
        with conn:
            for chunk in state["chunks"]:
                conn.sendall(chunk)
                time.sleep(0.03)
            time.sleep(0.5)

    threading.Thread(target=serve, daemon=True).start()
    state["port"] = srv.getsockname()[1]
    yield state
    srv.close()


class TestRtlTcpHeader:
    """rtl_tcp starts its stream with 12 bytes of dongle info: skipped, not read as samples"""

    def read_cu8(self, port, count):
        sdr = pysdrlib.open("tcpiq", host="127.0.0.1", port=port, sample_format="cu8")
        try:
            sdr.initialize(cf=100e6, Fs=1e6)
            sdr.start_rx()
            y = sdr.read(count, timeout=2.0)
            sdr.stop_rx()
            return y, sdr.rtl_tcp
        finally:
            sdr.close()

    @pytest.mark.parametrize("split", [None, (1, 3, 5), (12,), (4, 9)], ids=["whole", "bytewise", "header-alone", "into-samples"])
    def test_skipped(self, pieces, split):
        x = (0.5 * tone(1000)).astype(np.complex64)
        data = rtl_header() + convert.UINT8.from_complex64(x).tobytes()
        cuts = [0, *np.cumsum(split or ()).tolist(), len(data)]
        pieces["chunks"] = [data[a:b] for a, b in zip(cuts, cuts[1:]) if b > a]
        y, info = self.read_cu8(pieces["port"], 1000)
        np.testing.assert_array_equal(y, convert.UINT8.to_complex64(data[12:]))
        assert info == {"tuner": "R820T", "tuner_type": 5, "gain_count": 29}

    def test_a_stream_without_it_is_untouched(self, pieces):
        data = b"RT" + bytes(range(256)) * 4   # starts like the magic, then isn't
        pieces["chunks"] = [data[:1], data[1:3], data[3:]]
        y, info = self.read_cu8(pieces["port"], len(data) // 2)
        np.testing.assert_array_equal(y, convert.UINT8.to_complex64(data))
        assert info is None

    def test_listening_each_client(self, listening):
        for tuner in (1, 6):
            with client(listening) as c:
                c.sendall(rtl_header(tuner, 14) + tone(5000).tobytes())
                if tuner == 1:
                    listening.start_rx()
                assert wait_for(lambda: listening.rtl_tcp is not None and listening.rtl_tcp["tuner_type"] == tuner)
            assert wait_for(lambda: listening.peer is None)
        listening.stop_rx()


class TestReconnect:
    """Listening: a client that drops is waited for, and the stream goes on when it (or another) connects"""

    def test_streams_again_after_the_client_reconnects(self, listening):
        x = tone(20_000)
        listening.start_rx()
        with client(listening) as c:
            c.sendall(x[:10_000].tobytes())
            first = listening.read(10_000, timeout=2.0)
        assert wait_for(lambda: listening.peer is None)   # dropped: waiting
        assert listening.state["rx"]
        with client(listening) as c:
            c.sendall(x[10_000:].tobytes())
            second = listening.read(10_000, timeout=2.0)
        np.testing.assert_array_equal(np.concatenate([first, second]), x)
        assert listening.connections == 2 and listening.reconnects == 0
        listening.stop_rx()

    def test_a_read_waits_through_the_gap(self, listening):
        listening.start_rx()
        got = {}
        reader = threading.Thread(target=lambda: got.update(x=listening.read(5000, timeout=5.0)))
        reader.start()
        time.sleep(0.3)   # nobody connected yet
        with client(listening) as c:
            c.sendall(tone(5000).tobytes())
            reader.join(5)
        assert len(got["x"]) == 5000
        listening.stop_rx()

    def test_a_reconnecting_client_takes_over_a_dead_connection(self, listening):
        listening.start_rx()
        stale = client(listening)   # connected, then silent: as a connection whose client vanished
        try:
            assert wait_for(lambda: listening.peer is not None)
            old = listening.peer
            with client(listening) as c:
                c.sendall(tone(5000).tobytes())
                y = listening.read(5000, timeout=2.0)
                assert listening.peer != old and listening.reconnects == 1
            np.testing.assert_array_equal(y, tone(5000))
        finally:
            stale.close()
        listening.stop_rx()


def paced_sender(port, rate, seconds, chunk=2000, hold=0.0):
    """A client that sends cf32 at `rate` samples/s, in real time, for `seconds`, then stays connected `hold` seconds"""
    def send():
        x = tone(chunk).tobytes()
        with socket.create_connection(("127.0.0.1", port), timeout=5) as c:
            t0 = time.monotonic()
            for i in range(int(rate * seconds / chunk)):
                delay = t0 + i * chunk / rate - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                c.sendall(x)
            time.sleep(hold)
    t = threading.Thread(target=send, daemon=True)
    t.start()
    return t


def read_for(sdr, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        sdr.read(1000, timeout=0.2)


class TestMeasuredRate:
    """How fast samples arrive is measured, and checks the declared Fs (it never sets it)"""

    def test_measured_and_agreeing(self, listening, recwarn):
        assert listening.measured_rate is None   # nothing arrived yet
        listening.set_rx_sample_rate(200e3)
        listening.start_rx()
        sender = paced_sender(listening.port, 200e3, 2.0)
        read_for(listening, 1.8)
        assert listening.measured_rate == pytest.approx(200e3, rel=0.1)
        assert listening.get_rx_sample_rate() == 200e3
        assert not [w for w in recwarn if "Fs is declared" in str(w.message)]
        sender.join()

    def test_a_mismatch_is_warned_once(self, listening):
        listening.RATE_WINDOWS = 1
        listening.start_rx()   # declared 1 MS/s; the sender streams 200 kS/s
        sender = paced_sender(listening.port, 200e3, 3.0)
        with pytest.warns(UserWarning, match="Fs is declared as 1,000,000") as caught:   # warn.RateMismatch
            read_for(listening, 2.8)
        assert len([w for w in caught if "Fs is declared" in str(w.message)]) == 1
        assert listening.get_rx_sample_rate() == 1e6   # untouched
        sender.join()

    def test_unknown_once_nothing_arrives(self, listening):
        listening.start_rx()
        sender = paced_sender(listening.port, 200e3, 1.5, hold=4.0)
        assert wait_for(lambda: listening.measured_rate, timeout=3.0)   # still connected, sending
        assert wait_for(lambda: listening.measured_rate is None, timeout=4.0)   # still connected, silent
        assert listening.peer is not None
        sender.join()

    def test_a_client_leaving_forgets_it(self, listening):
        listening.start_rx()
        paced_sender(listening.port, 200e3, 1.5).join()
        assert wait_for(lambda: listening.peer is None)
        assert listening.measured_rate is None
