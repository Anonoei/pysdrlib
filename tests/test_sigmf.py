"""SigMF recordings: FileSink writes them, File reads them"""
import json

import numpy as np
import pytest

import pysdrlib
from pysdrlib import File, FileSink, Formats
from pysdrlib.base.stream import Event, RxMeta
from pysdrlib.file import sigmf

def tone(n, scale=0.5):
    return (scale * np.exp(2j * np.pi * np.arange(n) / 16)).astype(np.complex64)

def meta_of(path):
    return json.loads(sigmf.paths(path)[1].read_text())

class TestFormats:
    def test_datatypes(self):
        assert Formats.cf32.sigmf == "cf32_le"
        assert Formats.ci8.sigmf == "ci8"
        assert Formats.cui8.sigmf == "cu8"
        assert Formats.i16.sigmf == "ri16_le"
        assert Formats.ci4.sigmf is None and Formats.cf16.sigmf is None and Formats.i8o.sigmf is None

    def test_get_takes_sigmf_names(self):
        assert Formats.get("cf32_le") is Formats.cf32
        assert Formats.get("cu8") is Formats.cui8
        assert Formats.get("ci16_le") is Formats.ci16t
        assert Formats.get(Formats.cf64) is Formats.cf64
        assert Formats.get("ci16_be") is Formats.ci16t_be and Formats.ci16t_be.sigmf == "ci16_be"
        assert Formats.get("rf32_be") is Formats.f32_be and Formats.get("cu16_be") is Formats.cui16_be
        with pytest.raises(KeyError, match="unknown format"):
            Formats.get("ci8_be")

    def test_every_sigmf_format_round_trips(self):
        for fmt in Formats:
            if fmt.sigmf:
                assert Formats.get(fmt.sigmf) is fmt

class TestWrite:
    def test_paths(self, tmp_path):
        for name in ("rec.sigmf", "rec.sigmf-data", "rec.sigmf-meta", "rec"):
            assert sigmf.paths(tmp_path / name) == (tmp_path / "rec.sigmf-data", tmp_path / "rec.sigmf-meta")

    def test_recording_and_its_metadata(self, tmp_path):
        x = tone(1000)
        with FileSink(tmp_path / "rec.sigmf", "ci16", rate=2e6, freq=100e6, hw="HackRF One", clip=True) as out:
            out.write(x * 32767)
        assert (tmp_path / "rec.sigmf-data").stat().st_size == 1000 * 4
        doc = meta_of(tmp_path / "rec.sigmf")
        g = doc["global"]
        assert g["core:datatype"] == "ci16_le" and g["core:sample_rate"] == 2e6 and g["core:hw"] == "HackRF One"
        assert g["core:version"] == sigmf.VERSION and g["core:recorder"].startswith("pysdrlib")
        assert doc["captures"][0]["core:sample_start"] == 0 and doc["captures"][0]["core:frequency"] == 100e6
        assert doc["captures"][0]["core:datetime"].endswith("Z")
        assert doc["annotations"] == []

    def test_sigmf_true_with_another_name(self, tmp_path):
        with FileSink(tmp_path / "rec", "cf32", sigmf=True, rate=1e6) as out:
            out.write(tone(10))
        assert (tmp_path / "rec.sigmf-data").is_file() and (tmp_path / "rec.sigmf-meta").is_file()

    def test_the_metadata_is_there_while_recording(self, tmp_path):
        out = FileSink(tmp_path / "rec.sigmf", "cf32", rate=1e6, open=True)
        assert meta_of(tmp_path / "rec.sigmf")["global"]["core:datatype"] == "cf32_le"
        out.write(tone(10))
        out.retune(5e6)
        out.write(tone(10))
        out.flush()
        assert len(meta_of(tmp_path / "rec.sigmf")["captures"]) == 2
        out.close()

    def test_retunes_are_captures(self, tmp_path):
        with FileSink(tmp_path / "rec.sigmf", "cf32", rate=1e6, freq=100e6) as out:
            out.write(tone(100))
            out.retune(100e6)   # the same frequency: no new capture
            out.retune(101e6)
            out.retune(102e6)   # before any sample at 101 MHz: replaces it
            out.write(tone(100))
            out.retune(103e6)   # nothing written after it: not recorded
        caps = meta_of(tmp_path / "rec.sigmf")["captures"]
        assert [(c["core:sample_start"], c["core:frequency"]) for c in caps] == [(0, 100e6), (100, 102e6)]

    def test_device_metadata(self, tmp_path):
        """RxMeta gives the rate and frequency, retunes inside a block, and samples lost or skipped"""
        blocks = [
            RxMeta(index=0, count=100, freq=100e6, rate=2e6),
            RxMeta(index=100, count=100, freq=100e6, rate=2e6, events=(Event(40, "freq", 101e6),)),
            RxMeta(index=250, count=100, freq=101e6, rate=2e6),                 # 50 lost (or flushed) between
            RxMeta(index=350, count=100, freq=101e6, rate=2e6, gaps=((30, 7),)),   # 7 lost inside
        ]
        with FileSink(tmp_path / "rec.sigmf", "cf32") as out:
            for m in blocks:
                out.write(tone(100), meta=m)
        doc = meta_of(tmp_path / "rec.sigmf")
        assert doc["global"]["core:sample_rate"] == 2e6
        assert [(c["core:sample_start"], c["core:frequency"]) for c in doc["captures"]] == [(0, 100e6), (140, 101e6)]
        assert [a["core:sample_start"] for a in doc["annotations"]] == [200, 330]
        assert "50 samples" in doc["annotations"][0]["core:comment"]

    def test_a_real_device(self, tmp_path):
        sdr = pysdrlib.open("sim")
        sdr.initialize(cf=100e6, Fs=1e6)
        with sdr.stream(4096, meta=True, timeout=2) as s, FileSink(tmp_path / "rec.sigmf", "cf32", hw=sdr.NAME) as out:
            for k, (x, m) in zip(range(4), s):
                out.write(x, meta=m)
                if k == 1:
                    sdr.set_rx_freq(105e6, flush=True)
        sdr.close()
        f = File(tmp_path / "rec")
        assert f.rate == 1e6 and f.freq == 100e6 and f.max_samp == 4 * 4096
        assert f.meta.freq_at(4 * 4096 - 1) == 105e6

    def test_formats_sigmf_cannot_describe(self, tmp_path):
        with pytest.raises(ValueError, match="no datatype"):
            FileSink(tmp_path / "x.sigmf", "ci4")
        with pytest.raises(ValueError, match="append"):
            FileSink(tmp_path / "x.sigmf", "cf32", append=True)

