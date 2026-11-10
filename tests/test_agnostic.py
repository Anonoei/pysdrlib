"""pysdrlib's generic code must not know about any one device.

Device-specific names (gain stages, tuning stages, sample layouts) belong in that device's package.
"""
import re
from pathlib import Path

import numpy as np
import pytest

from pysdrlib import err
from pysdrlib.base import convert
from pysdrlib.base.caps import Capabilities, GainStage, Range
from pysdrlib.base.device import Device

SRC = Path(__file__).resolve().parents[1] / "src" / "pysdrlib"
GENERIC = [*(SRC / "base").glob("*.py"), *(SRC / "file").glob("*.py"),
           *SRC.glob("*.py"), *(SRC / "_native" / "include").glob("hardware_*.hpp")]

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
                hits.append(f"{path.relative_to(SRC).as_posix()}:{n}: {line.strip()}")
    assert not hits, "\n".join(hits)


def test_device_base_class_surface():
    public = {n for n in dir(Device) if not n.startswith("_")}
    assert {"open", "close", "initialize", "start_rx", "stop_rx", "get_samples", "set_rx_freq", "set_tx_freq", "set_rx_sample_rate",
            "set_tx_sample_rate",
            "set_rx_gain", "set_tx_gain", "set_bias_t"} <= public
    assert not {n for n in public if re.search(r"(rf|if|bb|cf|lo)_(gain|freq)|freq_(cf|if|lo)", n)}


# --- a device with no connection to any existing one -------------------------------------------
ODD = Capabilities(
    driver="", freq=[Range(1, 10_000_000)], default_freq=1_000_000, rates=[Range(1, 4_000_000)], default_rate=2_000_000,
    gains={"rx": {"MIXER": GainStage(5, 0, 30, 5), "PREAMP": GainStage(1, 0, 10, 0)}, "tx": {}},
)

class Odd(Device):
    """Minimal device using stage names no existing driver has"""
    NAME = "Odd"
    CAPS = ODD
    SAMPLE_FORMAT = convert.INT12_PACKED

    def __init__(self):
        super().__init__()
        self.calls = []
    def _open(self): return object()
    def _close(self): pass
    def _start_rx(self): pass
    def _stop_rx(self): pass
    def _get_samples(self): return np.zeros(0, np.complex64)
    def _set_rx_gain(self, gain, channel=0):
        self.calls.append(("total", gain))
        return gain

    def set_rx_mixer_gain(self, gain=None):
        return self._set_gain_stage("RX", "MIXER", gain, lambda g, channel=0: self.calls.append(("mixer", g)))
    def set_rx_preamp_gain(self, gain=None):
        return self._set_gain_stage("RX", "PREAMP", gain, lambda g, channel=0: self.calls.append(("preamp", g)))
    def set_rx_nonexistent_gain(self, gain=None):
        return self._set_gain_stage("RX", "NOPE", gain, lambda g, channel=0: None)


@pytest.fixture
def odd():
    dev = Odd()
    dev.open()
    return dev


class TestGenericCaps:
    def test_stages_are_whatever_the_device_declares(self):
        caps = Odd.CAPS
        assert set(caps.gains["rx"]) == {"MIXER", "PREAMP"} and caps.gains["tx"] == {}
        assert caps.stage("rx", "mixer").max == 30
        assert caps.stage("rx", "LNA") is None and caps.stage("tx", "MIXER") is None

    def test_totals_sum_the_stages(self):
        caps = Odd.CAPS
        r = caps.gain_range("rx")
        assert (r.lo, r.hi, sum(s.default for s in caps.gains["rx"].values())) == (0, 40, 5)
        assert caps.gain_range("tx") == Range(0, 0)

    def test_json_round_trip(self):
        from pysdrlib.base import caps as _caps
        caps = Odd().caps
        back = _caps.from_json(caps.json())
        assert back.stage("rx", "MIXER") == caps.stage("rx", "MIXER")
        assert back.default_freq == 1_000_000 and back.freq == caps.freq

    def test_a_device_gets_its_own_copy(self):
        a, b = Odd().caps, Odd().caps
        a.gains["rx"].pop("MIXER")
        assert "MIXER" in b.gains["rx"] and "MIXER" in Odd.CAPS.gains["rx"]


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
            odd.set_rx_gain_db(99)
        assert odd.calls[-1] == ("total", 40)
        odd.set_rx_gain_db()
        assert odd.calls[-1] == ("total", "default")

    def test_receive_only_initialize(self, odd):
        odd.initialize()   # no TX: must not call set_tx_gain
        assert odd.get_rx_freq() == 1_000_000

    def test_sample_format_is_metadata(self):
        assert Odd.SAMPLE_FORMAT is convert.INT12_PACKED
        assert Device.SAMPLE_FORMAT is None
