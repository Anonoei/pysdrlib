"""pysdrlib's generic code must not know about any one device.

Device-specific names (gain stages, tuning stages, sample layouts) belong in that device's package.
"""
import re
from pathlib import Path

import numpy as np
import pytest

from pysdrlib import err
from pysdrlib.base import convert
from pysdrlib.base.config import ConfigDevice
from pysdrlib.base.device import Device

SRC = Path(__file__).resolve().parents[1] / "src" / "pysdrlib"
GENERIC = [*(SRC / "base").glob("*.py"), *(SRC / "file").glob("*.py"),
           *SRC.glob("*.py"), *(SRC / "_ext").glob("hardware_*.hpp")]

# Names that only make sense for particular hardware
VENDOR_NAMES = [
    r"freq_(cf|if|lo)", r"(rx|tx)_(rf|if|bb)_gain", r"GAIN_(RX|TX)_(RF|IF|BB)\b", r"(RX|TX)_(RF|IF|BB)_(STEP|MIN|MAX)",
    r"hackrf", r"libhackrf", r"\bLNA\b", r"\bVGA\b",
]


def test_generic_files_were_found():
    assert len(GENERIC) > 8 and any(p.name == "device.py" for p in GENERIC)


@pytest.mark.parametrize("pattern", VENDOR_NAMES)
def test_no_vendor_specific_names_in_generic_code(pattern):
    hits = []
    for path in GENERIC:
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(pattern, line, re.IGNORECASE if pattern.islower() else 0):
                hits.append(f"{path.relative_to(SRC)}:{n}: {line.strip()}")
    assert not hits, "\n".join(hits)


def test_no_sample_width_assumptions_in_generic_code():
    """Generic code must not hard-code 8-bit samples (the HackRF's); formats come from a SampleFormat."""
    hits = []
    for path in GENERIC:
        if path.name in ("io.py", "hardware_numpy.hpp"):   # these define the per-width layouts
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r"\b(u?int8(_t)?|/ ?= ?128|/ 128)\b", line) and "SampleFormat" not in line:
                if path.name == "convert.py" and ("INT8" in line or "int8" in line.lower() and "dtype" in line):
                    continue
                hits.append(f"{path.relative_to(SRC)}:{n}: {line.strip()}")
    # convert.py defines the INT8/UINT8 *constants* and documents them; nothing else may mention 8-bit
    hits = [h for h in hits if not h.startswith("base/convert.py")]
    assert not hits, "\n".join(hits)


def test_device_base_class_surface():
    public = {n for n in dir(Device) if not n.startswith("_")}
    assert {"open", "close", "initialize", "start_rx", "stop_rx", "get_samples", "set_freq", "set_sample_rate",
            "set_rx_gain", "set_tx_gain", "set_bias_t"} <= public
    assert not {n for n in public if re.search(r"(rf|if|bb|cf|lo)_(gain|freq)|freq_(cf|if|lo)", n)}


# --- a device with no connection to any existing one -------------------------------------------
class ConfigOdd(ConfigDevice):
    __instance = None
    def __init__(self):
        super().__init__({
            "DEFAULT": {"FREQ": 1_000_000, "SAMPLE_RATE": 2_000_000, "GAIN_RX_MIXER": 5, "GAIN_RX_PREAMP": 0},
            "FREQ_MIN": 1, "FREQ_MAX": 10_000_000, "SAMPLE_RATE_MIN": 1, "SAMPLE_RATE_MAX": 4_000_000,
            "GAIN": {"RX_MIXER_STEP": 5, "RX_MIXER_MIN": 0, "RX_MIXER_MAX": 30,
                     "RX_PREAMP_STEP": 1, "RX_PREAMP_MIN": 0, "RX_PREAMP_MAX": 10},
        })

class Odd(Device):
    """Minimal device using stage names no existing driver has"""
    NAME = "Odd"
    CONFIG = ConfigOdd()
    SAMPLE_FORMAT = convert.INT12_PACKED

    def __init__(self):
        super().__init__()
        self.calls = []
    def _open(self): return object()
    def _close(self): pass
    def _start_rx(self): pass
    def _stop_rx(self): pass
    def _get_samples(self): return np.zeros(0, np.complex64)
    def _set_rx_gain(self, gain):
        self.calls.append(("total", gain))
        return gain

    def set_rx_mixer_gain(self, gain=None):
        return self._set_gain_stage("RX", "MIXER", gain, lambda g: self.calls.append(("mixer", g)))
    def set_rx_preamp_gain(self, gain=None):
        return self._set_gain_stage("RX", "PREAMP", gain, lambda g: self.calls.append(("preamp", g)))
    def set_rx_nonexistent_gain(self, gain=None):
        return self._set_gain_stage("RX", "NOPE", gain, lambda g: None)