class TestRead:
    def test_round_trip(self, tmp_path):
        x = (tone(500) * 127).round()
        with FileSink(tmp_path / "rec.sigmf", "ci8", rate=1e6, freq=433.92e6) as out:
            out.write(x)
        for name in ("rec", "rec.sigmf", "rec.sigmf-meta", "rec.sigmf-data"):
            with File(tmp_path / name) as f:
                assert f.fmt is Formats.ci8t and f.rate == 1e6 and f.freq == 433.92e6
                assert np.array_equal(f.next(500), x)

    def test_written_by_another_program(self, tmp_path):
        """cu8 (rtl_sdr), captures out of order, extra fields"""
        raw = np.array([127, 128, 255, 0] * 10, np.uint8)
        (tmp_path / "rtl.sigmf-data").write_bytes(raw.tobytes())
        (tmp_path / "rtl.sigmf-meta").write_text(json.dumps({
            "global": {"core:datatype": "cu8", "core:sample_rate": 2.4e6, "core:version": "1.0.0", "x:other": 1},
            "captures": [{"core:sample_start": 10, "core:frequency": 2e6}, {"core:sample_start": 0, "core:frequency": 1e6}],
            "annotations": [{"core:sample_start": 3, "core:label": "burst"}],
        }))
        f = File(tmp_path / "rtl.sigmf", scale=True)
        assert f.fmt is Formats.cui8 and f.max_samp == 20
        assert f.meta.captures == [(0, 1e6), (10, 2e6)]
        assert f.meta.freq_at(9) == 1e6 and f.meta.freq_at(10) == 2e6
        assert f.meta.annotations[0]["core:label"] == "burst" and f.meta.raw["global"]["x:other"] == 1
        with f:
            assert np.allclose(f.next(2), [-0.5 / 127.5 + 0.5j / 127.5, 1 - 1j], atol=1e-6)

    def test_a_format_given_wins(self, tmp_path):
        with FileSink(tmp_path / "rec.sigmf", "ci16", rate=1e6) as out:
            out.write(np.zeros(8, np.complex64))
        assert File(tmp_path / "rec.sigmf", "ci8").max_samp == 16

    def test_unreadable_recordings(self, tmp_path):
        (tmp_path / "a.sigmf-data").write_bytes(b"")
        (tmp_path / "a.sigmf-meta").write_text(json.dumps({"global": {"core:datatype": "ci64_le"}}))
        with pytest.raises(ValueError, match="not supported"):
            File(tmp_path / "a.sigmf")
        (tmp_path / "a.sigmf-meta").write_text(json.dumps({"global": {"core:datatype": "cf32_le", "core:num_channels": 2}}))
        with pytest.raises(ValueError, match="channels"):
            File(tmp_path / "a.sigmf")

    def test_a_raw_file_still_needs_its_format(self, tmp_path):
        (tmp_path / "x.bin").write_bytes(b"\0" * 8)
        assert File(tmp_path / "x.bin", "cf32").meta is None
        with pytest.raises(ValueError, match="fmt"):
            File(tmp_path / "x.bin")   # (".cfile" would name its format)


