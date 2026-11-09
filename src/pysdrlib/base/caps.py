"""What a device can do: tuning and rate ranges, gain stages, filters, antennas, settings

`Device.caps` is a `Capabilities`. Before `open()` it describes the device class (from its CONFIG); after, the
driver refines it for the unit that is attached (a tuner's real gain steps, the rates its firmware offers...).

    caps = sdr.caps
    caps.freq                 # [Range(lo, hi)]: tunable ranges
    caps.rates                # [Range]: a Range with lo == hi is one discrete rate
    caps.gains["rx"]          # {stage name: GainStage}, names as the device calls them
    caps.settings             # {name: Setting}: the device's own controls (sdr.set_setting(name, value))
"""
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from .config import GainStage

DIRECTIONS = ("rx", "tx")

@dataclass(frozen=True)
class Range:
    """A closed interval [lo, hi] (lo == hi: one value). step: spacing of the values in it (0: continuous)"""
    lo: float
    hi: float
    step: float = 0

    def __contains__(self, value):
        return self.lo <= value <= self.hi

    def clip(self, value):
        return min(max(value, self.lo), self.hi)

def nearest(ranges, value):
    """The value closest to `value` that one of `ranges` allows (a step is honoured)"""
    best, best_d = None, None
    for r in ranges:
        v = r.clip(value)
        if r.step:
            v = r.lo + round((v - r.lo) / r.step) * r.step
            v = r.clip(v)
        d = abs(v - value)
        if best_d is None or d < best_d:
            best, best_d = v, d
    return value if best is None else best

def contains(ranges, value):
    return any(value in r for r in ranges)

@dataclass(frozen=True)
class Setting:
    """A device-specific control. kind: bool, int, float or str; choices: allowed values (None: any of `kind`)

    method: the device method that applies it (called with the value). getter: one that reads it back, if any.
    """
    kind: type
    doc: str
    method: str
    choices: Optional[tuple] = None
    getter: Optional[str] = None
    default: Any = None

@dataclass
class Capabilities:
    """See the module docstring. Lists are empty when the device has no such control"""
    driver: str
    channels: dict = field(default_factory=lambda: {"rx": 1, "tx": 0})
    freq: list = field(default_factory=list)            # [Range] Hz
    rates: list = field(default_factory=list)           # [Range] samples/s
    bandwidths: dict = field(default_factory=lambda: {"rx": [], "tx": []})   # [Range] Hz of analog filter
    gains: dict = field(default_factory=lambda: {"rx": {}, "tx": {}})       # {stage: GainStage}
    gain_modes: dict = field(default_factory=lambda: {"rx": ["manual"], "tx": ["manual"]})
    antennas: dict = field(default_factory=lambda: {"rx": [], "tx": []})
    clock_sources: list = field(default_factory=list)
    settings: dict = field(default_factory=dict)        # {name: Setting}
    retune_settle: float = 0.0     # seconds of samples after a retune that may still be settling (see Device.set_freq)
    retune_samples: int = 0        # ... plus samples already in flight from before it (the driver's USB transfers)
    info: dict = field(default_factory=dict)            # serial, hardware revision, firmware... as the driver knows them

    def gain_range(self, direction="rx"):
        """Range of the total gain across a direction's stages"""
        stages = self.gains[direction].values()
        return Range(sum(s.min for s in stages), sum(s.max for s in stages))

    def json(self):
        """Plain types only (for the remote protocol, logs, UIs)"""
        def ranges(rs):
            return [[r.lo, r.hi, r.step] for r in rs]
        return {
            "driver": self.driver,
            "channels": dict(self.channels),
            "freq": ranges(self.freq),
            "rates": ranges(self.rates),
            "bandwidths": {d: ranges(v) for d, v in self.bandwidths.items()},
            "gains": {d: {n: asdict(s) for n, s in st.items()} for d, st in self.gains.items()},
            "gain_modes": {d: list(v) for d, v in self.gain_modes.items()},
            "antennas": {d: list(v) for d, v in self.antennas.items()},
            "clock_sources": list(self.clock_sources),
            "settings": {n: {"kind": s.kind.__name__, "doc": s.doc, "choices": s.choices, "default": s.default}
                         for n, s in self.settings.items()},
            "retune_settle": self.retune_settle,
            "retune_samples": self.retune_samples,
            "info": dict(self.info),
        }

_KINDS = {"bool": bool, "int": int, "float": float, "str": str}

def from_json(obj) -> Capabilities:
    """The inverse of Capabilities.json() (a remote device's capabilities)"""
    def ranges(rs):
        return [Range(*r) for r in rs]
    return Capabilities(
        driver=obj["driver"],
        channels=dict(obj["channels"]),
        freq=ranges(obj["freq"]),
        rates=ranges(obj["rates"]),
        bandwidths={d: ranges(v) for d, v in obj["bandwidths"].items()},
        gains={d: {n: GainStage(**{**s, "values": tuple(s["values"]) if s.get("values") else None}) for n, s in st.items()}
               for d, st in obj["gains"].items()},
        gain_modes={d: list(v) for d, v in obj["gain_modes"].items()},
        antennas={d: list(v) for d, v in obj["antennas"].items()},
        clock_sources=list(obj["clock_sources"]),
        settings={n: Setting(_KINDS.get(s["kind"], str), s["doc"], "set_setting",
                             tuple(s["choices"]) if s.get("choices") else None, default=s.get("default"))
                  for n, s in obj["settings"].items()},
        retune_settle=obj.get("retune_settle", 0.0),
        retune_samples=obj.get("retune_samples", 0),
        info=dict(obj.get("info", {})),
    )

def from_config(cls) -> Capabilities:
    """The capabilities a device class declares through its CONFIG (what is known before opening one)"""
    config = cls.CONFIG
    caps = Capabilities(driver=cls.NAME)
    caps.channels = {"rx": 1, "tx": 1 if cls.CAN_TRANSMIT else 0}
    if config is None:
        return caps
    if config.FREQ_MIN is not None:
        caps.freq = [Range(config.FREQ_MIN, config.FREQ_MAX)]
    if config.SAMPLE_RATE_MIN is not None:
        caps.rates = [Range(config.SAMPLE_RATE_MIN, config.SAMPLE_RATE_MAX)]
    caps.gains = {"rx": dict(config.stages["RX"]), "tx": dict(config.stages["TX"]) if cls.CAN_TRANSMIT else {}}
    caps.retune_settle = getattr(cls, "RETUNE_SETTLE", 0.0)
    caps.retune_samples = getattr(cls, "RETUNE_SAMPLES", 0)
    return caps
