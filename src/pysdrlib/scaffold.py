"""Generate the skeleton of a new driver

    python -m pysdrlib.scaffold my_radio                       # a driver on a compiled extension (C++ and Python)
    python -m pysdrlib.scaffold my_radio --class MyRadio       # name the Device class (default: CamelCase of the name)
    python -m pysdrlib.scaffold my_radio --python-only         # no extension: a Device that talks to its radio in Python

It writes the files `pysdrlib.contributing` lists, filled in with the names, and fails if any already exists. The
Python driver imports and passes `pysdrlib.testing.check_driver` as generated; what it can't know (the vendor's API,
the radio's limits and gain stages) is marked `TODO`. A native driver's `_ext/<name>.py` returns no extension from
`get()` until you have replaced the C++ placeholders, so an unfinished driver never breaks `pip install`.
"""
import argparse
import re
import sys
from pathlib import Path

PACKAGE = Path(__file__).parent

def camel(name):
    return "".join(part.capitalize() for part in name.split("_"))

def _fill(text, names):
    for token, value in names.items():
        text = text.replace(f"@{token}@", value)
    return text

# --------------------------------------------------------------------------- Python templates
INIT = '''from .._ext import @NAME@ as ext, utils

@CLASS@ = utils.import_device(__name__, ".@NAME@", "@CLASS@", ext.LIB)
Device = @CLASS@

def find():
    """[DeviceInfo] of the @CLASS@ units attached"""
    from ..devices import DeviceInfo
    from . import lib
    found = []
    # TODO: ask the extension what is plugged in. open_args is what Device.open takes to open that unit
    #   for i, serial in enumerate(lib.@NAME@.serials()):
    #       found.append(DeviceInfo("@NAME@", "@CLASS@", serial, i, {"serial": serial}))
    return found
'''

INIT_PY = '''from .@NAME@ import @CLASS@
Device = @CLASS@

def find():
    """[DeviceInfo] of the @CLASS@ units attached"""
    from ..devices import DeviceInfo
    return []   # TODO: DeviceInfo("@NAME@", label, serial, index, open_args) for each unit
'''

DEVICE = '''from ..base.native import NativeDevice
from ..base import convert
from ..base.caps import Capabilities, GainStage, Range
from .. import err

from . import lib

class @CLASS@(NativeDevice):
    """@CLASS@: TODO one line on the radio, then what a user needs to know that `caps` doesn't say"""
    NAME = "@CLASS@"
    # TODO: the limits and defaults of the real radio, and one gain stage per entry, named as the radio calls it:
    # GainStage(step, min, max, default). A transmitter adds a "tx" dict and sets CAN_TRANSMIT.
    CAPS = Capabilities(
        driver="",
        freq=[Range(1_000_000, 6_000_000_000)], default_freq=100_000_000,
        rates=[Range(250_000, 20_000_000)], default_rate=2_000_000,
        gains={"rx": {"MAIN": GainStage(1, 0, 40, 20)}, "tx": {}},
    )
    CAN_TRANSMIT = False
    DUPLEX = False
    SAMPLE_FORMAT = convert.INT16   # TODO: the raw stream the extension delivers (convert.INT8, UINT8, FLOAT32...)
    # After a retune: samples already in flight from before it, and the synthesizer's settling time. Measure these
    RETUNE_SAMPLES = 0
    RETUNE_SETTLE = 0.0

    device: lib.@NAME@.device   # the extension's device (None until `open`)

    def __init__(self):
        super().__init__()

    def _open(self, serial_number=None, index=0):
        """Open the unit with this serial number, or else the `index`th one attached"""
        try:
            if serial_number is None:
                return lib.@NAME@.open(index)
            return lib.@NAME@.open_by_serial(serial_number)
        except lib.err.NOT_FOUND as exc:
            raise err.NoDevice("No @CLASS@ devices found!") from exc

    # --- tuning --- #
    def _set_rx_sample_rate(self, Fs, channel=0):
        self.device.set_sample_rate(Fs)
        self._rate_changed()   # sizes the receive queue for the new rate
    def _set_rx_freq(self, freq, channel=0):
        self.device.set_freq(int(freq))

    # --- gain: one hook per stage in CAPS applies it (set_rx_main_gain is made from CAPS) --- #
    # `set_rx_gain_db(total)` splits a total across the stages; `set_rx_gain(fraction)` is the same by fraction; the stage setter sets one.
    def _set_rx_main_gain(self, gain, channel=0):
        self.device.set_rx_gain(float(gain))

    def _set_bias_t(self, bias):
        raise err.NotSupported(f"{type(self).NAME} has no bias tee")

    # --- what this unit can do, beyond CAPS: see pysdrlib.base.caps --- #
    def _capabilities(self, caps):
        # caps.bandwidths = {"rx": [Range(lo, hi)], "tx": []}
        # caps.antennas = {"rx": [...], "tx": []}
        # caps.gain_modes["rx"] = ["manual", "auto"]      (then implement _set_gain_mode)
        # caps.info["serial"] = ...
        return caps
'''

