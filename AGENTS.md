# pysdrlib for agents

Python wrappers for hardware SDRs. Every radio presents the same `Device` API, and samples are always `complex64`.
It is the radio backend of pyspecan (a spectrum analyzer) and RPPS (a signal processing library). The goal is a lighter,
narrower SoapySDR: each driver wraps its vendor's own library, vendored and built by pysdrlib. There is no generic
bridge. Docs: https://anonoei.github.io/pysdrlib/ (`llms-full.txt` there is this file, every package docstring and the
public signatures in one file).

## Package map

| Where | What |
| --- | --- |
| `pysdrlib/__init__.py` | Re-exports: `find`, `open`, `device`, `connect`, `register`, `discover`, `Device`, `Capabilities`, `Range`, `Setting`, `RxMeta`, `Event`, `File`, `FileSink`, `Formats`, `err`, `warn` |
| `devices.py` | The driver registry (`ls`, `get`; entry points `pysdrlib.drivers`), `find`, `open`, `device`, `connect`, `register`, `DeviceInfo` |
| `targets.py` | `discover()` and target strings (`"hackrf@SERIAL"`, `"host/hackrf-0"`) |
| `base/device.py` | `Device`: the API every driver presents, and the generic logic (gain fractions and splitting, range checks, `read`/`stream`/`sweep`, drop and stall reporting) |
| `base/native.py` | `NativeDevice`: a `Device` over a compiled extension that streams on its own thread without the GIL |
| `base/caps.py` | `Capabilities`, `Range`, `GainStage`, `Setting`: what a driver declares (`CAPS`) |
| `base/stream.py` | `RxMeta` (index, gaps, events, time, hw_time), `Event` |
| `base/formats.py`, `base/convert.py` | Sample formats on disk and wire (`cf32`, `ci16`, `cu8`...), raw-to-complex64 conversion |
| `<driver>/` | `rtl_sdr`, `hackrf`, `uhd` (B210, X310, X410), `bladerf`, `extio` (RX-888), `tcpiq`: `__init__.py` (docstring = the radio's page, `Device`, `find`), `<module>.py` (the class), `lib/` (the extension import), `vendor/` (the vendored library) |
| `_ext/<driver>.py` | `LIB` (how the vendor library is found or built) and `get()` (the extension, for setup.py); `_ext/build.py` is `pysdrlib build` |
| `_native/<driver>/` | C++: `<name>_device.hpp/.cpp` (the wrapper) and `<name>_pybind.cpp`; `_native/include/` is shared (queue, shm ring) |
| `sim/` | A simulated radio on the real native streaming path: tones, noise, bursts, channels, drops/stalls on demand |
| `file/` | `File`, `FileSink`, SigMF, `Replay` (`open("file", path=...)`: a recording as a radio, and transmit into a file) |
| `remote/` | ZeroMQ server/client, controllers and viewers, shared-memory transport on one machine, relays, mDNS discovery, the `pysdrlib server/client/relay/discover` commands |
| `testing.py`, `contributing.py`, `scaffold.py` | Driver checks (`check_driver`, `check_package`), the driver-writing guide (a docstring), the skeleton generator |
| `cli.py` | The `pysdrlib` command (`build`, `play`, `server`, `client`...) |

## The device model

- `pysdrlib.open("hackrf")` returns the device opened. `pysdrlib.device("sim", channels=2)` constructs one and leaves
  `open()` to you. `find()` lists the radios attached; it only asks the drivers whose library is present.
- `sdr.initialize(cf=..., Fs=...)`, `start_rx()`, then `read(n, timeout=, meta=True)`. `read` returns exactly `n`
  samples, plus an `RxMeta` (stream index, gaps where samples were lost, retune events). `stream()` and `sweep()` are
  built on it. `get_samples()` is the low-level "whatever has arrived".
- Every control that differs by direction has `set_rx_*` and `set_tx_*`. There is no `direction=` argument. Every
  control takes `channel=0`. `start_rx(channels=(0, 1))` with `read(n, channel=(0, 1))` returns `(2, n)`.
- `sdr.caps` says what the radio can do. A control it lacks raises `err.NotSupported`. Out-of-range values are
  clamped, or raise with `sdr.strict = True`.
- **Two gain layers, both kept.** The generic `set_rx_gain(fraction)` (portable, 0-1) and `set_rx_gain_db(total |
  stage=)`, *and* each driver's own per-stage setters (`set_rx_lna_gain`...), generated from `CAPS` stage names. Never
  remove one for the other.
- **Samples are `complex64` everywhere.** Drivers convert in C++. Never introduce a `complex128` intermediate.
- Transmit: `start_tx()`, then `write(complex64 at full scale ±1)`. `write` blocks on a bounded queue and
  `get_tx_stats()` counts underruns.

## Where new code goes

- **A new radio**: `python -m pysdrlib.scaffold NAME` (or `--python-only`, `--out-of-tree`), then follow
  `pysdrlib.contributing` step by step. `pysdrlib.testing.check_package(NAME)` checks the work, and a `Spec` in
  `tests/devices.py` puts the driver under every contract test. The driver's `__init__.py` docstring is its docs page:
  specs, setup, quirks. Add a row to README's device table, and the page to `API_PAGES` in `tools/gen_docs.py` and to
  `zensical.toml`'s nav.
- **A generic feature** (anything every radio should do) goes in `base/device.py` or `base/native.py`, with tests in
  `tests/test_device_contract.py` / `tests/test_native_contract.py`. These run on every driver's fake.
- **Remote/protocol** changes go in `remote/`; `remote/protocol.py` documents the sockets and framing.
- A vendor library update: `tools/vendor.py snapshot DRIVER` after bumping `ref` in `tools/vendor/DRIVER/vendor.toml`.
  Snapshots replace git submodules.

## Commands

```
pip install -e '.[dev]'           # docs: '.[docs]'
python builder.py -c              # compile the extensions in place
python builder.py -t              # pytest (no hardware needed)
python builder.py -d              # docs site into site/ (+ llms.txt, llms-full.txt)
pytest                            # skips are fine, failures are not
PYSDRLIB_HW=1 pytest              # plus hardware tests, each skipping if its radio is absent
pytest -m benchmark               # timing checks (deselected by default)
python tools/bench/run.py --quick # sample-path overhead table (--check: against this machine's baseline)
python tools/dev/api_check.py     # the generic API on a real radio, by eye (tools/dev: hardware check scripts)
pysdrlib build [DRIVER|--all]     # vendor libraries: status, or build into the per-user cache
python examples/quickstart.py     # every example runs on the simulated radio
```

## Conventions

- Tests run without hardware: drivers run over fakes of their extensions, and everything else over `sim`. Hardware
  tests are opt-in and skip per device.
- Importing a driver never builds anything. A missing library fails with a message naming `pysdrlib build`
  (`PYSDRLIB_BUILD=auto` builds on first use).
- `find()` probes no network unless given `addresses=`. USB radios come first, and network/PCIe radios are opt-in. No
  SoapySDR.
- No backwards-compatibility aliases: rename, then fix the callers.
- Docs have one home per kind:
  - concepts and reference live in docstrings, which are the API pages
  - how-to lives in `docs/guide/*.md`, which embed `examples/*.py` (tested by `tests/test_examples.py`)
  - orientation lives here
- Other sessions may edit the same working tree: don't commit or revert changes you didn't make.

## Where to read more

| Question | Read |
| --- | --- |
| Every `Device` method | `pysdrlib.base.device` |
| What a driver declares; ranges, gain stages | `pysdrlib.base.caps` |
| Writing a driver, step by step | `pysdrlib.contributing` |
| One radio's specs, setup and quirks | `pysdrlib.<driver>` package docstring |
| Remote semantics: ownership, need/want, shm, relay, wire formats | `pysdrlib.remote` |
| File formats, SigMF, replay | `pysdrlib.file`, `pysdrlib.base.formats` |
| How to use it, with code | `docs/guide/` (receiving, controls, files, remote, custom-drivers, installing) |
