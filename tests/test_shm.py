"""The shared-memory sample ring (hardware_shm.hpp), on plain buffers: no processes or sockets needed.

Sample i of these tests is the complex number (i mod 2^20) + 0j (float32 holds integers exactly up to 2^24), so whatever a
read returns can be checked against the index it says it starts at: a torn or stale read shows as a value that is not
its index.
"""
import threading
import time

import numpy as np
import pytest

from pysdrlib.base.lib import samples as _samples

ShmRing = _samples.ShmRing


def ramp(first, n):
    return (np.arange(first, first + n) % (1 << 20)).astype(np.complex64)


def pair(capacity, stream_id=7):
    """(writer, reader) on one buffer"""
    buf = bytearray(_samples.shm_segment_size(capacity))
    return ShmRing(buf, capacity, stream_id), ShmRing(buf), buf


class TestRing:
    def test_what_is_published_is_read_with_its_index(self):
        w, r, _ = pair(1000)
        assert r.capacity == 1000 and r.stream_id == 7 and r.head == 0
        w.publish(ramp(0, 100), 0)
        out = np.empty(100, np.complex64)
        assert r.read(out, 0) == (100, 0) and np.array_equal(out, ramp(0, 100)) and r.head == 100

    def test_nothing_new_reads_nothing_at_the_cursor(self):
        w, r, _ = pair(100)
        out = np.empty(10, np.complex64)
        assert r.read(out, 0) == (0, 0)
        w.publish(ramp(0, 10), 0)
        assert r.read(out, 10) == (0, 10)

    def test_a_read_takes_at_most_what_fits(self):
        w, r, _ = pair(100)
        w.publish(ramp(0, 50), 0)
        out = np.empty(20, np.complex64)
        assert r.read(out, 0) == (20, 0) and np.array_equal(out, ramp(0, 20))
        assert r.read(out, 20) == (20, 20) and np.array_equal(out, ramp(20, 20))
        assert r.read(out, 40) == (10, 40) and np.array_equal(out[:10], ramp(40, 10))

    def test_readers_are_independent(self):
        w, r1, buf = pair(100)
        r2 = ShmRing(buf)
        w.publish(ramp(0, 30), 0)
        a, b = np.empty(30, np.complex64), np.empty(10, np.complex64)
        assert r1.read(a, 0) == (30, 0)
        assert r2.read(b, 5) == (10, 5) and np.array_equal(b, ramp(5, 10))   # a cursor of its own
        assert r1.read(a, 0) == (30, 0)                                       # and r1's read took nothing from r2

    def test_the_ring_wraps(self):
        w, r, _ = pair(64)
        out = np.empty(40, np.complex64)
        for k in range(10):
            w.publish(ramp(k * 40, 40), k * 40)
            assert r.read(out, k * 40) == (40, k * 40) and np.array_equal(out, ramp(k * 40, 40))

    def test_a_lapped_reader_continues_from_what_is_still_there(self):
        w, r, _ = pair(100)
        w.publish(ramp(0, 250), 0)   # more than it holds in one go: only the last 100 are kept
        out = np.empty(500, np.complex64)
        n, first = r.read(out, 0)
        assert (n, first) == (100, 150) and np.array_equal(out[:n], ramp(150, 100))
        w.publish(ramp(250, 60), 250)
        n, first = r.read(out, 250)
        assert (n, first) == (60, 250)
        w.publish(ramp(310, 80), 310)
        n, first = r.read(out, 250)   # 140 behind in a 100-sample ring: lapped
        assert first == 290 and n == 100 and np.array_equal(out[:n], ramp(290, 100))

    def test_samples_lost_before_the_ring_are_a_gap_no_read_spans(self):
        w, r, _ = pair(1000)
        w.publish(ramp(0, 10), 0)
        w.publish(ramp(100, 10), 100)   # 10..99 were lost on the way
        out = np.empty(200, np.complex64)
        assert r.read(out, 0) == (10, 0)                                           # stops at the gap
        n, first = r.read(out, 10)
        assert (n, first) == (10, 100) and np.array_equal(out[:n], ramp(100, 10))   # then jumps over it
        assert r.read(out, 120) == (0, 120)

    def test_a_cursor_inside_a_gap_skips_to_its_end(self):
        w, r, _ = pair(1000)
        w.publish(ramp(0, 5), 0)
        w.publish(ramp(50, 5), 50)
        w.publish(ramp(200, 5), 200)
        out = np.empty(10, np.complex64)
        assert r.read(out, 30) == (5, 50)
        assert r.read(out, 55) == (5, 200)

    def test_closing_is_seen_by_readers(self):
        w, r, _ = pair(10)
        assert not r.closed
        w.close()
        assert r.closed

    def test_publishing_out_of_order_is_refused(self):
        w, _, _ = pair(100)
        w.publish(ramp(0, 10), 0)
        with pytest.raises(ValueError):
            w.publish(ramp(5, 10), 5)

    def test_what_is_not_a_ring_is_refused(self):
        with pytest.raises(ValueError):
            ShmRing(bytearray(_samples.shm_segment_size(10)))   # never laid out
        with pytest.raises(ValueError):
            ShmRing(bytearray(100), 10)                          # too small for that capacity
        w, _, buf = pair(100)
        with pytest.raises(ValueError):
            ShmRing(memoryview(buf)[:33000])                     # a reader needs the whole ring

    def test_release_lets_go_of_the_buffer(self):
        buf = bytearray(_samples.shm_segment_size(10))
        w = ShmRing(buf, 10)
        with pytest.raises(BufferError):
            buf.extend(b"x")     # exported while the ring holds it
        w.release()
        buf.extend(b"x")
        with pytest.raises(ValueError):
            w.head


class TestConcurrent:
    def test_a_reader_racing_the_writer_never_sees_torn_or_stale_data(self):
        """A writer publishing as fast as it can into a small ring, with readers that fall behind and get lapped: every
        sample a read returns equals the index the read says it starts at, plus its offset"""
        cap = 4096
        w, _, buf = pair(cap)
        stop = threading.Event()
        bad, reads, lapped = [], [0, 0], [0]

        def write():
            i = 0
            rng = np.random.default_rng(1)
            while not stop.is_set():
                n = int(rng.integers(1, 3000))
                if rng.random() < 0.05:   # now and then samples are lost before the ring
                    skip = int(rng.integers(1, 500))
                    w.publish(ramp(i + skip, n), i + skip)
                    i += skip + n
                else:
                    w.publish(ramp(i, n), i)
                    i += n

        def read(k, delay):
            r = ShmRing(buf)
            out = np.empty(1500, np.complex64)
            pos = 0
            while not stop.is_set():
                n, first = r.read(out, pos)
                if first > pos and pos:
                    lapped[0] += 1
                if n and not np.array_equal(out[:n].real, np.arange(first, first + n) % (1 << 20)):
                    bad.append((first, n))
                    return
                pos = first + n
                reads[k] += n
                if delay:
                    time.sleep(delay)   # a slow reader: it is lapped
            r.release()

        threads = [threading.Thread(target=write)] + [threading.Thread(target=read, args=(k, d)) for k, d in ((0, 0), (1, 0.002))]
        for t in threads:
            t.start()
        time.sleep(1.5)
        stop.set()
        for t in threads:
            t.join()
        assert not bad, f"torn or stale reads: {bad[:3]}"
        assert reads[0] > 100000 and reads[1] > 0 and lapped[0] > 0   # it really raced, and the slow one really was lapped
