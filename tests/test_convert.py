"""Tests for base.convert: raw device layouts -> complex64, for any ADC format."""
import numpy as np
import pytest

from pysdrlib.base import convert
from pysdrlib.base.convert import SampleFormat

ALL = {
    "INT8": convert.INT8, "UINT8": convert.UINT8, "INT16": convert.INT16, "INT12": convert.INT12,
    "INT12_PACKED": convert.INT12_PACKED, "FLOAT32": convert.FLOAT32,
}


def tone(n=2000, seed=0):
    rng = np.random.default_rng(seed)
    return (rng.uniform(-0.99, 0.99, n) + 1j * rng.uniform(-0.99, 0.99, n)).astype(np.complex64)


class TestDescriptor:
    def test_properties(self):
        assert convert.INT8.dtype == np.int8 and convert.INT8.bytes_per_sample == 2
        assert convert.INT12.dtype == np.int16 and convert.INT12.bytes_per_sample == 4
        assert convert.INT12_PACKED.dtype is None and convert.INT12_PACKED.bytes_per_sample == 3
        assert convert.FLOAT32.dtype == np.float32 and convert.FLOAT32.bytes_per_sample == 8
        assert convert.UINT8.dtype == np.uint8

    def test_full_scale(self):
        assert convert.INT8.full_scale == 128
        assert convert.INT12.full_scale == 2048        # significant bits, not storage
        assert convert.UINT8.full_scale == 127.5
        assert convert.FLOAT32.full_scale == 1.0

    @pytest.mark.parametrize("bad", [
        dict(kind="bcd", bits=8, storage=8),
        dict(kind="int", bits=0, storage=8),
        dict(kind="int", bits=17, storage=16),
        dict(kind="int", bits=12, storage=12),            # packed must say so
        dict(kind="int", bits=12, storage=12, packed=True, byteorder="x"),
        dict(kind="uint", bits=12, storage=12, packed=True),
        dict(kind="float", bits=16, storage=16),
        dict(kind="int", bits=8, storage=24),
    ])
    def test_invalid(self, bad):
        with pytest.raises(ValueError):
            SampleFormat(**bad)

    def test_hashable_and_comparable(self):
        assert SampleFormat("int", 8, 8) == convert.INT8
        assert len({convert.INT8, SampleFormat("int", 8, 8)}) == 1

    def test_str(self):
        assert str(convert.INT12) == "int12 in 16"
        assert str(convert.INT12_PACKED) == "int12 packed"


class TestToComplex64:
    @pytest.mark.parametrize("name", ALL)
    def test_always_complex64(self, name):
        fmt = ALL[name]
        out = fmt.to_complex64(fmt.from_complex64(tone()))
        assert out.dtype == np.complex64 and out.ndim == 1

    def test_int8_scaling(self):
        out = convert.INT8.to_complex64(np.array([64, -64, -128, 127], dtype=np.int8))
        np.testing.assert_allclose(out, [0.5 - 0.5j, complex(-1, 127 / 128)])

    def test_int12_in_int16_words(self):
        """12-bit values (-2048..2047) in 16-bit words scale by 2048, not 32768"""
        raw = np.array([2047, -2048, 1024, 0], dtype=np.int16)
        np.testing.assert_allclose(convert.INT12.to_complex64(raw), [complex(2047 / 2048, -1), complex(0.5, 0)])

    def test_uint8_offset_binary(self):
        """RTL-SDR style: 127.5 is zero"""
        out = convert.UINT8.to_complex64(np.array([0, 255, 255, 0, 128, 127], dtype=np.uint8))
        np.testing.assert_allclose(out, [-1 + 1j, 1 - 1j, complex(0.5 / 127.5, -0.5 / 127.5)])

    def test_int16(self):
        out = convert.INT16.to_complex64(np.array([16384, -32768], dtype=np.int16))
        np.testing.assert_allclose(out, [0.5 - 1j])

    def test_float32_passthrough(self):
        raw = np.array([0.25, -0.5, 1.5, 0], dtype=np.float32)
        np.testing.assert_array_equal(convert.FLOAT32.to_complex64(raw), [0.25 - 0.5j, 1.5 + 0j])

    def test_big_endian(self):
        be = SampleFormat("int", 16, 16, byteorder=">")
        raw = np.array([16384, -32768], dtype=">i2")
        np.testing.assert_allclose(be.to_complex64(raw), [0.5 - 1j])
        np.testing.assert_allclose(be.to_complex64(raw.tobytes()), [0.5 - 1j])

    def test_matches_naive_complex128_math(self):
        raw = np.random.default_rng(0).integers(-128, 128, 1000, dtype=np.int8)
        naive = (raw[0::2] + 1j * raw[1::2]) / 128
        np.testing.assert_array_equal(convert.INT8.to_complex64(raw), naive.astype(np.complex64))


