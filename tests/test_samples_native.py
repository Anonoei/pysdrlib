"""The C++ sample conversion and ring (pysdrlib.base.lib._samples), behind pysdrlib.base.convert and
pysdrlib.base.buffer, against the original pure-numpy implementations kept in tests/reference.

Those are the spec: any difference is a bug in one of the two. No device or vendor library is involved.
"""
import threading

import numpy as np
import pytest

from pysdrlib.base import buffer, convert
from pysdrlib.base.lib import samples

from reference import buffer_numpy, convert_numpy

S = convert.SampleFormat

# Every layout the library supports, little and big endian
FORMATS = [
    S("int", 8, 8), S("uint", 8, 8), S("int", 16, 16), S("uint", 16, 16), S("int", 12, 16), S("uint", 12, 16),
    S("int", 32, 32), S("uint", 32, 32), S("int", 24, 32), S("int", 64, 64), S("uint", 64, 64),
    S("float", 32, 32), S("float", 64, 64), S("int", 12, 12, packed=True),
    *(S(f.kind, f.bits, f.storage, byteorder=">") for f in (
        S("int", 16, 16), S("uint", 16, 16), S("int", 12, 16), S("int", 32, 32), S("uint", 64, 64),
        S("float", 32, 32), S("float", 64, 64))),
]
# Layouts whose float32 arithmetic is well defined for any input (wider integers overflow float32's range
# the way numpy's astype does, which the reference leaves to the platform)
ENCODABLE = [f for f in FORMATS if f.storage <= 16 or f.kind == "float"]


def ref(fmt):
    return convert_numpy.SampleFormat(fmt.kind, fmt.bits, fmt.storage, fmt.packed, fmt.byteorder)


def ids(fmt):
    return f"{fmt}{'>' if fmt.byteorder == '>' else ''}"


def random_raw(fmt, n, seed=0):
    """n random complex samples of raw data in this layout (random bytes; finite values for floats)"""
    rng = np.random.default_rng(seed)
    if fmt.kind == "float":
        return (rng.standard_normal(2 * n) * 3).astype(fmt.dtype).view(np.uint8)
    return rng.integers(0, 256, n * fmt.bytes_per_sample, dtype=np.uint8)


class TestDecode:
    @pytest.mark.parametrize("fmt", FORMATS, ids=ids)
    def test_identical_to_the_numpy_reference(self, fmt):
        raw = random_raw(fmt, 5001)
        got, want = fmt.to_complex64(raw), ref(fmt).to_complex64(raw)
        assert got.dtype == np.complex64
        np.testing.assert_array_equal(got.view(np.uint32), want.view(np.uint32))   # bit for bit

    @pytest.mark.filterwarnings("ignore::RuntimeWarning")   # numpy warns about the NaNs and overflows it casts
    @pytest.mark.parametrize("fmt", [f for f in FORMATS if f.kind == "float"], ids=ids)
    def test_floats_pass_through_even_when_not_finite(self, fmt):
        raw = np.random.default_rng(1).integers(0, 256, 4000 * fmt.bytes_per_sample, dtype=np.uint8)   # NaNs, infs
        np.testing.assert_array_equal(fmt.to_complex64(raw), ref(fmt).to_complex64(raw))   # NaN == NaN here

    @pytest.mark.parametrize("fmt", FORMATS, ids=ids)
    def test_valid_bytes_and_partial_samples(self, fmt):
        raw = random_raw(fmt, 100)
        for valid in (0, 1, fmt.bytes_per_sample - 1, fmt.bytes_per_sample, 7 * fmt.bytes_per_sample + 1, 10_000):
            np.testing.assert_array_equal(fmt.to_complex64(raw, valid), ref(fmt).to_complex64(raw, valid))

    @pytest.mark.parametrize("fmt", FORMATS[:6], ids=ids)
    def test_any_buffer_type_and_alignment(self, fmt):
        raw = random_raw(fmt, 64)
        want = ref(fmt).to_complex64(raw)
        np.testing.assert_array_equal(fmt.to_complex64(raw.tobytes()), want)             # bytes
        np.testing.assert_array_equal(fmt.to_complex64(bytearray(raw.tobytes())), want)  # bytearray
        np.testing.assert_array_equal(fmt.to_complex64(memoryview(raw)), want)
        shifted = np.concatenate([[0], raw]).astype(np.uint8)[1:]                        # misaligned start
        np.testing.assert_array_equal(fmt.to_complex64(shifted), want)

    def test_an_array_of_another_dtype_is_read_as_memory(self):
        """A driver may hand over uint8 bytes for 16-bit data, or a typed array for the same bytes"""
        words = np.array([1000, -1000, 5, -5], dtype=np.int16)
        np.testing.assert_array_equal(convert.INT16.to_complex64(words), convert.INT16.to_complex64(words.view(np.uint8)))

    def test_empty(self):
        for fmt in FORMATS:
            out = fmt.to_complex64(np.empty(0, dtype=np.uint8))
            assert out.dtype == np.complex64 and len(out) == 0

    def test_a_strided_array_is_made_contiguous_by_convert_but_not_by_the_extension(self):
        strided = np.arange(40, dtype=np.int8)[::2]
        np.testing.assert_array_equal(convert.INT8.to_complex64(strided), ref(convert.INT8).to_complex64(strided))
        with pytest.raises(ValueError):
            samples.decode(strided, "int", 8, 8)

    def test_a_2d_array_is_read_in_memory_order(self):
        raw = random_raw(convert.INT16, 64).view(np.int16).reshape(8, 16)
        np.testing.assert_array_equal(convert.INT16.to_complex64(raw), ref(convert.INT16).to_complex64(raw.reshape(-1)))

    def test_a_layout_the_extension_cannot_read_is_rejected(self):
        for args in (("float", 16, 16, False), ("int", 12, 12, False), ("uint", 12, 12, True), ("int", 40, 32, False),
                     ("bogus", 8, 8, False)):
            with pytest.raises(ValueError):
                samples.decode(b"\0" * 8, *args)


