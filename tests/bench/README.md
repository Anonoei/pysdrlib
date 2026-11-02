# pysdrlib benchmarks

Measures the overhead pysdrlib adds on the sample path (driver callback -> conversion -> buffer ->
`get_samples()`), the generic `Device` call cost, and format/file IO. Everything except the
`hardware` group runs on the simulated device (`pysdrlib.sim`), so no SDR is needed.

```sh
python tests/bench/run.py                    # full table (~40 s); --quick is ~7 s
python tests/bench/run.py --hardware         # also stream from a real HackRF at 10 and 20 MS/s
python tests/bench/run.py --list
```

Each timing is best-of-N seconds per call. Where a bare-numpy equivalent exists it is measured as a
"floor", so the table shows `x1.05` (5% over a plain copy) rather than just a number. `MS/s` columns
are compared with 20 MS/s, the HackRF's maximum, so `35x` means 35x real-time headroom.

## Catching regressions between commits

Timings differ between machines, so the baseline is local (`.benchmarks/`, gitignored).

```sh
python tests/bench/run.py --update-baseline  # record this commit as the baseline
# ...change code...
python tests/bench/run.py --quick --check    # exit 1 if anything is >25% slower
```

`--check` re-measures anything that looks slower before failing (to rule out noise) and warns if the
baseline came from a different CPU. Use `--threshold 0.4` to loosen it, `--save` to keep each run in
`.benchmarks/runs/`.

To run it on every commit, add `.git/hooks/pre-commit` (and `chmod +x` it):

```sh
#!/bin/sh
python tests/bench/run.py --quick --check || {
    echo "benchmark regression; fix it or commit with --no-verify"; exit 1; }
```

## pytest

```sh
pytest -m benchmark                          # machine-independent guard rails (overhead ratios, 20 MS/s headroom)
PYSDRLIB_HW=1 pytest -m benchmark            # + real-HackRF stream must not drop samples
PYSDRLIB_BENCH_CHECK=1 pytest -m benchmark   # + compare with .benchmarks/baseline.json
```

Benchmarks are deselected from a plain `pytest`.

## Adding one

Decorate a factory in `suite.py` with `@bench("group.name", "group")` that returns a `Case(fn, floor, items)`
(timed) or, with `kind="gauges"`, a dict of `Gauge` values from a longer scenario.
