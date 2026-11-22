import struct
import io as _io

from pysdrlib import Formats
from pysdrlib.base import io

import numpy as np
import pytest

def _to_buff(data: bytearray):
    return _io.BytesIO(data)

def _show(arr, sep=" ", pad=4):
    return sep.join([f"{num:0{pad}d}" for num in arr])

def _show_hex(arr, sep=" ", pad=4):
    return sep.join([f"{num:0{pad}x}" for num in arr])

def _show_bin(arr, sep=" ", pad=4):
    return sep.join([f"{num:0{pad}b}" for num in arr])

class TestIO:
    def test_o8(self):
        fmt = "B"
        us = [   0,    1,  127,  128,  129,  254,  255]
        ts = [   0,    1,  127, -128, -127,   -2,   -1]
        os = [   0,    1,  127, -127, -126,   -1,   -0]

        us = struct.pack(">" + (fmt * len(us)), *us)
        data = _to_buff(us)
        vals = [v[0] for v in struct.iter_unpack(">" + fmt, us)]


        result = Formats.i8o.read(data, -1)
        print("Input:   " + _show(vals))
        print("Hex:     0x" + _show_hex(vals, sep="   ", pad=2))
        print("1's out: " + _show(os))
        print("2's out: " + _show(ts))
        print("Result:  " + _show(result))

        assert os == result.tolist()

    def test_o16(self):
        fmt = "H"
        us = [     0,      1,  32767,  32768,  32769,  65534,  65535]
        ts = [     0,      1,  32767, -32768, -32767,     -2,     -1]
        os = [     0,      1,  32767, -32767, -32766,     -1,      0]

        us = struct.pack("<" + (fmt * len(us)), *us)
        data = _to_buff(us)
        vals = [v[0] for v in struct.iter_unpack("<" + fmt, us)]

        result = Formats.i16o.read(data, -1)
        print("Input:   " + _show(vals, pad=6))
        print("Hex:     0x" + _show_hex(vals, sep="   ", pad=4))
        print("1's out: " + _show(os, pad=6))
        print("2's out: " + _show(ts, pad=6))
        print("Result:  " + _show(result, pad=6))

        assert os == result.tolist()


def _read(fmt, raw, count=-1):
    return fmt.read(_io.BytesIO(bytes(raw)), count)

class TestIntegers:
    def test_unsigned_widths(self):
        assert _read(Formats.ui8, [0, 255]).tolist() == [0, 255]
        assert _read(Formats.ui16, [0xff, 0xff, 0x00, 0x80]).tolist() == [65535, 32768]  # little endian
        assert _read(Formats.ui32, struct.pack("<2I", 1, 4294967295)).tolist() == [1, 4294967295]

    def test_twos_complement(self):
        assert _read(Formats.i8t, [0, 1, 127, 128, 255]).tolist() == [0, 1, 127, -128, -1]
        assert _read(Formats.i16t, struct.pack("<3h", -32768, -1, 32767)).tolist() == [-32768, -1, 32767]
        assert _read(Formats.i32t, struct.pack("<2i", -2**31, 2**31 - 1)).tolist() == [-2**31, 2**31 - 1]

    def test_read_count_limits_bytes_consumed(self):
        data = _io.BytesIO(struct.pack("<4h", 1, 2, 3, 4))
        assert Formats.i16t.read(data, 2).tolist() == [1, 2]
        assert Formats.i16t.read(data, -1).tolist() == [3, 4]

    def test_i4t(self):
        """Two samples per byte, first sample in the high nibble, sign-extended."""
        #            hi  lo
        raw = [0x01, 0x7f, 0x80, 0xf8, 0x00]
        assert _read(Formats.i4t, raw).tolist() == [0, 1, 7, -1, -8, 0, -1, -8, 0, 0]

    def test_i4t_count_is_samples_not_bytes(self):
        assert _read(Formats.i4t, [0x12, 0x34], 2).tolist() == [1, 2]
        assert _read(Formats.i4t, [0x12, 0x34], 4).tolist() == [1, 2, 3, 4]

    def test_i4_odd_count_rejected(self):
        for fmt in (Formats.i4t, Formats.i4o):
            with pytest.raises(ValueError):
                _read(fmt, [0x12, 0x34], 3)

    def test_sub_byte_formats_occupy_one_byte(self):
        assert Formats.i4t.bytes == Formats.i4o.bytes == 1
        assert Formats.i4t.bits == 4
        assert Formats.ci4t.bytes == 1

    def test_i4o(self):
        """One's complement: nibble 8..15 -> -7..-0"""
        raw = [0x07, 0x89, 0xf1]
        assert _read(Formats.i4o, raw).tolist() == [0, 7, -7, -6, 0, 1]

    def test_o16_matches_expected_table(self):
        us = [0, 1, 32767, 32768, 32769, 65534, 65535]
        os = [0, 1, 32767, -32767, -32766, -1, 0]
        assert _read(Formats.i16o, struct.pack("<7H", *us)).tolist() == os

    def test_ones_complement_has_no_minus_128(self):
        """Every byte pattern maps into -127..127 and is symmetric around 0."""
        out = _read(Formats.i8o, range(256)).tolist()
        assert min(out) == -127 and max(out) == 127
        assert out[:128] == list(range(128))
        assert out[128:] == list(range(-127, 1))

    def test_ones_complement_write_inverts_read(self):
        vals = list(range(-127, 128))
        buf = _io.BytesIO()
        Formats.i8o.write(buf, vals)
        assert _read(Formats.i8o, buf.getvalue()).tolist() == vals