@pytest.fixture
def odd():
    dev = Odd()
    dev.open()
    return dev


class TestGenericConfig:
    def test_stages_are_whatever_the_device_declares(self):
        cfg = ConfigOdd()
        assert set(cfg.stages["RX"]) == {"MIXER", "PREAMP"} and cfg.stages["TX"] == {}
        assert cfg.stage("RX", "mixer").max == 30
        assert cfg.stage("RX", "LNA") is None and cfg.stage("TX", "MIXER") is None

    def test_totals_sum_the_stages(self):
        cfg = ConfigOdd()
        assert (cfg.GAIN_RX_MIN, cfg.GAIN_RX_MAX, cfg.DEFAULT_GAIN_RX) == (0, 40, 5)
        assert (cfg.GAIN_TX_MIN, cfg.GAIN_TX_MAX, cfg.DEFAULT_GAIN_TX) == (0, 0, 0)

    def test_attribute_style_access_resolves_any_stage_name(self):
        cfg = ConfigOdd()
        assert cfg.GAIN_RX_MIXER is True and cfg.GAIN_RX_MIXER_STEP == 5 and cfg.GAIN_RX_MIXER_MAX == 30
        assert cfg.DEFAULT_GAIN_RX_MIXER == 5 and cfg.DEFAULT_GAIN_RX_PREAMP == 0
        assert cfg.GAIN_RX_LNA is False and cfg.GAIN_RX_LNA_MAX is None and cfg.DEFAULT_GAIN_RX_LNA is None

    def test_unknown_attribute_still_raises(self):
        with pytest.raises(AttributeError):
            ConfigOdd().NOT_A_THING
        with pytest.raises(AttributeError):
            ConfigOdd()._private

    def test_json(self):
        j = ConfigOdd().json()
        assert j["GAIN"]["RX_MIXER_MAX"] == 30 and j["GAIN"]["RX_MIXER"] is True
        assert j["DEFAULT"]["GAIN_RX_PREAMP"] == 0 and j["FREQ_MAX"] == 10_000_000

    def test_incomplete_stage_is_ignored(self):
        class Partial(ConfigDevice):
            __instance = None
            def __init__(self):
                super().__init__({"DEFAULT": {}, "GAIN": {"RX_X_MIN": 0, "RX_X_MAX": 5}})
        assert Partial().stage("RX", "X") is None

    def test_empty_config(self):
        class Empty(ConfigDevice):
            __instance = None
            def __init__(self):
                super().__init__({"DEFAULT": {}, "GAIN": {}})
        cfg = Empty()
        assert cfg.stages == {"RX": {}, "TX": {}} and cfg.GAIN_RX_MAX == 0


class TestGenericDevice:
    def test_stage_setter_validates_against_declared_limits(self, odd):
        with pytest.warns(Warning):
            assert odd.set_rx_mixer_gain(32) == 30      # clamped to max
        with pytest.warns(Warning):
            assert odd.set_rx_mixer_gain(7) == 5        # rounded down to the step
        assert odd.calls == [("mixer", 30), ("mixer", 5)]

    def test_stage_default(self, odd):
        assert odd.set_rx_mixer_gain() == 5
        assert odd.set_rx_preamp_gain() == 0

    def test_missing_stage(self, odd):
        with pytest.raises(NotImplementedError, match="NOPE"):
            odd.set_rx_nonexistent_gain(1)

    def test_stage_requires_open(self):
        with pytest.raises(err.NotOpen):
            Odd().set_rx_mixer_gain(5)

    def test_generic_total_uses_overall_limits(self, odd):
        with pytest.warns(Warning):
            odd.set_rx_gain(99)
        assert odd.calls[-1] == ("total", 40)
        odd.set_rx_gain()
        assert odd.calls[-1] == ("total", "default")

    def test_receive_only_initialize(self, odd):
        odd.initialize()   # no TX: must not call set_tx_gain
        assert odd.get_freq() == 1_000_000

    def test_sample_format_is_metadata(self):
        assert Odd.SAMPLE_FORMAT is convert.INT12_PACKED
        assert Device.SAMPLE_FORMAT is None