def test_big_endian_recordings(tmp_path):
    x = (tone(300) * 30000).round()
    with FileSink(tmp_path / "be.sigmf", "ci16_be", rate=1e6) as out:
        out.write(x)
    assert meta_of(tmp_path / "be.sigmf")["global"]["core:datatype"] == "ci16_be"
    raw = np.fromfile(tmp_path / "be.sigmf-data", ">i2")
    assert np.array_equal(raw[0::2], x.real) and np.array_equal(raw[1::2], x.imag)
    with File(tmp_path / "be") as f:
        assert f.fmt is Formats.ci16t_be and np.array_equal(f.next(300), x)


class TestRxContext:
    """How a channel receives (Device.get_rx_context) travels with a recording as pysdrlib:rx"""

    @staticmethod
    def lnb():
        from pysdrlib.base import caps
        return caps.Chain(lo=5.15e9, invert=True, name="C-band LNB")

    def test_a_device_says_its_chain_and_reference(self):
        d = pysdrlib.device("sim", realtime=False)
        d.open()
        try:
            d.set_chains([self.lnb()])
            d.initialize(cf=3.9e9, Fs=1e6)
            d.set_freq_correction(12.5)
            c = d.get_rx_context()
            assert c.freq == 3.9e9 and c.rate == 1e6 and c.inverted and c.chain.lo == 5.15e9 and c.ppm == 12.5
            assert c.chain.to_if(c.freq) == 1.25e9 # the radio tunes the IF
            d.set_chains(None)
            assert not d.get_rx_context().inverted and d.get_rx_context().chain is None
        finally:
            d.close()

    def test_the_context_travels_with_the_recording(self, tmp_path):
        ctx = pysdrlib.RxContext(freq=3.9e9, rate=1e6, chain=self.lnb(), ppm=14.8, clock_source="external",
                                 gains={"LNA": 20}, port="RX2", hw="X310 31BE973")
        with FileSink(tmp_path / "rec.sigmf", "cf32", context=ctx) as s:
            s.write(tone(100))
        m = sigmf.read(tmp_path / "rec.sigmf")
        assert m.context == ctx and m.rate == 1e6 and m.freq == 3.9e9 # (rate and freq from the context)
        g = meta_of(tmp_path / "rec.sigmf")["global"]
        assert {"name": "pysdrlib", "version": sigmf.EXTENSION_VERSION, "optional": True} in g["core:extensions"]
        assert g["core:hw"] == "X310 31BE973"
        assert File(tmp_path / "rec.sigmf").meta.context.inverted
        from pysdrlib.file.play import describe
        with File(tmp_path / "rec.sigmf") as f:
            line = next(l for l in describe(f, 1e6) if "received" in l)
        assert "C-band LNB (LO 5.15 GHz, inverted)" in line and "port RX2" in line and "+14.8 ppm" in line

    def test_numpy_numbers_in_the_context_are_written(self, tmp_path):
        # (a gain split across stages, or a frequency given as numpy, is a numpy scalar)
        ctx = pysdrlib.RxContext(freq=np.float32(1e9), rate=np.float64(1e6), ppm=np.float32(1.5),
                                 ppm_accuracy=np.float32(0.25), gains={"LNA": np.float32(10.0), "VGA": np.int64(20)})
        with FileSink(tmp_path / "rec.sigmf", "cf32", context=ctx) as s:
            s.write(tone(10))
        c = sigmf.read(tmp_path / "rec.sigmf").context
        assert c.freq == 1e9 and c.gains == {"LNA": 10.0, "VGA": 20.0} and c.ppm_accuracy == 0.25
        sigmf.write(tmp_path / "x.sigmf", "cf32", np.float32(1e6), extra={"core:hw": "x", "y:n": np.int32(3)})
        assert meta_of(tmp_path / "x.sigmf")["global"]["y:n"] == 3

    def test_the_level_calibration_and_feeds_travel_with_each_channel(self, tmp_path):
        """A two-feed capture: two recordings, one per channel, each with its channel's chain (its feed's polarization)
        and level calibration"""
        from pysdrlib.base import caps
        from pysdrlib.file.play import describe
        feeds = [caps.Chain(lo=9.75e9, channel=k, polarization=p, name=f"LNB {p}") for k, p in enumerate("HV")]
        d = pysdrlib.device("sim", realtime=False, channels=2)
        d.open()
        try:
            d.set_chains(feeds)
            d.initialize(cf=11.7e9, Fs=1e6)
            d.set_level_calibration(-72.5, accuracy=0.3, channel=0)
            d.set_level_calibration(-71.0, channel=1)
            d.start_rx(channels=(0, 1))
            x = d.read(4096, channel=(0, 1))
            ctx = [d.get_rx_context(channel=k) for k in (0, 1)]
        finally:
            d.close()
        assert [c.chain.polarization for c in ctx] == ["H", "V"]
        assert (ctx[0].level_offset, ctx[0].level_accuracy, ctx[1].level_offset, ctx[1].level_accuracy) == (-72.5, 0.3, -71.0, None)
        for k in (0, 1):
            with FileSink(tmp_path / f"ch{k}.sigmf", "cf32", context=ctx[k]) as s:
                s.write(x[k])
            assert sigmf.read(tmp_path / f"ch{k}.sigmf").context == ctx[k]
        with File(tmp_path / "ch0.sigmf") as f:
            line = next(l for l in describe(f, 1e6) if "received" in l)
        assert "LNB H (LO 9.75 GHz, H)" in line and "level dBm = dBFS -72.5 +-0.3 dB" in line
        old = pysdrlib.RxContext.from_json({"freq": 1.0, "chain": {"lo": 9.75e9}})   # (a 1.1.0 recording: not said)
        assert old.level_offset is None and old.level_accuracy is None and old.chain.polarization is None

    def test_the_extension_is_declared_once(self, tmp_path):
        ext = {"name": "pysdrlib", "version": "0.9.0", "optional": True}
        sigmf.write(tmp_path / "x.sigmf", "cf32", 1e6, extra={"core:extensions": [ext, {"name": "other", "version": "1"}]},
                    context=pysdrlib.RxContext(freq=1e9))
        names = [e["name"] for e in meta_of(tmp_path / "x.sigmf")["global"]["core:extensions"]]
        assert sorted(names) == ["other", "pysdrlib"]

    def test_a_recording_without_it_says_none(self, tmp_path):
        with FileSink(tmp_path / "plain.sigmf", "cf32", rate=1e6, freq=1e9) as s:
            s.write(tone(10))
        assert sigmf.read(tmp_path / "plain.sigmf").context is None

    def test_a_replay_gives_the_recordings_context(self, tmp_path):
        ctx = pysdrlib.RxContext(freq=3.9e9, rate=1e6, chain=self.lnb())
        with FileSink(tmp_path / "rec.sigmf", "cf32", context=ctx) as s:
            s.write(tone(4096))
        d = pysdrlib.open("file", path=str(tmp_path / "rec.sigmf"), realtime=False)
        try:
            assert d.get_rx_freq() == 3.9e9   # the recording's, before it plays
            c = d.get_rx_context()
            assert c.inverted and c.chain.lo == 5.15e9 and c.freq == 3.9e9
        finally:
            d.close()

    def test_samples_put_right_after_the_radio_are_not_mirrored(self):
        import dataclasses
        ctx = pysdrlib.RxContext(freq=3.9e9, rate=1e6, chain=self.lnb())
        done = dataclasses.replace(ctx, undone=True)
        assert ctx.inverted and not done.inverted and done.chain.invert # (the chain is what it is)
        assert pysdrlib.RxContext.from_json(done.json()) == done

    def test_how_well_the_reference_and_lo_are_known_travels_too(self, tmp_path):
        from pysdrlib.base import caps
        chain = caps.Chain(lo=9.7503e9, lo_accuracy=2e3, name="measured LNB")
        d = pysdrlib.device("sim", realtime=False)
        d.open()
        try:
            d.set_chains([chain])
            d.initialize(cf=11.7e9, Fs=1e6)
            d.set_freq_correction(-14.2, accuracy=0.3)
            assert d.get_freq_correction_accuracy() == 0.3
            ctx = d.get_rx_context()
        finally:
            d.close()
        assert ctx.ppm == -14.2 and ctx.ppm_accuracy == 0.3 and ctx.chain.lo_accuracy == 2e3
        with FileSink(tmp_path / "cal.sigmf", "cf32", context=ctx) as s:
            s.write(tone(10))
        back = sigmf.read(tmp_path / "cal.sigmf").context
        assert back == ctx and meta_of(tmp_path / "cal.sigmf")["global"]["core:extensions"][0]["version"] == sigmf.EXTENSION_VERSION
        assert pysdrlib.RxContext.from_json({"freq": 1.0}).ppm_accuracy is None # (a 1.0.0 recording: not known)
