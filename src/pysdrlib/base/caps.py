"""What a device can do: tuning and rate ranges, gain stages, filters, antennas, settings

`Device.caps` is a `Capabilities`. Before `open()` it describes the device class (from the class's CAPS); after, the
driver refines it for the unit that is attached (a tuner's real gain steps, the rates its firmware offers...).

    caps = sdr.caps
    caps.freq                 # [Range(lo, hi)]: tunable ranges
    caps.rates                # [Range]: a Range with lo == hi is one discrete rate
    caps.gains["rx"]          # {stage name: GainStage}, names as the device calls them
    caps.settings             # {name: Setting}: the device's own controls (sdr.set_setting(name, value))
"""
import copy
import math
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

DIRECTIONS = ("rx", "tx")

@dataclass(frozen=True)
class GainStage:
    """One adjustable gain element of a device (its name is whatever the device calls it), in dB

    step: spacing of its values (0: continuous). values: the gains it really has, when they are uneven steps (a
    tuner's table); set gains snap to the nearest. default: what `set_rx_gain()` with no value sets.
    """
    step: float
    min: float
    max: float
    default: Optional[float] = None
    values: Optional[tuple] = None

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

def at_least(ranges, value):
    """The smallest value of `ranges` that is `value` or more (a step is honoured: the next step up), None when they all stop short of it"""
    best = None
    for r in ranges:
        if value > r.hi:
            continue
        v = max(value, r.lo)
        if r.step:
            v = r.lo + math.ceil((v - r.lo) / r.step - 1e-9) * r.step
            if v > r.hi:
                continue
        if best is None or v < best:
            best = v
    return best

def contains(ranges, value):
    return any(value in r for r in ranges)

def _ranges_json(rs):
    return [[r.lo, r.hi, r.step] for r in rs]

@dataclass(frozen=True)
class Chain:
    """Hardware between a radio and the air that moves frequencies (an LNB, an up/downconverter, a mixer + power amplifier)
    and the antenna on it. Frequencies a program asks for are over the air ("RF"); the radio tunes "IF".

    direction: "rx" or "tx" (a radio can have a chain of each). port: the radio's antenna port it sits behind (None: the
    one for any port that has no chain of its own). lo: Hz of the external LO (0: none, RF == IF). invert: the LO is above the
    signal, RF = lo - IF and the spectrum arrives mirrored (RF = IF + lo otherwise).
    antenna: the standard of antenna fitted to it (see `pysdrlib.base.antennas`). usable: [Range] of RF Hz it works at at all
    (empty: the antenna's, else whatever the radio's tuning gives). optimal: [Range] of RF Hz it is best at (empty: the antenna's).
    """
    direction: str = "rx"
    port: Optional[str] = None
    antenna: Optional[str] = None
    lo: float = 0.0
    invert: bool = False
    usable: list = field(default_factory=list)
    optimal: list = field(default_factory=list)
    name: str = ""

    def __post_init__(self):
        if self.direction not in DIRECTIONS:
            raise ValueError(f"a chain's direction is rx or tx, not {self.direction!r}")
        if self.invert and not self.lo:
            raise ValueError("an inverting chain needs an LO frequency (lo)")

    @property
    def identity(self):
        return not self.lo

    def usable_ranges(self):
        return self.usable or _standard(self.antenna, "usable")

    def optimal_ranges(self):
        return self.optimal or _standard(self.antenna, "optimal")

    def to_if(self, rf):
        """The radio frequency for an over-the-air one"""
        return self.lo - rf if self.invert else rf - self.lo

    def to_rf(self, if_):
        """The over-the-air frequency for one the radio tunes"""
        return self.lo - if_ if self.invert else if_ + self.lo

    def span_to_rf(self, lo, hi):
        a, b = self.to_rf(lo), self.to_rf(hi)
        return (min(a, b), max(a, b))

    def span_to_if(self, lo, hi):
        a, b = self.to_if(lo), self.to_if(hi)
        return (min(a, b), max(a, b))

    def rf_ranges(self, ranges):
        """`ranges` (what the radio tunes) as the over-the-air ranges they give through this chain, held to what it is usable at"""
        out = []
        for r in ranges:
            lo, hi = self.span_to_rf(r.lo, r.hi)
            out.append(Range(lo, hi, r.step))
        use = self.usable_ranges()
        if not use:
            return out
        return [Range(max(a.lo, u.lo), min(a.hi, u.hi)) for a in out for u in use if max(a.lo, u.lo) <= min(a.hi, u.hi)]

    def json(self):
        return {"direction": self.direction, "port": self.port, "antenna": self.antenna, "lo": self.lo, "invert": self.invert,
                "usable": _ranges_json(self.usable_ranges()), "optimal": _ranges_json(self.optimal_ranges()), "name": self.name}

def _standard(name, what):
    if not name:
        return []
    from . import antennas
    found = antennas.find(name)
    return list(getattr(found, what)) if found else []

def chain_from_json(obj) -> Chain:
    return Chain(direction=obj.get("direction", "rx"), port=obj.get("port"), antenna=obj.get("antenna"), lo=obj.get("lo", 0.0),
                 invert=obj.get("invert", False), usable=[Range(*r) for r in obj.get("usable", [])],
                 optimal=[Range(*r) for r in obj.get("optimal", [])], name=obj.get("name", ""))

