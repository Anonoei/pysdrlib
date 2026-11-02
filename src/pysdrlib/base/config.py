import re
from dataclasses import dataclass
from typing import Optional

from .. import logger

@dataclass(frozen=True)
class GainStage:
    """One adjustable gain element of a device (its name is whatever the device calls it)"""
    step: int
    min: int
    max: int
    default: Optional[int] = None

_STAGE_KEY = re.compile(r"^(RX|TX)_(.+)_(STEP|MIN|MAX)$")
_ATTR = re.compile(r"^(DEFAULT_)?GAIN_(RX|TX)_([A-Z0-9_]+?)(?:_(STEP|MIN|MAX))?$")

class ConfigDevice:
    """Device limits and defaults.

    Generic: a device declares its own gain stages by name; nothing here knows what they are.

        ConfigDevice({
            "DEFAULT": {"FREQ": ..., "SAMPLE_RATE": ..., "GAIN_RX_MIXER": 16},
            "FREQ_MIN": ..., "FREQ_MAX": ..., "SAMPLE_RATE_MIN": ..., "SAMPLE_RATE_MAX": ...,
            "GAIN": {"RX_MIXER_STEP": 8, "RX_MIXER_MIN": 0, "RX_MIXER_MAX": 40},   # <DIR>_<STAGE>_<STEP|MIN|MAX>
        })

    Per stage: `stage("RX", "MIXER")` -> GainStage, or the attributes GAIN_RX_MIXER (bool: exists),
    GAIN_RX_MIXER_STEP / _MIN / _MAX and DEFAULT_GAIN_RX_MIXER. Totals across the stages of a direction:
    GAIN_RX_MIN, GAIN_RX_MAX, DEFAULT_GAIN_RX (and the TX equivalents).
    """
    __instance = None

    def __new__(cls, *args, **kwargs):
        if cls.__instance is None:
            cls.__instance = super().__new__(cls)
            cls.__instance.__init__(*args, **kwargs)
        return cls.__instance

    def __init__(self, sets):
        self._log = logger.new(f"device.{type(self).__name__}")
        self.FREQ_MIN = sets.get("FREQ_MIN")
        self.FREQ_MAX = sets.get("FREQ_MAX")
        self.SAMPLE_RATE_MIN = sets.get("SAMPLE_RATE_MIN")
        self.SAMPLE_RATE_MAX = sets.get("SAMPLE_RATE_MAX")

        default = sets.get("DEFAULT", {})
        self.DEFAULT_FREQ = default.get("FREQ")
        self.DEFAULT_SAMPLE_RATE = default.get("SAMPLE_RATE")

        # {"RX": {stage name: GainStage}, "TX": {...}}
        self.stages = {"RX": {}, "TX": {}}
        raw = {}
        for key, value in sets.get("GAIN", {}).items():
            m = _STAGE_KEY.match(key)
            if m:
                raw.setdefault((m.group(1), m.group(2)), {})[m.group(3)] = value
        for (direction, name), v in raw.items():
            if None in (v.get("STEP"), v.get("MIN"), v.get("MAX")):
                continue  # incomplete stage: not present
            self.stages[direction][name] = GainStage(
                v["STEP"], v["MIN"], v["MAX"], default.get(f"GAIN_{direction}_{name}"))

        for direction in ("RX", "TX"):
            stages = self.stages[direction].values()
            setattr(self, f"GAIN_{direction}_MIN", sum(s.min for s in stages))
            setattr(self, f"GAIN_{direction}_MAX", sum(s.max for s in stages))
            setattr(self, f"DEFAULT_GAIN_{direction}", sum(s.default or 0 for s in stages))

    def stage(self, direction, name) -> Optional[GainStage]:
        """The named gain stage ("RX"/"TX", stage name), or None if the device has no such stage"""
        return self.stages[direction.upper()].get(name.upper())

    def __getattr__(self, name):
        # Only reached for attributes that don't exist: resolve GAIN_<DIR>_<STAGE>[_STEP|_MIN|_MAX]
        # and DEFAULT_GAIN_<DIR>_<STAGE> from the declared stages.
        m = _ATTR.match(name) if not name.startswith("_") else None
        if m is None or "stages" not in self.__dict__:
            raise AttributeError(name)
        is_default, direction, stage_name, field = m.groups()
        stage = self.stages[direction].get(stage_name)
        if is_default:
            return stage.default if stage and not field else None
        if field is None:
            return stage is not None
        return getattr(stage, field.lower()) if stage else None

    def json(self):
        rtr = {
            "FREQ_MIN": self.FREQ_MIN, "FREQ_MAX": self.FREQ_MAX,
            "SAMPLE_RATE_MIN": self.SAMPLE_RATE_MIN, "SAMPLE_RATE_MAX": self.SAMPLE_RATE_MAX,
            "DEFAULT": {"FREQ": self.DEFAULT_FREQ, "SAMPLE_RATE": self.DEFAULT_SAMPLE_RATE},
            "GAIN": {},
        }
        for direction in ("RX", "TX"):
            rtr["GAIN"][f"{direction}_MIN"] = getattr(self, f"GAIN_{direction}_MIN")
            rtr["GAIN"][f"{direction}_MAX"] = getattr(self, f"GAIN_{direction}_MAX")
            rtr["DEFAULT"][f"GAIN_{direction}"] = getattr(self, f"DEFAULT_GAIN_{direction}")
            for name, s in self.stages[direction].items():
                rtr["GAIN"][f"{direction}_{name}"] = True
                rtr["GAIN"][f"{direction}_{name}_STEP"] = s.step
                rtr["GAIN"][f"{direction}_{name}_MIN"] = s.min
                rtr["GAIN"][f"{direction}_{name}_MAX"] = s.max
                rtr["DEFAULT"][f"GAIN_{direction}_{name}"] = s.default
        return rtr