DEVICE_PY = '''import numpy as np

from ..base.device import Device as _Device
from ..base.caps import Capabilities, GainStage, Range
from .. import err


class @CLASS@(_Device):
    """@CLASS@: TODO one line on the radio. A Device in Python: it provides the samples itself, `read()` and the rest
    are built on `_get_samples`. (A driver on a compiled extension is a NativeDevice: see pysdrlib.contributing)"""
    NAME = "@CLASS@"
    # TODO: the limits and defaults of the real radio, and one gain stage per entry, named as the radio calls it:
    # GainStage(step, min, max, default). A transmitter adds a "tx" dict and sets CAN_TRANSMIT.
    CAPS = Capabilities(
        driver="",
        freq=[Range(1_000_000, 6_000_000_000)], default_freq=100_000_000,
        rates=[Range(250_000, 20_000_000)], default_rate=2_000_000,
        gains={"rx": {"MAIN": GainStage(1, 0, 40, 20)}, "tx": {}},
    )
    CAN_TRANSMIT = False
    DUPLEX = False

    def __init__(self):
        super().__init__()

    def _open(self, serial_number=None):
        return object()   # TODO: the connection to the radio, kept as self.device
    def _close(self):
        pass

    def _set_rx_sample_rate(self, Fs, channel=0):
        pass   # TODO
    def _set_rx_freq(self, freq, channel=0):
        pass   # TODO

    def _start_rx(self):
        pass   # TODO
    def _stop_rx(self):
        pass   # TODO
    def _get_samples(self):
        """Whatever has arrived since the last call, as complex64 (possibly none)"""
        return np.empty(0, dtype=np.complex64)   # TODO

    def _set_rx_main_gain(self, gain, channel=0):   # applies the stage; set_rx_main_gain is made from CAPS
        pass   # TODO

    def _set_bias_t(self, bias):
        raise err.NotSupported(f"{type(self).NAME} has no bias tee")
'''

LIB_INIT = '''from . import _@NAME@ as @NAME@
from . import err  # pyright: ignore[reportMissingModuleSource]   # made by the extension when it is imported; err.pyi types it
'''

EXT = '''"""Build and load helpers for the @CLASS@ extension.

The extension is compiled WITHOUT linking the vendor library (only its header is needed), so packaging never builds
or needs it and wheels build anywhere. The library is found or built on the user's machine the first time
`pysdrlib.@NAME@` is imported; see `utils.VendorLibrary`.
"""
from . import utils

LIB = utils.VendorLibrary(
    name="@NAME@",                                              # TODO: the name ctypes.util.find_library knows it by
    source=utils.PATH_MOD / "@NAME@" / "vendor" / "@NAME@",     # TODO: the vendored sources (a CMake project)
    files=("lib@NAME@.so.0", "lib@NAME@.so", "lib@NAME@.dylib", "@NAME@.dll"),
    build_globs=("lib@NAME@.so*", "lib@NAME@*.dylib", "@NAME@.dll"),
    missing_hints=("lib@NAME@",),
    help=("TODO: how to install the library, or what a build of the bundled copy needs "
          "(e.g. `apt install cmake libusb-1.0-0-dev`)."),
)

mk_lib = LIB.build
load_library = LIB.load

def get():
    """The Extension setup.py builds. None while the driver is unfinished: it is left out of the build until then.
    TODO: when _native/@NAME@/ compiles, return the line below instead of None."""
    return None
    # return utils.pybind_extension("@NAME@", "src/pysdrlib/@NAME@/vendor/@NAME@/include", "@NAME@.h")
'''

README = '''# @CLASS@

TODO: what the radio is, and links (vendor page, a how-to, the library it wraps).

## Specs
- Frequency (TODO)
- Sample rate (TODO)
- Samples (TODO: complex int16, converted to complex64)
- Gain (dB): TODO the stages, as `sdr.rx_gain_stages()` lists them

## Setup
TODO: what the first import looks for (system library, cache, bundled build) and what a build needs.

## Notes
TODO: anything measured (retune settling, rates that drop samples), udev rules, quirks.
'''

