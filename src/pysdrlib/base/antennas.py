"""Standards of antenna, by name: the band each works at and the band it is best at

A `Chain` (see `pysdrlib.base.caps`) names the antenna fitted to a radio instead of repeating its ranges, and selection
(`pysdrlib.remote.choose`) prefers a radio whose antenna is optimal at the frequency a program wants.

    from pysdrlib.base import antennas
    antennas.get("ANT500").optimal      # [Range(75e6, 1e9)]
    antennas.register(antennas.Antenna("MYDISH", kind="dish", size=(0.6, 0.6), impedance=50, optimal=[Range(10e9, 12e9)]))
    antennas.find_kind("lpda").measures # "boom length"
"""
from dataclasses import dataclass, field
from typing import Optional

from .caps import Range


@dataclass(frozen=True)
class Kind:
    """A type of antenna. key: its short id. name: how it is shown. measures: what an antenna's `size` is for this type"""
    key: str
    name: str
    measures: str = "size"


KINDS = {}
_KIND_ALIASES = {}


def register_kind(kind, replace=False):
    """Make `kind` known (by its key and its name, case ignored). Raises ValueError for a key that is taken, unless `replace`"""
    key = kind.key.strip().lower()
    if not key:
        raise ValueError("a kind of antenna needs a key")
    if key in KINDS and not replace:
        raise ValueError(f"kind of antenna {kind.key!r} is already registered")
    KINDS[key] = kind
    return kind


def find_kind(text):
    """The `Kind` whose key or name is `text` (case ignored), or None"""
    if text is None:
        return None
    text = " ".join(str(text).lower().split())
    text = _KIND_ALIASES.get(text, text)
    if text in KINDS:
        return KINDS[text]
    return next((k for k in KINDS.values() if k.name.lower() == text), None)


for _kind in (Kind("whip", "Whip / monopole", "length"), Kind("dipole", "Dipole", "length"),
              Kind("biconical", "UWB - extremely wide bandwidth biconical", "length"),
              Kind("lpda", "Log-periodic dipole array", "boom length"), Kind("yagi", "Yagi-Uda", "boom length"),
              Kind("dish", "Parabolic dish", "diameter"), Kind("horn", "Horn", "aperture"),
              Kind("discone", "Discone", "diameter"), Kind("patch", "Patch", "width"),
              Kind("helical", "Helical", "length"), Kind("loop", "Loop", "diameter")):
    register_kind(_kind)
del _kind


@dataclass(frozen=True)
class Antenna:
    """name: how a chain refers to it. usable: [Range] of Hz it works at at all (empty: no limit known).
    optimal: [Range] of Hz it is best at (empty: none declared, so it is never preferred or avoided).
    kind: what type it is: a key or name from `KINDS`, or any text for one that is not there. size: (smallest, largest) in metres
    of what that type measures (length of a whip, diameter of a dish; `Kind.measures`), the same twice for a fixed one.
    impedance: ohms"""
    name: str
    usable: list = field(default_factory=list)
    optimal: list = field(default_factory=list)
    doc: str = ""
    kind: Optional[str] = None
    size: Optional[tuple] = None
    impedance: Optional[float] = None

    def __post_init__(self):
        if self.size is not None:
            try:
                lo, hi = self.size
                ok = 0 < lo <= hi
            except (TypeError, ValueError):
                ok = False
            if not ok:
                raise ValueError(f"antenna {self.name!r}: size is (smallest, largest) in metres, not {self.size!r}")
        if self.impedance is not None and not self.impedance > 0:
            raise ValueError(f"antenna {self.name!r}: impedance is positive ohms, not {self.impedance!r}")

    def describe(self) -> dict:
        """Plain types only: what is known about the antenna, for clients and `list`"""
        kind = find_kind(self.kind)
        return {"name": self.name, "kind": kind.key if kind else self.kind, "kind_name": kind.name if kind else self.kind,
                "size": list(self.size) if self.size else None, "measures": kind.measures if kind else "size",
                "impedance": self.impedance, "doc": self.doc}


ANTENNAS = {}


def _key(name):
    return str(name).strip().upper()


def register(antenna, replace=False):
    """Make `antenna` known by its name (case ignored). Raises ValueError for a name that is taken, unless `replace`"""
    key = _key(antenna.name)
    if not key:
        raise ValueError("an antenna needs a name")
    if key in ANTENNAS and not replace:
        raise ValueError(f"antenna {antenna.name!r} is already registered")
    ANTENNAS[key] = antenna
    return antenna


def find(name):
    """The antenna called `name`, or None"""
    return ANTENNAS.get(_key(name))


def get(name):
    """The antenna called `name`. Raises ValueError listing the known ones when there is none"""
    found = find(name)
    if found is None:
        raise ValueError(f"no antenna {name!r} (known: {', '.join(a.name for a in ANTENNAS.values()) or 'none'})")
    return found


register(Antenna("ANT500", optimal=[Range(75e6, 1e9)], kind="whip", size=(0.20, 0.88), impedance=50.0,
                 doc="telescopic whip, 20 cm to 88 cm, 50 ohm general purpose antenna designed for 75 MHz to 1 GHz"))
