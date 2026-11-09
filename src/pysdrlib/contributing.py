"""Adding a device to pysdrlib

A driver makes one radio look like every other: the same calls, the same units, samples as complex64. This page is the
whole job, in order. Start from a generated skeleton:

    python -m pysdrlib.scaffold my_radio                  # C++ extension + Python (most hardware)
    python -m pysdrlib.scaffold my_radio --python-only    # a Device written in Python alone

then fill in each `TODO`, checking as you go with `pysdrlib.testing`.

## What a driver is made of

| File | What it holds |
| --- | --- |
| `<name>/config.py` | The radio's limits, defaults and gain stages (`ConfigDevice`) |
| `<name>/<name>.py` | The `Device` subclass: the hooks below |
| `<name>/__init__.py` | `Device = <class>` and `find()` |
| `<name>/lib/__init__.py`, `lib/err.py` | The compiled extension's import, and its error classes (`make_errors`, from `pysdrlib.base.errors`) |
| `<name>/README.md` | What the radio is, its specs, what to install |
| `_ext/<name>.py` | `LIB` (how the vendor library is found or built) and `get()` (the extension, for setup.py) |
| `_native/<name>/` | `<name>_device.hpp/.cpp` (the wrapper) and `<name>_pybind.cpp` (its Python binding) |
| `tests/devices.py` | A `Spec`, which puts the driver under every contract test |

Nothing else is edited: `pysdrlib.devices.ls()` finds a driver by its `Device = ...` line, and setup.py builds every
`_ext/<name>.py` whose `get()` returns an extension. (Until it does, the driver is left out of the build.)

## 1. Describe the radio: `config.py`

Limits and defaults, and the gain stages by the names the hardware gives them. Nothing generic knows these names.

    ConfigDevice({
        "DEFAULT": {"FREQ": 100e6, "SAMPLE_RATE": 2e6, "GAIN_RX_AMP": 16},
        "FREQ_MIN": 1e6, "FREQ_MAX": 6e9, "SAMPLE_RATE_MIN": 250e3, "SAMPLE_RATE_MAX": 20e6,
        "GAIN": {"RX_AMP_STEP": 8, "RX_AMP_MIN": 0, "RX_AMP_MAX": 40},      # <RX|TX>_<STAGE>_<STEP|MIN|MAX>
    })

This is what `sdr.caps` reports before the device is open. Whatever only the attached unit knows (the tuner it has,
its real gain table, the rates its firmware offers) is added in `Device._capabilities(caps)` once open.

## 2. The `Device` class

A driver on an extension subclasses `pysdrlib.base.native.NativeDevice`, which supplies receiving, the sample queue,
stall and drop detection, and `read()`. A driver implements only what is particular to its hardware:

| Hook | Does |
| --- | --- |
| `_open(serial_number=None, ...)` | Returns the extension's device. Map "not found" to `err.NoDevice` |
| `_set_freq(freq)`, `_set_sample_rate(Fs)` | Tune. Call `self._rate_changed()` after a rate change |
| `_set_<dir>_<stage>_gain(gain)` | Apply one gain stage (one per stage in the config) |
| `set_<dir>_<stage>_gain(gain=None)` | Not written: made from the config for each stage (write one yourself only to document it better; it calls `self._set_gain_stage("RX", "AMP", gain, self._set_rx_amp_gain)`) |
| `_capabilities(caps)` | Refine `caps` for the attached unit |
| `_set_bandwidth`, `_set_antenna`, `_set_gain_mode`, `_set_clock_source`, `_set_bias_t` | Only the controls the hardware has. A control left out raises `err.NotSupported` |
| `_set_rx_channels`, `_set_tx_channels` | Only if it has several channels (see `caps.channels`, `caps.shared`) |
| `_tx_write(samples, timeout, slot)`, `_start_tx`, `_stop_tx`, `_tx_check`, `_tx_stats` | Only if `CAN_TRANSMIT` |

Class attributes: `NAME`, `CONFIG`, `CAN_TRANSMIT`, `DUPLEX` (separate receive and transmit synthesizers),
`SAMPLE_FORMAT` (the raw stream the extension delivers), `RETUNE_SAMPLES` and `RETUNE_SETTLE` (what a retune leaves in
flight and how long it takes to settle: measure them, as the comments in the shipped drivers do), `SETTINGS` (anything else the radio
has, as `caps.Setting`s a user reaches with `set_setting`), `GAIN_RESERVE` and `GAIN_BALANCE` (how a total gain is split
across stages).

**Two layers of API stay in step.** The generic calls (`set_gain`, `set_rx_gain`, which split a total over the stages)
work on every device. Each stage also has its own setter (`set_rx_amp_gain`) under the hardware's name. You provide only
the config's declaration of the stage and `_set_<dir>_<stage>_gain`, which applies it; the setter is made from them.

A hook takes the arguments the base class's does (`_set_freq(freq)`, `_set_bandwidth(direction, bandwidth)`...), and
may take `channel`, `direction` or `slot` as keywords with defaults, which are passed only to hooks that ask for them.
`testing.check_driver` reports a hook that needs different arguments.

**Samples are complex64, full scale +-1.** The extension converts from the hardware's format (`SAMPLE_FORMAT`) on its
streaming thread; do not go through complex128, or float64 at all.

**Out-of-range values** are clamped with a warning (or raise `err.InvalidValue` when `sdr.strict`): the base class does
this from the config, so a driver never validates its own limits. `_set_gain_stage` snaps to the stage's step or table.

## 3. The extension (C++)

`_native/<name>/` wraps the vendor's C API in a `HardwareDevice` (`include/hardware_wrapper.hpp`) and binds it with
`hw::bind_device` (`include/hardware_pybind.hpp`), which adds everything devices share: start and stop, the queue,
statistics. Your code:

- opens and closes the handle, and throws `HardwareError(code, message)` on failure;
- on its streaming thread, passes each buffer to `dispatch_rx(data, valid_bytes, buffer_bytes)`, and reports samples
  the hardware says it lost with `skip_rx(count)` so readers see the gap;
- exposes the device's controls (`set_freq`, `set_sample_rate`...) as methods, bound in `<name>_pybind.cpp`.

The error codes you throw are mapped by name to the classes in `lib/err.py`, which `make_errors` defines from a list of names. `_native/rtl_sdr/` (a vendor callback) and
`_native/uhd/` (several channels, transmit) are complete examples.

The extension is built **without linking** the vendor library: its symbols resolve when `_ext/<name>.py`'s `LIB` loads
the library at first import (installed, cached, or built from the vendored sources with cmake, no network). Vendored
sources go in `<name>/vendor/` (a git submodule). Give `LIB.help` a message a user can act on.

## 4. Finding units: `find()`

`<name>/__init__.py` defines `find()`, returning a `DeviceInfo(driver, label, serial, index, open_args)` for each
attached unit. `open_args` is what `Device.open` takes to open that one (`{"serial": ...}` or `{"index": ...}`), which
is how `pysdrlib.open(info)` works. It must not open the radio, and must not raise when none is attached.

## 5. Check it

    from pysdrlib import testing
    print(*testing.check_package("my_radio"), sep="\\n")

`testing.check_driver` reports a gain stage with no setter, a `Setting` naming a method that does not exist, a
default outside its range, a missing `SAMPLE_FORMAT`, `CAN_TRANSMIT` without a `_tx_write`. Errors are broken; warnings
are missing polish. `tests/test_conformance.py` runs it on every driver.

Tests run without hardware. Add a `Spec` for the driver to `tests/devices.py`: it says how to encode a known sample
block in the raw format, what a fake of the extension returns, and which calls must fail on a closed device. Every
`test_device_contract.py` and `test_native_contract.py` test then runs on the driver over a fake. Put what is particular to
the radio (its gain stages, rate snapping, quirks) in `tests/test_<name>_api.py` over the same fake. A test that
needs the radio is marked `hardware`, runs only with `PYSDRLIB_HW=1`, and skips when none is attached.

## 6. Document it

The reference documentation is generated from docstrings: document the class, and every public method a driver adds,
as you would for a user. Fill in `README.md`: what the radio is, its specs, what to install, and what you measured.
Add it to the roadmap list in the top-level README.
"""
