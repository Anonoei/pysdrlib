"""Tests for pysdrlib.file.File"""
import numpy as np
import pytest

from pysdrlib import err
from pysdrlib.base import Formats
from pysdrlib.file import File
from pysdrlib.file import err as file_err

N = 100


def make_file(tmp_path, fmt="i16t", values=None):
    values = list(range(N)) if values is None else values
    path = tmp_path / "samples.bin"
    with open(path, "wb") as f:
        Formats[fmt].write(f, values)
    return path


@pytest.fixture
def f(tmp_path):
    """File of int16 samples 0..99, opened."""
    file = File()
    file.set_path(make_file(tmp_path))
    file.set_fmt("i16t")
    file.open()
    yield file
    if file.f is not None:
        file.close()


def ints(arr):
    return arr.astype(np.int64).tolist()


class TestSetup:
    def test_max_samp_from_size_and_format(self, tmp_path):
        file = File()
        file.set_path(make_file(tmp_path, "i16t"))
        file.set_fmt("i16t")
        assert file.max_samp == N

    def test_set_fmt_before_set_path(self, tmp_path):
        file = File()
        file.set_fmt("f32")
        file.set_path(make_file(tmp_path, "f32"))
        assert file.max_samp == N

    def test_max_samp_follows_format_changes(self, tmp_path):
        file = File()
        file.set_path(make_file(tmp_path, "i16t"))
        file.set_fmt("i16t")
        file.set_fmt("i8t")
        assert file.max_samp == 2 * N

    def test_complex_and_4bit_use_whole_bytes(self, tmp_path):
        file = File()
        file.set_path(make_file(tmp_path, "ci8t", [1 + 2j] * 10))
        file.set_fmt("ci8t")
        assert file.max_samp == 10  # one byte per I/Q pair
        file.set_fmt("i4t")         # 4-bit: smallest unit is a byte, no divide by zero
        assert file.max_samp == 10

    def test_percent_without_path(self):
        assert File().percent() == 0.0


class TestForward:
    def test_next_reads_in_order(self, f):
        assert ints(f.next(10)) == list(range(10))
        assert ints(f.next(5)) == list(range(10, 15))

    def test_cur_samp_counts_each_sample_once(self, f):
        f.next(10)
        assert f.cur_samp == 10
        f.next(5)
        assert f.cur_samp == 15

    def test_percent(self, f):
        f.next(25)
        assert f.percent() == 0.25

    def test_get_samples_uses_count(self, f):
        f.set_sample_count(7)
        assert ints(f.get_samples()) == list(range(7))
        assert ints(f.get_samples()) == list(range(7, 14))

    def test_read_exactly_to_end(self, f):
        f.next(N)
        assert f.cur_samp == N
        assert f.percent() == 1.0

    def test_overflow(self, f):
        f.next(95)
        with pytest.raises(file_err.Overflow):
            f.next(6)
        assert f.cur_samp == 95  # a failed read doesn't move

    def test_underflow_on_truncated_file(self, tmp_path):
        path = make_file(tmp_path, "i8t", list(range(10)))
        file = File()
        file.set_path(path)
        file.set_fmt("i8t")
        file.max_samp = 20  # lie about the size
        file.open()
        with pytest.raises(file_err.Underflow):
            file.next(15)
        file.close()

    def test_forward_yields_full_chunks(self, f):
        chunks = list(f.forward(30))
        assert [ints(c) for c in chunks] == [list(range(0, 30)), list(range(30, 60)), list(range(60, 90))]
        assert f.cur_samp == 90

    def test_call_is_forward(self, f):
        assert len(list(f(25))) == 4


class TestBackward:
    def test_prev_returns_samples_before_position(self, f):
        f.next(20)
        assert ints(f.prev(5)) == list(range(15, 20))
        assert f.cur_samp == 15

    def test_prev_then_next_is_consistent(self, f):
        f.next(20)
        f.prev(5)                       # now at 15
        assert ints(f.next(5)) == list(range(15, 20))
        assert f.cur_samp == 20

    def test_prev_leaves_file_position_at_cur_samp(self, f):
        f.next(30)
        f.prev(10)
        assert f.f.tell() == f.cur_samp * f.fmt.bytes

    def test_prev_before_start(self, f):
        f.next(5)
        with pytest.raises(file_err.Overflow):
            f.prev(6)
        assert f.cur_samp == 5

    def test_reverse_walks_back_to_start(self, f):
        f.next(N)
        chunks = list(f.reverse(40))
        assert [ints(c) for c in chunks] == [list(range(60, 100)), list(range(20, 60))]
        assert f.cur_samp == 20

    def test_forward_then_reverse_round_trip(self, f):
        forward = np.concatenate(list(f.forward(25)))
        backward = np.concatenate(list(f.reverse(25))[::-1])
        np.testing.assert_array_equal(forward, backward)


