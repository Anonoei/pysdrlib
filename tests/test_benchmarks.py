"""Performance guard rails for pysdrlib's own overhead.

Deselected by default; run with:   pytest -m benchmark
(or `python tests/bench/run.py --check` to compare against this machine's recorded baseline).

These assert properties that hold on any machine: overhead ratios against bare numpy, and real-time
headroom relative to the HackRF's 20 MS/s maximum. Limits are several times looser than what is
measured today, so they trip on a real regression, not on a noisy runner.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "bench"))
import suite  # noqa: E402

pytestmark = pytest.mark.benchmark

HW = os.environ.get("PYSDRLIB_HW") == "1"


@pytest.fixture(scope="module")
def results():
    return suite.run(quick=True, hardware=HW)["results"]


def test_everything_ran(results):
    for name in ("buffer.update", "convert.int8", "sim.pump_16_blocks", "sim.stream_20MS.dropped"):
        assert name in results, name


# --- overhead vs bare numpy -------------------------------------------------- #
@pytest.mark.parametrize("name,limit", [
    ("buffer.update", 4),          # ring append is a copy
    ("buffer.update_read", 4),     # append + drain is two copies
    ("convert.int8", 4),           # one pass int8 -> complex64
    ("convert.int16", 4),
    ("convert.int12", 4),
    ("hackrf.rx_callback", 5),     # native convert + queue, vs numpy converting alone
    ("sim.pump_16_blocks", 6),     # whole generic device path vs conversion alone
    ("file.next_8192", 8),         # File.next vs reading the chunk directly
])
def test_overhead_vs_floor(results, name, limit):
    if name not in results:
        pytest.skip(f"{name} unavailable")
    assert results[name]["overhead_x"] < limit, suite.format_row(name, results[name])


# --- keeping up with the fastest SDR ---------------------------------------- #
@pytest.mark.parametrize("name,limit", [
    ("sim.pump_16_blocks", 5),     # whole pipeline sustains >= 5x HackRF's max rate
    ("buffer.update_read", 10),
    ("convert.int8", 10),
    ("convert.int12", 10),
    ("convert.int12_packed", 5),    # bit unpacking: costlier, still well above real time
    ("convert.uint8", 10),
    ("convert.float32", 10),
    ("formats.read.i8o", 20),      # was 2.6x before the one's-complement fast path
    ("formats.read.i16o", 20),
    ("formats.read.ci8t", 5),
    ("formats.read.ci16t", 10),
])
def test_realtime_headroom(results, name, limit):
    assert results[name]["realtime_x"] > limit, suite.format_row(name, results[name])


def test_ring_append_does_not_slow_down_with_use(results):
    """A big backlog is drained in bounded chunks without being much slower than one read"""
    chunks, once = results["buffer.read_chunks"], results["buffer.update_read"]
    per_sample_chunks = chunks["best"] / chunks["items"]
    per_sample_once = once["best"] / once["items"]
    assert per_sample_chunks < per_sample_once * 12


# --- generic Device call overhead (microseconds per call) ------------------- #
@pytest.mark.parametrize("name,max_us", [
    ("device.get_samples_empty", 50),
    ("device.set_freq", 100),
    ("device.set_rx_gain", 300),
])
def test_device_call_cost(results, name, max_us):
    assert results[name]["best"] * 1e6 < max_us, suite.format_row(name, results[name])


# --- a real-time 20 MS/s stream ---------------------------------------------- #
def test_stream_20MS_keeps_up(results):
    assert results["sim.stream_20MS.dropped"]["value"] == 0
    assert results["sim.stream_20MS.delivered"]["value"] > 0.999
    assert results["sim.stream_20MS.max_lag"]["value"] < 50   # ms the producer ever ran behind
    assert results["sim.stream_20MS.rate"]["value"] > 18       # MS/s achieved vs 20 requested


# --- real hardware ----------------------------------------------------------- #
@pytest.mark.skipif(not HW, reason="set PYSDRLIB_HW=1 with a HackRF attached")
@pytest.mark.parametrize("rate", [10, 20])
def test_hackrf_stream_keeps_up(results, rate):
    key = f"hackrf.stream_{rate}MS"
    if f"{key}.dropped" not in results:
        pytest.skip("no usable HackRF")
    assert results[f"{key}.dropped"]["value"] == 0
    assert results[f"{key}.rate"]["value"] > 0.9 * rate
    assert results[f"{key}.callback_load"]["value"] < 25   # % of one core spent in pysdrlib's callback


# --- optional: compare against this machine's recorded baseline ---------------- #
@pytest.mark.skipif(os.environ.get("PYSDRLIB_BENCH_CHECK") != "1",
                    reason="set PYSDRLIB_BENCH_CHECK=1 to compare with .benchmarks/baseline.json")
def test_no_regression_vs_baseline(results):
    import json
    path = Path(__file__).resolve().parents[1] / ".benchmarks" / "baseline.json"
    if not path.exists():
        pytest.skip("no baseline; run tests/bench/run.py --update-baseline")
    base = json.load(open(path))
    cur = {"meta": {}, "results": results}
    bad = [r for r in suite.compare(base, cur, 0.35) if r[1] == "regression"]
    assert not bad, "\n".join(f"{n}: {b:.4g} -> {c:.4g} ({ch:+.0%})" for n, _, b, c, ch in bad)
