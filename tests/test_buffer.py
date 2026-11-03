"""Tests for the RotatingBuffer class.

Semantics: update() appends, reading drains oldest-first, and when more than the capacity is
pending the oldest samples are dropped (and counted in `dropped`).
"""
import threading
import time

import numpy as np
import pytest

from pysdrlib.base import buffer


def seq(start, count):
    """Samples whose real part is their sequence number (imag = 2 * real)"""
    i = np.arange(start, start + count, dtype=np.float32)
    return (i + 2j * i).astype(np.complex64)


def real(arr):
    return arr.real.astype(np.int64).tolist()


class TestRotatingBuffer:
    """Test the RotatingBuffer class."""

    def test_initial_state(self):
        """Test initial buffer state."""
        rot_buf = buffer.RotatingBuffer()
        assert rot_buf.capacity == 0
        assert len(rot_buf.samples) == 0
        assert rot_buf.available == 0
        assert rot_buf.dropped == 0

    def test_reset(self):
        """Reset sets capacity and leaves nothing pending."""
        rot_buf = buffer.RotatingBuffer()
        rot_buf.reset(100)

        assert rot_buf.capacity == 100
        assert rot_buf.available == 0
        assert len(rot_buf.samples) == 0
        assert rot_buf.samples.dtype == 'complex64'

    def test_reset_discards_pending_and_dropped(self):
        rot_buf = buffer.RotatingBuffer()
        rot_buf.reset(10)
        rot_buf.update(seq(0, 25))  # overflows
        assert rot_buf.dropped == 15 and rot_buf.available == 10
        rot_buf.reset(10)
        assert rot_buf.available == 0
        assert rot_buf.dropped == 0
        assert len(rot_buf.samples) == 0

    def test_update_and_samples(self):
        """Test update and samples retrieval."""
        rot_buf = buffer.RotatingBuffer()
        rot_buf.reset(100)

        test_samples = [complex(i, i*2) for i in range(50)]
        rot_buf.update(test_samples)

        retrieved = rot_buf.samples

        assert len(retrieved) == 50
        assert retrieved.dtype == 'complex64'
        for expected, actual in zip(test_samples, retrieved):
            assert expected == actual

    def test_wraparound(self):
        """A single update larger than the capacity keeps only the newest samples."""
        rot_buf = buffer.RotatingBuffer()
        rot_buf.reset(10)

        rot_buf.update([complex(i, i) for i in range(20)])

        retrieved = rot_buf.samples
        assert len(retrieved) == 10
        for i in range(10):
            assert retrieved[i] == complex(10+i, 10+i)
        assert rot_buf.dropped == 10

    def test_partial_wraparound(self):
        """Read between updates, then write past the end of the ring."""
        rot_buf = buffer.RotatingBuffer()
        rot_buf.reset(10)

        rot_buf.update(seq(0, 5))
        assert real(rot_buf.samples) == [0, 1, 2, 3, 4]

        # write position is now 5; 8 more samples wrap past the end of the ring
        rot_buf.update(seq(5, 8))
        assert real(rot_buf.samples) == list(range(5, 13))
        assert rot_buf.dropped == 0

    def test_overflow_keeps_newest_in_order(self):
        """Unread data older than the capacity is dropped, order preserved."""
        rot_buf = buffer.RotatingBuffer()
        rot_buf.reset(10)

        rot_buf.update(seq(0, 5))
        rot_buf.update(seq(5, 8))  # 13 pending, capacity 10
        assert rot_buf.dropped == 3
        assert real(rot_buf.samples) == list(range(3, 13))

    @pytest.mark.parametrize("chunks", [[3, 3, 3, 3], [1] * 25, [7, 11, 2, 9], [25]])
    def test_many_wraps_preserve_order(self, chunks):
        rot_buf = buffer.RotatingBuffer()
        rot_buf.reset(10)
        got, n = [], 0
        for c in chunks:
            rot_buf.update(seq(n, c))
            n += c
            got += real(rot_buf.samples)
        assert got[-1] == n - 1  # newest sample always delivered
        assert got == sorted(got)  # never reordered
        assert len(got) + rot_buf.dropped == n

    def test_multiple_updates(self):
        """Reading drains: each read returns only what arrived since the previous read."""
        rot_buf = buffer.RotatingBuffer()
        rot_buf.reset(50)

        rot_buf.update([complex(i, i*0.5) for i in range(20)])
        assert len(rot_buf.samples) == 20

        rot_buf.update([complex(20+i, (20+i)*0.5) for i in range(15)])
        assert len(rot_buf.samples) == 15

        rot_buf.update([complex(35+i, (35+i)*0.5) for i in range(10)])
        assert len(rot_buf.samples) == 10

    def test_updates_accumulate_until_read(self):
        rot_buf = buffer.RotatingBuffer()
        rot_buf.reset(50)
        rot_buf.update(seq(0, 20))
        rot_buf.update(seq(20, 15))
        rot_buf.update(seq(35, 10))
        assert real(rot_buf.samples) == list(range(45))

    def test_empty_samples(self):
        """Reading before any update returns an empty array."""
        rot_buf = buffer.RotatingBuffer()
        rot_buf.reset(50)
        assert len(rot_buf.samples) == 0

    def test_empty_update_is_noop(self):
        rot_buf = buffer.RotatingBuffer()
        rot_buf.reset(10)
        rot_buf.update([])
        assert rot_buf.available == 0 and rot_buf.dropped == 0

    def test_update_before_reset_counts_as_dropped(self):
        rot_buf = buffer.RotatingBuffer()
        rot_buf.update(seq(0, 4))
        assert len(rot_buf.samples) == 0
        assert rot_buf.dropped == 4

    def test_exact_fill(self):
        rot_buf = buffer.RotatingBuffer()
        rot_buf.reset(10)
        rot_buf.update(seq(0, 10))
        assert rot_buf.dropped == 0
        assert real(rot_buf.samples) == list(range(10))

    def test_thread_safety(self):
        """Concurrent producers don't lose or corrupt samples."""
        rot_buf = buffer.RotatingBuffer()
        rot_buf.reset(100)

        def update_samples(start_idx, count):
            rot_buf.update([complex(start_idx+i, start_idx+i*2) for i in range(count)])

        threads = [threading.Thread(target=update_samples, args=(i*10, 10)) for i in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        samples = rot_buf.samples
        assert len(samples) == 50  # 5 threads * 10 samples each
        assert np.iscomplexobj(samples)
        # each producer's block stays contiguous and in order
        starts = sorted(int(samples[i].real) for i in range(0, 50, 10))
        assert starts == [0, 10, 20, 30, 40]

    def test_samples_property_behavior(self):
        """Reading clears pending samples."""
        rot_buf = buffer.RotatingBuffer()
        rot_buf.reset(100)
        rot_buf.update([complex(i, i) for i in range(30)])

        assert len(rot_buf.samples) == 30
        assert len(rot_buf.samples) == 0

    def test_returned_array_is_independent(self):
        """Later updates must not change an array the consumer already holds."""
        rot_buf = buffer.RotatingBuffer()
        rot_buf.reset(10)
        rot_buf.update(seq(0, 10))
        held = rot_buf.samples
        rot_buf.update(seq(100, 10))
        assert real(held) == list(range(10))

    def test_complex_numbers(self):
        """Test buffer handles complex numbers correctly."""
        rot_buf = buffer.RotatingBuffer()
        rot_buf.reset(20)

        test_samples = [complex(1, 2), complex(-3, -4), complex(5, 0), complex(0, 7), complex(-8, -9)]
        rot_buf.update(test_samples)

        retrieved = rot_buf.samples
        assert len(retrieved) == 5
        for expected, actual in zip(test_samples, retrieved):
            assert expected == actual

    def test_large_buffer(self):
        """Test buffer with large size."""
        rot_buf = buffer.RotatingBuffer()
        rot_buf.reset(10000)
        rot_buf.update([complex(i, i) for i in range(100)])

        retrieved = rot_buf.samples
        assert len(retrieved) == 100
        assert retrieved[0] == complex(0, 0)
        assert retrieved[50] == complex(50, 50)
        assert retrieved[99] == complex(99, 99)

    def test_accepts_complex128_input(self):
        """Drivers may hand over complex128; storage is complex64."""
        rot_buf = buffer.RotatingBuffer()
        rot_buf.reset(10)
        rot_buf.update(np.array([0.5 - 0.25j, 1 + 1j]))
        assert rot_buf.samples.dtype == np.complex64


class TestRead:
    """read(n) lets a consumer work through a backlog in bounded chunks."""

    def test_read_chunks_cover_every_sample(self):
        rot_buf = buffer.RotatingBuffer()
        rot_buf.reset(100)
        rot_buf.update(seq(0, 70))
        got = []
        while rot_buf.available:
            got += real(rot_buf.read(16))
        assert got == list(range(70))

    def test_read_more_than_available(self):
        rot_buf = buffer.RotatingBuffer()
        rot_buf.reset(10)
        rot_buf.update(seq(0, 4))
        assert real(rot_buf.read(100)) == [0, 1, 2, 3]
        assert len(rot_buf.read(5)) == 0

    def test_read_zero_and_negative(self):
        rot_buf = buffer.RotatingBuffer()
        rot_buf.reset(10)
        rot_buf.update(seq(0, 4))
        assert len(rot_buf.read(0)) == 0
        assert len(rot_buf.read(-3)) == 0
        assert rot_buf.available == 4

    def test_partial_read_then_wrapping_write(self):
        rot_buf = buffer.RotatingBuffer()
        rot_buf.reset(10)
        rot_buf.update(seq(0, 8))
        assert real(rot_buf.read(5)) == [0, 1, 2, 3, 4]  # 5,6,7 pending
        rot_buf.update(seq(8, 6))                         # wraps; 9 pending
        assert real(rot_buf.read()) == list(range(5, 14))
        assert rot_buf.dropped == 0

    def test_partial_read_then_overflow_drops_oldest_pending(self):
        rot_buf = buffer.RotatingBuffer()
        rot_buf.reset(10)
        rot_buf.update(seq(0, 8))
        rot_buf.read(5)           # 5,6,7 pending
        rot_buf.update(seq(8, 9))  # 12 pending -> drop 5,6
        assert rot_buf.dropped == 2
        assert real(rot_buf.read()) == list(range(7, 17))

    def test_len_and_available(self):
        rot_buf = buffer.RotatingBuffer()
        rot_buf.reset(10)
        rot_buf.update(seq(0, 6))
        assert len(rot_buf) == rot_buf.available == 6

    def test_clear_dropped(self):
        rot_buf = buffer.RotatingBuffer()
        rot_buf.reset(4)
        rot_buf.update(seq(0, 9))
        assert rot_buf.clear_dropped() == 5
        assert rot_buf.dropped == 0


class TestStreaming:
    """Producer thread (the driver callback) and consumer thread running together."""

    @staticmethod
    def _run(capacity, block, blocks, read_every, read_count=None):
        rot_buf = buffer.RotatingBuffer()
        rot_buf.reset(capacity)
        done = threading.Event()
        got = []

        def producer():
            n = 0
            for _ in range(blocks):
                rot_buf.update(seq(n, block))
                n += block
            done.set()

        def consumer():
            while not done.is_set() or rot_buf.available:
                got.append(real(rot_buf.read(read_count)))
                time.sleep(read_every)

        threads = [threading.Thread(target=producer), threading.Thread(target=consumer)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert not any(t.is_alive() for t in threads)
        flat = [x for chunk in got for x in chunk]
        return rot_buf, flat, block * blocks

    def test_keeping_up_sees_every_sample(self):
        """Capacity covers the gap between reads: nothing lost, nothing repeated."""
        rot_buf, flat, total = self._run(capacity=1_000_000, block=4096, blocks=200, read_every=0.0005)
        assert rot_buf.dropped == 0
        assert flat == list(range(total))

    def test_falling_behind_drops_but_stays_ordered_and_accounted(self):
        """Tiny ring + slow reader: oldest samples are dropped, none duplicated or reordered,
        and delivered + dropped == produced."""
        rot_buf, flat, total = self._run(capacity=5_000, block=4096, blocks=300, read_every=0.002)
        assert rot_buf.dropped > 0
        assert flat == sorted(set(flat))
        assert len(flat) + rot_buf.dropped == total
        assert flat[-1] == total - 1  # newest data is always delivered

    def test_bounded_chunk_reads_see_every_sample(self):
        rot_buf, flat, total = self._run(capacity=1_000_000, block=4096, blocks=100,
                                         read_every=0.0005, read_count=1024)
        assert rot_buf.dropped == 0
        assert flat == list(range(total))

    def test_update_cost_does_not_grow_with_fill(self):
        """Appending to a full ring costs the same as to an empty one (no memmove)."""
        block = seq(0, 16384)

        def per_update(prefill):
            rot_buf = buffer.RotatingBuffer()
            rot_buf.reset(2_000_000)
            for _ in range(prefill):
                rot_buf.update(block)
            t = time.perf_counter()
            for _ in range(200):
                rot_buf.update(block)
            return (time.perf_counter() - t) / 200

        empty, full = per_update(0), per_update(200)  # 200 * 16384 > capacity: ring is full
        assert full < max(empty * 5, 0.002)