class TestPosition:
    def test_reset(self, f):
        f.next(40)
        f.reset()
        assert f.cur_samp == 0
        assert ints(f.next(3)) == [0, 1, 2]

    def test_seek(self, f):
        f.seek(50)
        assert f.cur_samp == 50
        assert ints(f.next(3)) == [50, 51, 52]

    def test_seek_out_of_range(self, f):
        for bad in (-1, N + 1):
            with pytest.raises(file_err.Overflow):
                f.seek(bad)

    @pytest.mark.parametrize("fmt,values", [
        ("i8t", list(range(N))),
        ("i16t", list(range(N))),
        ("i32t", list(range(N))),
        ("f32", [float(i) for i in range(N)]),
        ("f64", [float(i) for i in range(N)]),
        ("ci8t", [complex(i % 8, -(i % 8)) for i in range(N)]),
        ("ci16t", [complex(i, -i) for i in range(N)]),
        ("cf64", [complex(i, -i) for i in range(N)]),
    ])
    def test_open_resumes_at_cur_samp_for_any_format(self, tmp_path, fmt, values):
        """Opening at a non-zero sample seeks to sample * bytes, not sample / bytes."""
        file = File()
        file.set_path(make_file(tmp_path, fmt, values))
        file.set_fmt(fmt)
        file.cur_samp = 40
        file.open()
        out = file.next(4)
        file.close()
        np.testing.assert_allclose(out, np.array(values[40:44]))
        assert file.cur_samp == 44

    @pytest.mark.parametrize("fmt", ["i16t", "cf64", "ci8t"])
    def test_position_invariant(self, tmp_path, fmt):
        values = [complex(i % 8, 1) if fmt == "ci8t" else (i if fmt == "i16t" else complex(i, 1)) for i in range(N)]
        file = File()
        file.set_path(make_file(tmp_path, fmt, values))
        file.set_fmt(fmt)
        file.open()
        for step in (file.next, file.next, file.prev, file.next):
            step(7)
            assert file.f.tell() == file.cur_samp * file.fmt.bytes
        file.close()


class TestOpenClose:
    def test_close_then_reopen(self, f):
        f.next(10)
        f.close()
        assert f.f is None
        f.open()
        assert ints(f.next(5)) == list(range(10, 15))  # resumes where it left off

    def test_double_open_and_close_do_not_break(self, f):
        f.open()   # warns, keeps the handle
        f.close()
        f.close()  # warns, no error
        assert f.f is None

    def test_context_manager(self, tmp_path):
        file = File()
        file.set_path(make_file(tmp_path))
        file.set_fmt("i16t")
        with file as opened:
            assert ints(opened.next(3)) == [0, 1, 2]
        assert file.f is None

    def test_reading_when_closed_raises_not_open(self, tmp_path):
        file = File()
        file.set_path(make_file(tmp_path))
        file.set_fmt("i16t")
        with pytest.raises(err.NotOpen):
            file.next(1)
        with pytest.raises(err.NotOpen):
            file.prev(1)


class TestConvenience:
    def test_constructor_args(self, tmp_path):
        file = File(make_file(tmp_path), "i16t")
        assert file.max_samp == N and file.fmt is Formats.i16t

    def test_next_partial_returns_short_last_block(self, f):
        f.seek(N - 3)
        assert ints(f.next(10, partial=True)) == [N - 3, N - 2, N - 1]
        assert len(f.next(10, partial=True)) == 0
        with pytest.raises(file_err.Overflow):
            f.next(10)

    def test_forward_partial_yields_tail(self, f):
        sizes = [len(b) for b in f.forward(30, partial=True)]
        assert sizes == [30, 30, 30, 10]

    def test_forward_default_skips_tail(self, f):
        assert [len(b) for b in f.forward(30)] == [30, 30, 30]


class TestFileSink:
    def test_round_trip(self, tmp_path):
        from pysdrlib.file import FileSink
        path = tmp_path / "out.bin"
        with FileSink(path, "i16t") as s:
            s.write(np.arange(10))
            s.write(np.arange(10, 20))
            assert s.count == 20
        with File(path, "i16t") as r:
            assert ints(r.next(20)) == list(range(20))

    def test_complex_round_trip(self, tmp_path):
        from pysdrlib.file import FileSink
        path = tmp_path / "out.cf32"
        x = (np.arange(8) + 1j * np.arange(8)).astype(np.complex64)
        with FileSink(path, "cf32") as s:
            s.write(x)
        with File(path, "cf32") as r:
            assert np.array_equal(r.next(8), x)

    def test_truncates_unless_append(self, tmp_path):
        from pysdrlib.file import FileSink
        path = tmp_path / "out.bin"
        for append, expect in ((False, 5), (True, 10), (False, 5)):
            with FileSink(path, "i16t", append=append) as s:
                s.write(np.arange(5))
            assert File(path, "i16t").max_samp == expect

    def test_write_requires_open(self, tmp_path):
        from pysdrlib.file import FileSink
        with pytest.raises(err.NotOpen):
            FileSink(tmp_path / "x.bin", "i16t").write([1])