class TestFloats:
    def test_float_reads(self):
        assert _read(Formats.f16, np.array([1.5, -2], dtype="<f2").tobytes()).tolist() == [1.5, -2]
        assert _read(Formats.f32, struct.pack("<2f", 1.5, -2)).tolist() == [1.5, -2]
        assert _read(Formats.f64, struct.pack("<2d", 1.5, -2)).tolist() == [1.5, -2]

class TestComplex:
    def test_ci4t_is_two_int4(self):
        out = _read(Formats.ci4t, [0x1f, 0x80])  # (1, -1), (-8, 0)
        assert out.dtype == np.complex64
        assert out.tolist() == [1 - 1j, -8 + 0j]

    def test_ci8t_ci16t_ci32t(self):
        assert _read(Formats.ci8t, [1, 255, 128, 3]).tolist() == [1 - 1j, -128 + 3j]
        assert _read(Formats.ci16t, struct.pack("<4h", 1, -2, 300, -300)).tolist() == [1 - 2j, 300 - 300j]
        assert _read(Formats.ci32t, struct.pack("<2i", -70000, 70000)).tolist() == [-70000 + 70000j]

    def test_unsigned_complex(self):
        """cui8 is an RTL-SDR .cu8: unsigned bytes, read as stored and centred by scale"""
        out = _read(Formats.cui8, [0, 255, 128, 127])
        assert out.dtype == np.complex64 and out.tolist() == [0 + 255j, 128 + 127j]
        assert Formats.cui8.scale(out).tolist() == pytest.approx([-1 + 1j, (0.5 + -0.5j) / 127.5])
        assert _read(Formats.cui16, struct.pack("<2H", 1, 65535)).tolist() == [1 + 65535j]

    def test_twos_complement_is_the_default(self):
        for bare, full in (("i4", "i4t"), ("i8", "i8t"), ("i16", "i16t"), ("i32", "i32t"),
                           ("ci4", "ci4t"), ("ci8", "ci8t"), ("ci16", "ci16t"), ("ci32", "ci32t")):
            assert Formats[bare] is Formats[full], bare
        assert "ci8" not in Formats.ls() and "ci8t" in Formats.ls()

    def test_float_complex(self):
        assert _read(Formats.cf16, np.array([1, 2, 3, 4], dtype="<f2").tobytes()).tolist() == [1 + 2j, 3 + 4j]
        assert _read(Formats.cf32, struct.pack("<4f", 1, 2, 3, 4)).tolist() == [1 + 2j, 3 + 4j]
        out = _read(Formats.cf64, struct.pack("<2d", 1.25, 2.5))
        assert out.dtype == np.complex128 and out.tolist() == [1.25 + 2.5j]

    def test_complex_count_is_samples(self):
        raw = struct.pack("<8f", *range(8))
        assert _read(Formats.cf32, raw, 2).tolist() == [0 + 1j, 2 + 3j]

class TestHelpers:
    def test_o2t(self):
        assert io.o2t(np.array([0, 127, 128, 254, 255], dtype=np.uint8), 8, np.int8).tolist() == [0, 127, -127, -1, 0]

    def test_t2o_inverts_o2t(self):
        for width, utype, otype in ((8, np.uint8, np.int8), (16, np.uint16, np.int16)):
            lo, hi = -(2**(width - 1)) + 1, 2**(width - 1) - 1
            vals = np.array([lo, -1, 0, 1, hi], dtype=otype)
            assert io.o2t(io.t2o(vals, width, utype), width, otype).tolist() == vals.tolist()

    def test_helpers_do_not_modify_input(self):
        src = np.array([128, 255], dtype=np.uint8)
        io.o2t(src, 8, np.int8)
        assert src.tolist() == [128, 255]