# --------------------------------------------------------------------------- C++ templates
HPP = '''#pragma once

#include <atomic>
#include <cstdint>
#include <mutex>
#include <string>
#include <thread>

#include "@NAME@.h"   // TODO: the vendor's header
#include "hardware_wrapper.hpp"

// The vendor library's errors are its own; these are pysdrlib's, mapped to the classes of lib/err by the binding.
enum @CLASS@ErrorCode {
    @UPPER@_ERROR_FAILED = -1001,        // a vendor call failed; the message says which
    @UPPER@_ERROR_INVALID_PARAM = -1002,
    @UPPER@_ERROR_NOT_FOUND = -1003,
    @UPPER@_ERROR_NOT_OPEN = -1004,
    @UPPER@_ERROR_OPEN_FAILED = -1005,
    @UPPER@_ERROR_BUSY = -1006,
};

// Throws HardwareError(@UPPER@_ERROR_FAILED, "<what> failed (<result>)") when `result` is an error.
void @NAME@_check(int result, const char* what);

class @CLASS@Device : public HardwareDevice<void*> {   // TODO: HardwareDevice<the vendor's handle type>
public:
    @CLASS@Device() = default;
    ~@CLASS@Device() override;

    void open(uint32_t index);
    void open_by_serial(const std::string& serial);
    void close() override;

    // TODO: the raw stream as the hardware delivers it ({kind, significant bits, storage bits, packed})
    SampleFormat rx_sample_format() const override { return {SampleKind::Int, 16, 16, false}; }

    void start_rx() override;
    void stop_rx() override;
    bool is_streaming() const override { return running_; }

    void set_freq(uint64_t freq_hz);
    double set_sample_rate(double rate_hz);   // returns the rate the hardware settled on
    double set_rx_gain(double gain_db);

protected:
    HardwareError not_open_error() const override;

private:
    std::atomic<bool> running_{false};
};

uint32_t @NAME@_device_count();
'''

CPP = '''#include "@NAME@_device.hpp"

// Where the vendor's streaming thread hands over a buffer, call
//     dispatch_rx(data, valid_bytes, buffer_bytes);
// It converts to complex64 and queues on that thread, with no Python involved; a non-zero return asks you to stop.
// Report samples the hardware says it lost with skip_rx(count), so readers see the gap.
// See _native/rtl_sdr/ (a callback driver) and _native/uhd/ (a polled one) for the whole of it.

void @NAME@_check(int result, const char* what) {
    if (result >= 0) return;
    throw HardwareError(@UPPER@_ERROR_FAILED, std::string(what) + " failed (" + std::to_string(result) + ")");
}

uint32_t @NAME@_device_count() { return 0; }   // TODO

@CLASS@Device::~@CLASS@Device() {
    try { close(); } catch (...) {}
}

HardwareError @CLASS@Device::not_open_error() const {
    return HardwareError(@UPPER@_ERROR_NOT_OPEN, "@CLASS@ device is not open");
}

void @CLASS@Device::open(uint32_t index) {
    close();
    // TODO: open the unit; set handle_; throw @UPPER@_ERROR_NOT_FOUND / @UPPER@_ERROR_OPEN_FAILED
    throw HardwareError(@UPPER@_ERROR_NOT_FOUND, "No @CLASS@ with index " + std::to_string(index));
}

void @CLASS@Device::open_by_serial(const std::string& serial) {
    // TODO: find the index of the unit with this serial, then open(index)
    throw HardwareError(@UPPER@_ERROR_NOT_FOUND, "No @CLASS@ with serial number '" + serial + "'");
}

void @CLASS@Device::close() {
    if (!handle_) return;
    // TODO: stop the stream (join its thread) first, then drop rx_callback_, then close the handle
    handle_ = nullptr;
    rx_callback_ = nullptr;
}

void @CLASS@Device::start_rx() { require(); /* TODO: start the stream; running_ = true */ }
void @CLASS@Device::stop_rx()  { require(); /* TODO: stop it and join the thread; running_ = false */ }

void @CLASS@Device::set_freq(uint64_t freq_hz) { require(); (void)freq_hz; /* TODO */ }
double @CLASS@Device::set_sample_rate(double rate_hz) { require(); return rate_hz; /* TODO: read back the real rate */ }
double @CLASS@Device::set_rx_gain(double gain_db) { require(); return gain_db; /* TODO */ }
'''