def test_percent_as_0_100(f):
    f.next(25)
    assert f.percent() == 0.25
    assert f.percent(fraction=False) == 25.0


def test_sink_open_on_construction_and_top_level_export(tmp_path):
    import pysdrlib
    s = pysdrlib.FileSink(tmp_path / "o.bin", "i16t", open=True)
    s.write([1, 2, 3])
    s.close()
    assert File(tmp_path / "o.bin", "i16t").max_samp == 3


class TestClip:
    def test_clips_and_warns_once_on_close(self, tmp_path):
        import logging
        from pysdrlib.file import FileSink
        path = tmp_path / "c.bin"
        x = np.array([1 + 2j, 40000 - 50000j, -3 + 0j, 0 + 33000j], dtype=np.complex64)
        s = FileSink(path, "ci32t", clip=True, open=True)
        records = []  # pysdrlib loggers are detached from the root logger, so listen on the sink's own
        class Grab(logging.Handler):
            def emit(self, record):
                records.append(record)
        s.log.addHandler(Grab(logging.WARNING))
        s.write(x)
        s.write(x[:1])
        assert not records  # nothing logged until close
        s.close()
        assert s.clipped == 2
        assert [r.getMessage() for r in records if "clipped" in r.getMessage()] == \
            [f"2 samples clipped writing {path}"]
        with File(path, "ci32t") as r:
            out = r.next(5)
        assert out.dtype == np.complex64
        assert out[1] == 32767 - 32768j and out[3] == 0 + 32767j and out[0] == 1 + 2j

    def test_real_integer_and_float_formats(self, tmp_path):
        from pysdrlib.file import FileSink
        with FileSink(tmp_path / "a.bin", "i8t", clip=True) as s:
            s.write([-500, 0, 500])
            assert s.clipped == 2
        with File(tmp_path / "a.bin", "i8t") as r:
            assert ints(r.next(3)) == [-128, 0, 127]
        with FileSink(tmp_path / "b.bin", "f32", clip=True) as s:
            s.write([1e30])
            assert s.clipped == 0


class TestRangeChecks:
    def test_plain_integer_formats_raise_instead_of_wrapping(self, tmp_path):
        from pysdrlib.file import FileSink
        for fmt, bad in (("i16t", [40000]), ("ci32t", np.array([40000 + 0j], dtype=np.complex64)),
                         ("ui8", [300]), ("i16t", [np.nan]), ("i16t", [np.inf])):
            with FileSink(tmp_path / "w.bin", fmt) as s:
                with pytest.raises(ValueError):
                    s.write(bad)

    def test_in_range_and_float_formats_unchanged(self, tmp_path):
        from pysdrlib.file import FileSink
        with FileSink(tmp_path / "w.bin", "i16t") as s:
            s.write([-32768, 32767, 3.4])
        with FileSink(tmp_path / "f.bin", "f32") as s:
            s.write([np.nan, np.inf, 1e30])
        with FileSink(tmp_path / "h.bin", "f16") as s:
            s.write([np.nan, np.inf, 1.0])
            with pytest.raises(ValueError):
                s.write([70000.0])

    def test_clip_counts_nonfinite_and_stores_zero(self, tmp_path):
        from pysdrlib.file import FileSink
        with FileSink(tmp_path / "n.bin", "i16t", clip=True) as s:
            s.write(np.array([np.nan, np.inf, -np.inf, 3], dtype=np.float32))
            assert s.clipped == 3
        with File(tmp_path / "n.bin", "i16t") as r:
            assert ints(r.next(4)) == [0, 32767, -32768, 3]

    def test_rejects_non_1d(self, tmp_path):
        from pysdrlib.file import FileSink
        with FileSink(tmp_path / "m.bin", "i16t") as s:
            with pytest.raises(ValueError):
                s.write(np.zeros((2, 2)))
            assert s.count == 0


class TestNits:
    def test_failed_write_leaves_counters(self, tmp_path):
        from pysdrlib.file import FileSink
        with FileSink(tmp_path / "o.bin", "i4o", clip=True) as s:
            with pytest.raises(ValueError):
                s.write([100, 0, 1])  # odd count: nibble packer raises
            assert s.clipped == 0 and s.count == 0

    def test_error_text(self, tmp_path):
        from pysdrlib.file import FileSink
        with pytest.raises(ValueError, match=r"^i16t: values must be within \[-32768, 32767\]$"):
            Formats.i16t.write(open(tmp_path / "a", "wb"), [40000])
        with FileSink(tmp_path / "b", "i16t") as s:
            with pytest.raises(ValueError, match=r"^i16t: .*clip=True"):
                s.write([40000])
