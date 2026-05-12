import struct
import io as _io

from pysdrlib.base import io

import numpy as np

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


        result = io.i8_o.read(data, -1)
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

        result = io.i16_o.read(data, -1)
        print("Input:   " + _show(vals, pad=6))
        print("Hex:     0x" + _show_hex(vals, sep="   ", pad=4))
        print("1's out: " + _show(os, pad=6))
        print("2's out: " + _show(ts, pad=6))
        print("Result:  " + _show(result, pad=6))

        assert os == result.tolist()
