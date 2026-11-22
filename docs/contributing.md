# Contributing

```
git clone https://github.com/Anonoei/pysdrlib
cd pysdrlib
git switch -c feature/<your feature>
python tools/builder.py -s -c -t     # install with the dev extras, compile the extensions in place, run the tests
```

## `tools/builder.py`

| Flag | Does |
| --- | --- |
| `-s` | Install the package and the dev extras (editable) |
| `-n NAME` | Generate the skeleton of a new driver (`python -m pysdrlib.scaffold`) |
| `-v M\|m\|p` | Bump the major, minor or patch version (a dry run without `-r`) |
| `-c` | Compile the extensions in place |
| `-b` | Build the sdist and wheel into `dist/`, and check them with twine |
| `-l` | Install in editable mode |
| `-t` | Run the tests |
| `-d` | Build this site into `site/` |
| `-u` | Upload `dist/` to TestPyPI (PyPI with `-r`) |

The same helper, with the same flags, is in RPPS and pyspecan.

Releases are published by CI from a GitHub release tagged `vX.Y.Z`, and the site by `deploy-docs.yml` on every push to
`main`.

## Tests

`pytest` runs without hardware. Drivers are tested over fakes of their extensions (`tests/devices.py`: a `Spec` per
driver puts it under every contract test), and the rest over the simulated radio. Tests for a driver whose vendor
library isn't there are skipped.

- `PYSDRLIB_HW=1 pytest` adds the hardware tests (marked `hardware`). Each skips when its radio isn't attached.
- `pytest -m benchmark` runs the timing-sensitive checks, which are left out by default.
- `tests/test_examples.py` runs every script in `examples/`; `tests/test_docs.py` checks the docs are complete.

## Adding a driver

`python -m pysdrlib.scaffold my_radio` writes the skeleton (`--python-only` for one without a C++ extension), and
`pysdrlib.testing.check_package("my_radio")` checks it as you fill it in. The whole job, in order, is
[Adding a driver](api/contributing.md) (`pysdrlib.contributing`).

## Vendor libraries

Each driver's vendor library ships as a trimmed snapshot under `src/pysdrlib/<driver>/vendor/`, which
`pysdrlib build` compiles. `tools/vendor.py` makes the snapshots from a pinned upstream tag; bumping a library means
changing `ref` in `tools/vendor/<driver>/vendor.toml` and running `python tools/vendor.py snapshot <driver>`. Check the
result with `pysdrlib build <driver> --force` and the tests.

## Benchmarks

`tools/bench/run.py` measures the overhead pysdrlib adds on the sample path (driver callback → conversion → buffer →
`get_samples()`), the cost of the generic `Device` calls, and format and file IO. Everything except the `hardware`
group runs on the simulated radio.

```sh
python tools/bench/run.py                    # full table (~40 s); --quick is ~7 s
python tools/bench/run.py --hardware         # also stream from every supported SDR that is plugged in
python tools/bench/run.py --hardware rtl_sdr # ONLY the hardware benchmarks, for the named device(s)
python tools/bench/run.py --list
```

Each timing is best-of-N seconds per call. Where a bare-numpy equivalent exists it is measured as a floor, so the table
shows `x1.05` (5% over a plain copy) rather than just a number. `MS/s` columns are compared with 20 MS/s (the HackRF's
maximum), so `35x` means 35x real-time headroom.

Timings differ between machines, so the regression baseline is local (`.benchmarks/`, gitignored):

```sh
python tools/bench/run.py --update-baseline  # record this commit as the baseline
# ...change code...
python tools/bench/run.py --quick --check    # exit 1 if anything is >25% slower
```

`--check` re-measures anything that looks slower before failing (to rule out noise), and warns if the baseline came
from a different CPU. `--threshold 0.4` loosens it; `--save` keeps each run in `.benchmarks/runs/`.

## Documentation

This site is built with [Zensical](https://zensical.org) and mkdocstrings:

```
pip install -e '.[docs]'
python tools/builder.py -d          # API pages, the site into site/, llms.txt and llms-full.txt
python -m zensical serve      # preview (after tools/builder.py -d has written docs/api/)
```

Each kind of documentation has one home:

- **Concepts and reference** are the package and module docstrings, rendered as the API pages. A driver's page is
  its `__init__.py` docstring.
- **How to use it** goes in the guides in `docs/guide/`. They embed the scripts in `examples/`
  (`--8<-- "examples/x.py:section"`), and a missing file fails the build.
- **For agents**, `AGENTS.md` is the hand-written orientation. `tools/gen_docs.py` writes `llms.txt` and
  `llms-full.txt` from it and from the docstrings.

`docs/api/` is generated (`API_PAGES` in `tools/gen_docs.py`); a new module's page goes there and in `zensical.toml`'s nav.