@dataclass(frozen=True)
class Setting:
    """A device-specific control. kind: bool, int, float or str; choices: allowed values (None: any of `kind`)

    method: the device method that applies it (called with the value). getter: one that reads it back, if any.
    per_channel: those methods also take `channel=` (the setting is per channel).
    """
    kind: type
    doc: str
    method: str
    choices: Optional[tuple] = None
    getter: Optional[str] = None
    default: Any = None
    per_channel: bool = False

@dataclass
class Capabilities:
    """See the module docstring. Lists are empty when the device has no such control"""
    driver: str
    channels: dict = field(default_factory=lambda: {"rx": 1, "tx": 0})
    # what one setting changes: "channel" (just that one), "direction" (every channel of its direction: one synthesizer
    # or clock per direction), "device" (both directions: one for everything)
    shared: dict = field(default_factory=lambda: {"freq": "device", "rate": "device", "gain": "channel",
                                                  "bandwidth": "channel", "antenna": "channel"})
    freq: list = field(default_factory=list)            # [Range] Hz
    rates: list = field(default_factory=list)           # [Range] samples/s
    default_freq: Optional[float] = None                # what set_rx_freq() with no value sets
    default_rate: Optional[float] = None
    bandwidths: dict = field(default_factory=lambda: {"rx": [], "tx": []})   # [Range] Hz of analog filter
    gains: dict = field(default_factory=lambda: {"rx": {}, "tx": {}})       # {stage: GainStage}
    gain_modes: dict = field(default_factory=lambda: {"rx": ["manual"], "tx": ["manual"]})
    antennas: dict = field(default_factory=lambda: {"rx": [], "tx": []})
    clock_sources: list = field(default_factory=list)
    settings: dict = field(default_factory=dict)        # {name: Setting}
    chains: list = field(default_factory=list)          # [Chain] hardware between the radio and the air (both directions); empty: none
    retune_settle: float = 0.0     # seconds of samples after a retune that may still be settling (see Device.set_rx_freq)
    retune_samples: int = 0        # ... plus samples already in flight from before it (the driver's USB transfers)
    info: dict = field(default_factory=dict)            # serial, hardware revision, firmware... as the driver knows them

    def stage(self, direction, name) -> Optional[GainStage]:
        """The named gain stage of a direction, or None if the device has no such stage"""
        return self.gains[direction.lower()].get(str(name).upper())

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
            "shared": dict(self.shared),
            "freq": ranges(self.freq),
            "rates": ranges(self.rates),
            "default_freq": self.default_freq,
            "default_rate": self.default_rate,
            "bandwidths": {d: ranges(v) for d, v in self.bandwidths.items()},
            "gains": {d: {n: asdict(s) for n, s in st.items()} for d, st in self.gains.items()},
            "gain_modes": {d: list(v) for d, v in self.gain_modes.items()},
            "antennas": {d: list(v) for d, v in self.antennas.items()},
            "clock_sources": list(self.clock_sources),
            "settings": {n: {"kind": s.kind.__name__, "doc": s.doc, "choices": s.choices, "default": s.default,
                         "per_channel": s.per_channel}
                         for n, s in self.settings.items()},
            "chains": [c.json() for c in self.chains],
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
        shared=dict(obj.get("shared", {})) or Capabilities(driver="").shared,
        freq=ranges(obj["freq"]),
        rates=ranges(obj["rates"]),
        default_freq=obj.get("default_freq"),
        default_rate=obj.get("default_rate"),
        bandwidths={d: ranges(v) for d, v in obj["bandwidths"].items()},
        gains={d: {n: GainStage(**{**s, "values": tuple(s["values"]) if s.get("values") else None}) for n, s in st.items()}
               for d, st in obj["gains"].items()},
        gain_modes={d: list(v) for d, v in obj["gain_modes"].items()},
        antennas={d: list(v) for d, v in obj["antennas"].items()},
        clock_sources=list(obj["clock_sources"]),
        settings={n: Setting(_KINDS.get(s["kind"], str), s["doc"], "set_setting",
                             tuple(s["choices"]) if s.get("choices") else None, default=s.get("default"),
                             per_channel=s.get("per_channel", False))
                  for n, s in obj["settings"].items()},
        chains=[chain_from_json(c) for c in obj.get("chains", [])],
        retune_settle=obj.get("retune_settle", 0.0),
        retune_samples=obj.get("retune_samples", 0),
        info=dict(obj.get("info", {})),
    )

def for_class(cls) -> Capabilities:
    """A fresh copy of the capabilities a device class declares in `CAPS` (what is known before opening one)"""
    caps = copy.deepcopy(cls.CAPS) if cls.CAPS is not None else Capabilities(driver="")
    caps.driver = cls.NAME
    if cls.CAN_TRANSMIT and not caps.channels.get("tx"):
        caps.channels["tx"] = 1
    if not cls.CAN_TRANSMIT:
        caps.gains["tx"] = {}
    if cls.DUPLEX:   # separate receive and transmit synthesizers
        caps.shared.update(freq="direction", rate="direction")
    caps.retune_settle = getattr(cls, "RETUNE_SETTLE", 0.0)
    caps.retune_samples = getattr(cls, "RETUNE_SAMPLES", 0)
    return caps
