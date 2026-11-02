"""Tests for the Formats enum and its functionality."""
import io as _io

import numpy as np
import pytest

from pysdrlib.base import formats, io


def _to_buff(data: bytes):
    """Helper to create a BytesIO from bytes."""
    return _io.BytesIO(data)


class TestFormats:
    """Test the Formats enum and its functionality."""

    def test_formats_enum_exists(self):
        """Test that all expected formats are defined."""
        # Check integer formats
        assert hasattr(formats.Formats, 'ui8')
        assert hasattr(formats.Formats, 'ui16')
        assert hasattr(formats.Formats, 'ui32')
        assert hasattr(formats.Formats, 'i4t')
        assert hasattr(formats.Formats, 'i8t')
        assert hasattr(formats.Formats, 'i16t')
        assert hasattr(formats.Formats, 'i32t')
        assert hasattr(formats.Formats, 'i4o')
        assert hasattr(formats.Formats, 'i8o')
        assert hasattr(formats.Formats, 'i16o')

        # Check float formats
        assert hasattr(formats.Formats, 'f16')
        assert hasattr(formats.Formats, 'f32')
        assert hasattr(formats.Formats, 'f64')

        # Check complex formats
        assert hasattr(formats.Formats, 'ci8t')
        assert hasattr(formats.Formats, 'ci16t')
        assert hasattr(formats.Formats, 'ci32t')
        assert hasattr(formats.Formats, 'cf32')
        assert hasattr(formats.Formats, 'cf64')
        assert hasattr(formats.Formats, 'cf128')

    def test_formats_get(self):
        """Test getting formats by name."""
        ui8 = formats.Formats.get('ui8')
        assert ui8 == formats.Formats.ui8

        f32 = formats.Formats.get('f32')
        assert f32 == formats.Formats.f32

        # Test invalid format
        try:
            formats.Formats.get('invalid')
            assert False, "Should raise KeyError"
        except KeyError:
            pass

    def test_formats_ls(self):
        """Test listing all formats."""
        fmts = formats.Formats.ls()
        assert len(fmts) > 0
        assert 'ui8' in fmts
        assert 'f32' in fmts
        assert 'cf32' in fmts

    def test_formats_bits(self):
        """Test getting bits property."""
        ui8 = formats.Formats.ui8
        assert ui8.bits == 8

        ui16 = formats.Formats.ui16
        assert ui16.bits == 16

        f32 = formats.Formats.f32
        assert f32.bits == 32

        cf64 = formats.Formats.cf64
        assert cf64.bits == 64

    def test_formats_bytes(self):
        """Test getting bytes property."""
        ui8 = formats.Formats.ui8
        assert ui8.bytes == 1

        ui16 = formats.Formats.ui16
        assert ui16.bytes == 2

        f32 = formats.Formats.f32
        assert f32.bytes == 4

        cf64 = formats.Formats.cf64
        assert cf64.bytes == 8

    def test_formats_is_complex(self):
        """Test is_complex property."""
        # Non-complex formats
        assert not formats.Formats.ui8.is_complex
        assert not formats.Formats.f32.is_complex

        # Complex formats
        assert formats.Formats.cf32.is_complex
        assert formats.Formats.cf64.is_complex
        assert formats.Formats.ci16t.is_complex

    def test_formats_read_write(self):
        """Test read/write methods."""
        # Test writing and reading uint8
        data = _io.BytesIO()
        test_values = [0, 1, 127, 128, 255]

        formats.Formats.ui8.write(data, test_values)
        data.seek(0)

        result = formats.Formats.ui8.read(data, len(test_values))
        assert result.tolist() == test_values

        # Test writing and reading float32
        data = _io.BytesIO()
        test_values = [0.0, 1.5, 3.14, -2.7]

        formats.Formats.f32.write(data, test_values)
        data.seek(0)

        result = formats.Formats.f32.read(data, len(test_values))
        for expected, actual in zip(test_values, result):
            # Use approximate comparison for floats
            assert abs(expected - actual) < 1e-6

    def test_formats_write_complex(self):
        """Test writing and reading complex formats."""
        # Test complex float32
        data = _io.BytesIO()
        test_values = [1+2j, 3+4j, -5-6j]

        formats.Formats.cf32.write(data, test_values)
        data.seek(0)

        result = formats.Formats.cf32.read(data, len(test_values))
        for expected, actual in zip(test_values, result):
            # Use approximate comparison for complex numbers
            assert abs(expected - actual) < 1e-6

    def test_formats_sizes(self):
        """Test that format sizes match their enum values."""
        # Check that SIZE attribute is set correctly
        assert io.ui8_t.SIZE == 8
        assert io.ui16_t.SIZE == 16
        assert io.ui32_t.SIZE == 32
        assert io.i8_t.SIZE == 8
        assert io.i16_t.SIZE == 16
        assert io.i32_t.SIZE == 32

        assert io.f16.SIZE == 16
        assert io.f32.SIZE == 32
        assert io.f64.SIZE == 64

        assert io.i4_t.SIZE == io.i4_o.SIZE == 4
        assert io.i8_o.SIZE == 8
        assert io.i16_o.SIZE == 16
        assert io.ci8_t.SIZE == 8
        assert io.ci16_t.SIZE == 16
        assert io.ci32_t.SIZE == 32

        assert io.cf32.SIZE == 32
        assert io.cf64.SIZE == 64
        assert io.cf128.SIZE == 128

    def test_enum_matches_io_sizes(self):
        """Every enum member's bits come from its class, and bytes is bits // 8."""
        for fmt in formats.Formats:
            assert fmt.bits == fmt.value.SIZE
            assert fmt.bytes == max(1, fmt.bits // 8)  # 4-bit I/Q components: one pair per byte

    def test_complex_is_two_components(self):
        """A complex format is two of its base format (e.g. cf64 = 2 x f32)."""
        for fmt in formats.Formats:
            if fmt.is_complex:
                assert fmt.bits == 2 * fmt.value.BASE.SIZE, fmt.name

    def test_ls_matches_enum(self):
        assert formats.Formats.ls() == [f.name for f in formats.Formats]
        assert len(set(formats.Formats.ls())) == len(formats.Formats)  # no aliased members


# name -> (values that must survive a write/read round trip, expected dtype kind)
ROUND_TRIP = {
    "ui8":   ([0, 1, 127, 128, 255], "u"),
    "ui16":  ([0, 1, 32768, 65535], "u"),
    "ui32":  ([0, 1, 2**31, 2**32 - 1], "u"),
    "i4t":   ([-8, -1, 0, 7], "i"),
    "i8t":   ([-128, -1, 0, 1, 127], "i"),
    "i16t":  ([-32768, -1, 0, 1, 32767], "i"),
    "i32t":  ([-2**31, -1, 0, 1, 2**31 - 1], "i"),
    "i4o":   ([-7, -1, 0, 7], "i"),
    "i8o":   ([-127, -1, 0, 1, 127], "i"),
    "i16o":  ([-32767, -1, 0, 1, 32767], "i"),
    "f16":   ([0.0, 1.5, -2.25, 100.0], "f"),
    "f32":   ([0.0, 1.5, 3.14, -2.7, 1e10], "f"),
    "f64":   ([0.0, 1.5, 3.141592653589793, -2.7e-300], "f"),
    "ci8t":  ([1 + 2j, -8 - 7j, 7 + 0j, 0 - 1j], "c"),
    "ci16t": ([1 + 2j, -128 - 127j, 127 + 0j], "c"),
    "ci32t": ([1 + 2j, -32768 - 1j, 32767 + 0j], "c"),
    "cf32":  ([1 + 2j, 3 + 4j, -5 - 6j], "c"),
    "cf64":  ([1.5 + 2.5j, 3.14 - 4j, -5e10 + 6j], "c"),
    "cf128": ([1.5 + 2.5j, 3.141592653589793 - 4j], "c"),
}


class TestRoundTrip:
    def test_every_format_is_covered(self):
        assert set(ROUND_TRIP) == set(formats.Formats.ls())

    @staticmethod
    def _tol(name):
        return {"f16": 1e-3, "f32": 1e-6, "cf64": 1e-6}.get(name, 0)

    def _roundtrip(self, name, values, count=None):
        fmt = formats.Formats[name]
        buf = _io.BytesIO()
        fmt.write(buf, values)
        raw = buf.getvalue()
        buf.seek(0)
        return fmt, raw, fmt.read(buf, len(values) if count is None else count)

    def test_roundtrip(self):
        for name, (values, kind) in ROUND_TRIP.items():
            fmt, _, result = self._roundtrip(name, values)
            assert result.dtype.kind == kind, name
            assert len(result) == len(values), name
            tol = self._tol(name)
            for expected, actual in zip(values, result):
                assert abs(expected - actual) <= tol * max(1, abs(expected)), (name, expected, actual)

    def test_written_size(self):
        """Bytes written == samples * bits / 8 (4-bit packs two per byte, always a whole number)."""
        for name, (values, _) in ROUND_TRIP.items():
            fmt, raw, _ = self._roundtrip(name, values)
            assert len(raw) * 8 == len(values) * fmt.bits, name

    def test_read_all_with_negative_count(self):
        for name, (values, _) in ROUND_TRIP.items():
            fmt, raw, _ = self._roundtrip(name, values)
            result = fmt.read(_io.BytesIO(raw), -1)
            assert len(result) == len(values), name

    def test_read_partial_count(self):
        for name, (values, _) in ROUND_TRIP.items():
            fmt, raw, _ = self._roundtrip(name, values)
            result = fmt.read(_io.BytesIO(raw), 2)
            assert len(result) == 2, name

    def test_sequential_reads_continue(self):
        """Two reads from one stream return consecutive samples."""
        values = [1, 2, 3, 4, 5, 6]
        for name in ("ui8", "i16t", "f32", "i4t", "i4o", "i8o"):
            buf = _io.BytesIO()
            formats.Formats[name].write(buf, values)
            buf.seek(0)
            fmt = formats.Formats[name]
            assert fmt.read(buf, 2).tolist() == values[:2], name  # even: stays byte aligned for 4-bit
            assert fmt.read(buf, 4).tolist() == values[2:], name

    def test_4bit_requires_whole_iq_pairs(self):
        """4-bit samples are I/Q components (one pair per byte): odd counts are an error,
        never a silently dropped nibble."""
        for name in ("i4t", "i4o"):
            fmt = formats.Formats[name]
            with pytest.raises(ValueError):
                fmt.write(_io.BytesIO(), [1, 2, 3])
            with pytest.raises(ValueError):
                fmt.read(_io.BytesIO(b"\x12\x34"), 3)

    def test_ci8t_reads_any_sample_count_without_losing_data(self):
        """Complex int8 (two int4) is byte aligned, so any sample count works and streams cleanly."""
        buf = _io.BytesIO()
        iq = [1 + 2j, 3 - 4j, -5 + 6j, 7 - 8j, -1 + 0j]
        formats.Formats.ci8t.write(buf, iq)
        assert len(buf.getvalue()) == len(iq)  # 1 byte per complex sample
        buf.seek(0)
        assert formats.Formats.ci8t.read(buf, 3).tolist() == iq[:3]
        assert formats.Formats.ci8t.read(buf, 2).tolist() == iq[3:]

    def test_4bit_bytes_is_one(self):
        assert formats.Formats.i4t.bytes == formats.Formats.i4o.bytes == formats.Formats.ci8t.bytes == 1
        assert formats.Formats.i4t.bits == 4

    def test_accepts_numpy_and_lists(self):
        for data in ([1, 2, 3], (1, 2, 3), np.array([1, 2, 3], dtype=np.int8)):
            buf = _io.BytesIO()
            formats.Formats.i8t.write(buf, data)
            assert buf.getvalue() == bytes([1, 2, 3])

    def test_little_endian_on_disk(self):
        buf = _io.BytesIO()
        formats.Formats.ui16.write(buf, [0x0102])
        assert buf.getvalue() == b"\x02\x01"
        buf = _io.BytesIO()
        formats.Formats.f32.write(buf, [1.0])
        assert buf.getvalue() == b"\x00\x00\x80\x3f"

    def test_complex_is_interleaved_iq(self):
        buf = _io.BytesIO()
        formats.Formats.ci16t.write(buf, [1 + 2j, 3 + 4j])
        assert buf.getvalue() == bytes([1, 2, 3, 4])

    def test_complex_read_dtypes(self):
        """Complex reads give complex64, except cf128 which keeps double precision."""
        for name, (values, _) in ROUND_TRIP.items():
            if name.startswith(("ci", "cf")):
                _, _, result = self._roundtrip(name, values)
                assert result.dtype == (np.complex128 if name == "cf128" else np.complex64), name

    def test_integer_formats_round_floats(self):
        buf = _io.BytesIO()
        formats.Formats.i8t.write(buf, [1.4, 1.6, -1.6])
        assert list(buf.getvalue()) == [1, 2, 254]  # 1, 2, -2

    def test_4bit_out_of_range_rejected(self):
        for name, bad in (("i4t", [8]), ("i4t", [-9]), ("i4o", [-8]), ("i8o", [-128]), ("i16o", [-32768])):
            with pytest.raises(ValueError):
                formats.Formats[name].write(_io.BytesIO(), bad)

    def test_empty(self):
        for name in ROUND_TRIP:
            buf = _io.BytesIO()
            formats.Formats[name].write(buf, [])
            assert buf.getvalue() == b""
            assert len(formats.Formats[name].read(_io.BytesIO(b""), -1)) == 0
