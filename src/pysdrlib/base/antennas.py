"""Standards of antenna, by name: the band each works at and the band it is best at

A `Chain` (see `pysdrlib.base.caps`) names the antenna fitted to a radio instead of repeating its ranges, and selection
(`pysdrlib.remote.choose`) prefers a radio whose antenna is optimal at the frequency a program wants.

    from pysdrlib.base import antennas
    antennas.get("ANT500").optimal      # [Range(75e6, 1e9)]
    antennas.register(antennas.Antenna("MYWHIP", optimal=[Range(430e6, 440e6)]))
"""
from dataclasses import dataclass, field

from .caps import Range


@dataclass(frozen=True)
class Antenna:
    """name: how a chain refers to it. usable: [Range] of Hz it works at at all (empty: no limit known).
    optimal: [Range] of Hz it is best at (empty: none declared, so it is never preferred or avoided)"""
    name: str
    usable: list = field(default_factory=list)
    optimal: list = field(default_factory=list)
    doc: str = ""


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


register(Antenna("ANT500", optimal=[Range(75e6, 1e9)], doc="telescopic whip, best from 75 MHz to 1 GHz"))