class TestEncode:
    @staticmethod
    def values(n=6000, seed=0):
        """Within and beyond [-1, 1], plus exact halves (ties) and the edges"""
        rng = np.random.default_rng(seed)
        parts = [rng.uniform(-1.5, 1.5, n), rng.standard_normal(n) * 100, np.arange(-300, 301) / 256,
                 [0, 1, -1, 0.5, -0.5, 127 / 128, -128 / 128, 1e30, -1e30, np.inf, -np.inf]]
        flat = np.concatenate(parts).astype(np.float32)
        return flat[: len(flat) // 2 * 2].view(np.complex64)

    @pytest.mark.parametrize("fmt", ENCODABLE, ids=ids)
    def test_identical_to_the_numpy_reference(self, fmt):
        iq = self.values()
        got, want = fmt.from_complex64(iq), ref(fmt).from_complex64(iq)
        assert got.dtype == want.dtype and got.shape == want.shape
        np.testing.assert_array_equal(got, want)

    @pytest.mark.parametrize("fmt", ENCODABLE, ids=ids)
    def test_encoding_what_was_decoded_is_a_fixed_point(self, fmt):
        """raw -> complex64 -> raw -> complex64 -> raw changes nothing the second time. (The first pass may
        clamp: random 16-bit words are outside a 12-bit layout's range.)"""
        once = fmt.from_complex64(fmt.to_complex64(random_raw(fmt, 3000)))
        twice = fmt.from_complex64(fmt.to_complex64(once.view(np.uint8)))
        np.testing.assert_array_equal(once, twice)

    @pytest.mark.parametrize("fmt", [f for f in ENCODABLE if f.kind != "float" and f.bits == f.storage], ids=ids)
    def test_a_layout_that_uses_its_whole_width_roundtrips_exactly(self, fmt):
        raw = random_raw(fmt, 3000)
        np.testing.assert_array_equal(fmt.from_complex64(fmt.to_complex64(raw)).view(np.uint8), raw)

    def test_packed_12_bit_roundtrips_exactly(self):
        raw = random_raw(S("int", 12, 12, packed=True), 3000)
        fmt = S("int", 12, 12, packed=True)
        np.testing.assert_array_equal(fmt.from_complex64(fmt.to_complex64(raw)), raw)

    def test_uint8_is_offset_binary(self):
        out = convert.UINT8.from_complex64(np.array([-1 - 1j, 1 + 1j, 0 + 0j], dtype=np.complex64))
        assert out.tolist() == [0, 0, 255, 255, 128, 128]   # mid-scale 127.5 rounds to even

    def test_ties_round_to_even(self):
        out = convert.INT8.from_complex64(np.array([0.5 / 128 + 0j, 1.5 / 128 + 0j, 2.5 / 128 + 0j], dtype=np.complex64))
        assert out.tolist() == [0, 0, 2, 0, 2, 0]

    @pytest.mark.parametrize("fmt", [f for f in FORMATS if f.kind != "float"], ids=ids)
    def test_out_of_range_saturates(self, fmt):
        top = fmt.from_complex64(np.array([1e30 + 1e30j], dtype=np.complex64)).view(np.uint8)
        bottom = fmt.from_complex64(np.array([-1e30 - 1e30j], dtype=np.complex64)).view(np.uint8)
        hi, lo = fmt.to_complex64(top), fmt.to_complex64(bottom)
        assert hi.real[0] > 0.99 and hi.imag[0] > 0.99 and lo.real[0] < -0.99 and lo.imag[0] < -0.99
        if fmt.kind == "uint":
            assert lo.real[0] == -1 and hi.real[0] == 1   # exactly the end stops

    def test_not_a_number_is_the_lowest_value_not_undefined(self):
        out = convert.INT16.from_complex64(np.array([np.nan + 0j], dtype=np.complex64))
        assert out.tolist() == [-32768, 0]

    def test_accepts_other_complex_and_real_input(self):
        want = convert.INT16.from_complex64(np.array([0.25 - 0.5j], dtype=np.complex64))
        np.testing.assert_array_equal(convert.INT16.from_complex64(np.array([0.25 - 0.5j])), want)   # complex128
        np.testing.assert_array_equal(convert.INT16.from_complex64([0.25 - 0.5j]), want)             # a list
        assert convert.INT16.from_complex64(np.array([0.25, 0.5])).tolist() == [8192, 0, 16384, 0]

    def test_empty(self):
        for fmt in FORMATS:
            assert len(fmt.from_complex64(np.empty(0, dtype=np.complex64))) == 0


def apply(ring, op):
    """Run one of a random sequence of operations on a ring, returning what it observed"""
    name, arg = op
    if name == "update":
        ring.update(arg)
    elif name == "read":
        return ring.read(arg)
    elif name == "reset":
        ring.reset(arg)
    elif name == "clear":
        return ring.clear_dropped()


class TestRing:
    """The C++ ring behind RotatingBuffer, against the original numpy one, operation by operation"""

    def test_random_operations_match_the_numpy_reference(self):
        rng = np.random.default_rng(11)
        new, old = buffer.RotatingBuffer(), buffer_numpy.RotatingBuffer()
        for ring in (new, old):
            ring.reset(97)
        counter = 0
        for _ in range(3000):
            r = rng.random()
            if r < 0.55:
                n = int(rng.integers(0, 260))
                chunk = (np.arange(counter, counter + n) + 1j * np.arange(n)).astype(np.complex64)
                counter += n
                op = ("update", chunk)
            elif r < 0.9:
                op = ("read", None if rng.random() < 0.4 else int(rng.integers(0, 120)))
            elif r < 0.95:
                op = ("clear", None)
            else:
                op = ("reset", int(rng.integers(0, 200)))
            a, b = apply(new, op), apply(old, op)
            if isinstance(a, np.ndarray):
                np.testing.assert_array_equal(a, b)
            else:
                assert a == b
            assert (new.available, new.dropped, new.capacity) == (old._idx, old.dropped, old._N)

    def test_accepts_lists_and_other_dtypes_and_copies_them(self):
        ring = buffer.RotatingBuffer()
        ring.reset(8)
        src = np.array([1 + 2j, 3 + 4j])           # complex128
        ring.update(src)
        ring.update([5 + 6j])
        src[:] = 0
        assert ring.samples.tolist() == [1 + 2j, 3 + 4j, 5 + 6j]

    def test_the_samples_property_reads_everything_and_len_is_available(self):
        ring = buffer.RotatingBuffer()
        ring.reset(8)
        ring.update(np.arange(5, dtype=np.complex64))
        assert len(ring) == ring.available == 5
        assert len(ring.samples) == 5 and len(ring) == 0

    def test_is_a_subclass_of_the_extension_ring(self):
        assert isinstance(buffer.RotatingBuffer(), samples.Ring)

    def test_a_reader_and_writers_in_threads_lose_nothing(self):
        ring = buffer.RotatingBuffer()
        ring.reset(500)
        chunk = np.ones(37, dtype=np.complex64)
        got, done = [0], threading.Event()
        def produce():
            for _ in range(2000):
                ring.update(chunk)
        def consume():
            while not done.is_set():
                got[0] += len(ring.read())
        writers = [threading.Thread(target=produce) for _ in range(3)]
        reader = threading.Thread(target=consume)
        reader.start()
        for t in writers: t.start()
        for t in writers: t.join()
        done.set()
        reader.join()
        got[0] += len(ring.read())
        assert got[0] + ring.dropped == 3 * 2000 * 37

    def test_reading_while_the_gil_is_released_does_not_deadlock_with_writers(self):
        """update/read drop the GIL for the copy: a thread blocked on the ring's lock must not need it"""
        ring = buffer.RotatingBuffer()
        ring.reset(1_000_000)
        big = np.zeros(900_000, dtype=np.complex64)
        stop = threading.Event()
        def churn():
            while not stop.is_set():
                ring.update(big)
                ring.read()
        threads = [threading.Thread(target=churn) for _ in range(4)]
        for t in threads: t.start()
        threading.Event().wait(0.3)
        stop.set()
        for t in threads:
            t.join(10)
            assert not t.is_alive()