class TestInputs:
    def test_bytes_like(self):
        raw = np.array([1000, -1000, 5, 6], dtype=np.int16)
        for data in (raw.tobytes(), bytearray(raw.tobytes()), memoryview(raw.tobytes())):
            np.testing.assert_allclose(convert.INT16.to_complex64(data), raw[0::2] / 32768 + 1j * (raw[1::2] / 32768))

    def test_byte_view_of_wider_data(self):
        """A driver may hand over uint8 bytes for 16-bit data"""
        raw = np.array([1000, -1000], dtype=np.int16)
        out = convert.INT16.to_complex64(raw.view(np.uint8))
        np.testing.assert_allclose(out, [complex(1000 / 32768, -1000 / 32768)])

    def test_valid_is_in_bytes(self):
        raw = np.arange(16, dtype=np.int16)
        out = convert.INT16.to_complex64(raw, valid=8)   # 8 bytes = 4 components = 2 samples
        assert len(out) == 2
        np.testing.assert_allclose(out, [complex(0, 1 / 32768), complex(2 / 32768, 3 / 32768)])

    def test_valid_for_int8_equals_components(self):
        assert len(convert.INT8.to_complex64(np.arange(8, dtype=np.int8), 4)) == 2

    def test_odd_component_count_drops_dangling_one(self):
        assert len(convert.INT8.to_complex64(np.arange(5, dtype=np.int8))) == 2

    def test_valid_not_multiple_of_component_size(self):
        assert len(convert.INT16.to_complex64(np.arange(8, dtype=np.int16), valid=7)) == 1

    @pytest.mark.parametrize("name", ALL)
    def test_empty(self, name):
        fmt = ALL[name]
        empty = np.empty(0, dtype=np.uint8 if fmt.packed else fmt.dtype)
        assert len(fmt.to_complex64(empty)) == 0

    @pytest.mark.parametrize("name", ALL)
    def test_returns_new_array(self, name):
        fmt = ALL[name]
        raw = fmt.from_complex64(tone(8))
        before = raw.copy()
        out = fmt.to_complex64(raw)
        out[:] = 99
        np.testing.assert_array_equal(raw, before)

    def test_non_contiguous_input(self):
        raw = np.arange(40, dtype=np.int8)[::2]
        np.testing.assert_allclose(convert.INT8.to_complex64(raw), (raw[0::2] + 1j * raw[1::2]) / 128)


class TestPacked12:
    def test_known_bytes(self):
        """3 bytes = I (12 bits, low byte first) then Q"""
        # I = 0x123 = 291, Q = 0xABC = -1348 (two's compliment 12-bit)
        raw = np.array([0x23, 0xC1, 0xAB], dtype=np.uint8)
        out = convert.INT12_PACKED.to_complex64(raw)
        np.testing.assert_allclose(out, [complex(291 / 2048, -1348 / 2048)])

    def test_extremes(self):
        # I = 0x7FF (max), Q = 0x800 (min)
        out = convert.INT12_PACKED.to_complex64(np.array([0xFF, 0x07, 0x80], dtype=np.uint8))
        np.testing.assert_allclose(out, [complex(2047 / 2048, -1)])

    def test_partial_trailing_sample_dropped(self):
        raw = np.array([0x23, 0xC1, 0xAB, 0xFF, 0x07], dtype=np.uint8)
        assert len(convert.INT12_PACKED.to_complex64(raw)) == 1

    def test_valid_in_bytes(self):
        raw = np.zeros(9, dtype=np.uint8)
        assert len(convert.INT12_PACKED.to_complex64(raw, valid=6)) == 2

    def test_matches_unpacked_12bit(self):
        iq = tone(500)
        np.testing.assert_array_equal(convert.INT12_PACKED.to_complex64(convert.INT12_PACKED.from_complex64(iq)),
                                      convert.INT12.to_complex64(convert.INT12.from_complex64(iq)))


class TestRoundTrip:
    @pytest.mark.parametrize("name,tol", [
        ("INT8", 1 / 128), ("UINT8", 1 / 127.5), ("INT16", 1 / 32768),
        ("INT12", 1 / 2048), ("INT12_PACKED", 1 / 2048), ("FLOAT32", 0),
    ])
    def test_within_one_lsb(self, name, tol):
        fmt, iq = ALL[name], tone()
        back = fmt.to_complex64(fmt.from_complex64(iq))
        assert np.abs(back.real - iq.real).max() <= tol and np.abs(back.imag - iq.imag).max() <= tol

    @pytest.mark.parametrize("name", ALL)
    def test_raw_size(self, name):
        fmt = ALL[name]
        assert fmt.from_complex64(tone(100)).nbytes == 100 * fmt.bytes_per_sample

    @pytest.mark.parametrize("name", [n for n in ALL if n != "FLOAT32"])
    def test_out_of_range_clips(self, name):
        fmt = ALL[name]
        out = fmt.to_complex64(fmt.from_complex64(np.array([5 - 5j, -5 + 5j], dtype=np.complex64)))
        assert np.abs(out.real).max() <= 1.0 and np.abs(out.imag).max() <= 1.0
        assert out[0].real > 0.99 and out[0].imag < -0.99    # clipped to full scale, not wrapped

    def test_raw_dtypes(self):
        assert convert.INT8.from_complex64(tone(4)).dtype == np.int8
        assert convert.UINT8.from_complex64(tone(4)).dtype == np.uint8
        assert convert.INT12.from_complex64(tone(4)).dtype == np.int16
        assert convert.INT12_PACKED.from_complex64(tone(4)).dtype == np.uint8
        assert np.abs(convert.INT12.from_complex64(tone(1000))).max() <= 2047

    def test_accepts_complex128(self):
        assert convert.INT16.from_complex64(np.array([0.5 + 0.5j])).dtype == np.int16