PYBIND = '''#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <memory>

#include "@NAME@_device.hpp"
#include "hardware_pybind.hpp"

namespace py = pybind11;

PYBIND11_MODULE(_@NAME@, m) {
    m.doc() = "@CLASS@ bindings (pybind11)";

    // Error codes -> classes in pysdrlib.@NAME@.lib.err: the one place they are listed (name, docstring)
    hw::register_errors(m, "@CLASS@Error", {
        {@UPPER@_ERROR_INVALID_PARAM, "INVALID_PARAM", ""},
        {@UPPER@_ERROR_NOT_FOUND, "NOT_FOUND", ""},
        {@UPPER@_ERROR_NOT_OPEN, "NOT_OPEN", ""},
        {@UPPER@_ERROR_OPEN_FAILED, "OPEN_FAILED", "The unit is there but could not be opened: no permission, or something else has it"},
        {@UPPER@_ERROR_BUSY, "BUSY", ""},
    });

    // bind_device adds what every device shares (close, start_rx, the receive queue...); add the device's own controls
    hw::bind_device<@CLASS@Device>(m, "device")
        .def("set_freq", &@CLASS@Device::set_freq)
        .def("set_sample_rate", &@CLASS@Device::set_sample_rate)
        .def("set_rx_gain", &@CLASS@Device::set_rx_gain);

    m.def("device_count", [] { return @NAME@_device_count(); });

    m.def("open", [](uint32_t index) {
        return hw::open_device<@CLASS@Device>([&](@CLASS@Device& d) { d.open(index); });
    }, py::arg("index") = 0);
    m.def("open_by_serial", [](const std::string& serial) {
        return hw::open_device<@CLASS@Device>([&](@CLASS@Device& d) { d.open_by_serial(serial); });
    });
}
'''

def files(name, cls, native=True):
    """{path relative to the pysdrlib package: text} of a new driver"""
    names = {"NAME": name, "CLASS": cls, "UPPER": name.upper()}
    out = {
        f"{name}/__init__.py": INIT if native else INIT_PY,
        f"{name}/{name}.py": DEVICE if native else DEVICE_PY,
        f"{name}/README.md": README,
    }
    if native:
        out.update({
            f"{name}/lib/__init__.py": LIB_INIT,
            f"_ext/{name}.py": EXT,
            f"_native/{name}/{name}_device.hpp": HPP,
            f"_native/{name}/{name}_device.cpp": CPP,
            f"_native/{name}/{name}_pybind.cpp": PYBIND,
        })
    return {path: _fill(text, names) for path, text in out.items()}

def new_driver(name, cls=None, native=True, root=None):
    """Write the skeleton of the driver `name` under `root` (default: the pysdrlib package). Returns the paths written;
    raises FileExistsError, writing nothing, if any already exists"""
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name) or name in ("base", "file", "remote", "testing", "scaffold"):
        raise ValueError(f"{name!r}: a driver name is lower case letters, digits and underscores, starting with a letter, "
                         "and not one pysdrlib already uses")
    cls = cls or camel(name)
    if not cls.isidentifier():
        raise ValueError(f"{cls!r} is not a valid class name")
    root = Path(root) if root else PACKAGE
    planned = {root / path: text for path, text in files(name, cls, native).items()}
    existing = [str(p) for p in planned if p.exists()]
    if existing:
        raise FileExistsError("already exists: " + ", ".join(existing))
    for path, text in planned.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return list(planned)

def next_steps(name, cls, native=True):
    steps = [
        f"1. Fill in every TODO: src/pysdrlib/{name}/{name}.py, starting with CAPS (the radio's limits and gain stages)"
        + (f", then the C++ in src/pysdrlib/_native/{name}/ and _ext/{name}.py (make get() return the extension)." if native else "."),
        "2. Check it as you go:  python -c \"from pysdrlib import testing; "
        f"print(*testing.check_package('{name}'), sep='\\n')\"",
        f"3. Add a Spec for it to tests/devices.py (the contract tests then run on it), and tests/test_{name}_api.py "
        "for what is particular to the radio." if native else
        f"3. Write tests/test_{name}_api.py (see tests/test_sim.py); tests/test_conformance.py already checks the driver.",
        "See pysdrlib.contributing for the whole guide.",
    ]
    return "\n".join(steps)

def main(argv=None):
    parser = argparse.ArgumentParser("python -m pysdrlib.scaffold", description="Generate the skeleton of a new pysdrlib driver")
    parser.add_argument("name", help="driver name: its package, lower case with underscores (e.g. bladerf, rtl_sdr)")
    parser.add_argument("--class", dest="cls", help="Device class name (default: CamelCase of the name)")
    parser.add_argument("--python-only", action="store_true", help="no compiled extension: a Device written in Python")
    parser.add_argument("--root", help="directory to write into (default: the installed pysdrlib package)")
    args = parser.parse_args(argv)
    try:
        written = new_driver(args.name, args.cls, not args.python_only, args.root)
    except (ValueError, FileExistsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    base = Path(args.root) if args.root else PACKAGE
    for path in written:
        print("created", path.relative_to(base))
    print()
    print(next_steps(args.name, args.cls or camel(args.name), not args.python_only))
    return 0

if __name__ == "__main__":
    sys.exit(main())
