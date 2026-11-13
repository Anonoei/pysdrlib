"""What sits between each radio of a server and the air: LNBs, converters, antennas

An operator describes it once, in a JSON file (TOML also works) given to `pysdrlib server --frontends FILE`, and clients
just ask for the over-the-air frequency they want (see `choose`, `pysdrlib.base.caps.Chain`):

    {
      "antenna": {                              // antenna standards of your own (ANT500 is built in); usable/optimal: [low, high] Hz
        "MYLPDA": {"optimal": [[430e6, 440e6]],
                   "kind": "lpda",                // whip, dipole, biconical, lpda, yagi, dish, horn, discone, patch, helical, loop, or any text
                   "size": [0.5, 0.9],            // metres: what the kind measures (boom length for lpda, diameter for dish)
                   "impedance": 50}               // ohms
      },
      "radio": [{                               // picked by any of driver, serial and label (a part of it, case ignored); first match wins
        "serial": "0123ABCD",
        "chain": [{
          "direction": "rx",                    // rx or tx
          "port": "RX2",                        // the radio's antenna port it is behind (leave out: any port)
          "antenna": "ANT500",
          "lo": 9.75e9,                         // external LO, Hz (leave out: none): the radio tunes RF - lo, as behind an LNB
          "name": "universal LNB"
          // "invert": true                     // the LO is above the signal instead: radio tunes lo - RF, spectrum mirrored
          // "usable": [[10.7e9, 12.75e9]], "optimal": [[11e9, 12e9]]    // override the antenna's
        }]
      }]
    }

(Comments and `e` notation are for reading here: JSON proper has neither, so write 9750000000 and no comments.) The file is
read as JSON, unless its name ends in `.toml`; one that is not valid JSON is tried as TOML, with the same keys
(`[antenna.MYLPDA]`, `[[radio]]`, `[[radio.chain]]`).
"""
import copy
import json
import tomllib

from ..base import antennas as _antennas
from ..base import caps as _caps

_ANTENNA_KEYS = {"usable", "optimal", "doc", "kind", "size", "length", "impedance"}
_RADIO_KEYS = {"driver", "serial", "label", "chain"}
_CHAIN_KEYS = {"direction", "port", "antenna", "lo", "invert", "usable", "optimal", "name"}


def _check(where, table, allowed):
    unknown = set(table) - allowed
    if unknown:
        raise ValueError(f"{where} has no {', '.join(repr(k) for k in sorted(unknown))} (it takes {', '.join(sorted(allowed))})")


def _ranges(where, value):
    try:
        out = [_caps.Range(float(lo), float(hi)) for lo, hi in value]
    except (TypeError, ValueError):
        raise ValueError(f"{where} is a list of [low, high] pairs of Hz, not {value!r}") from None
    for r in out:
        if not 0 <= r.lo <= r.hi:
            raise ValueError(f"{where} has a pair that is not low <= high: [{r.lo:g}, {r.hi:g}]")
    return out


class Frontends:
    """The chains each radio has, by what picks the radio out. `radios`: [({driver/serial/label: text}, [Chain])]"""

    def __init__(self, radios=None):
        self.radios = list(radios or [])

    def chains_for(self, driver, serial=None, label=None):
        """The chains of the first radio entry that matches this radio ([] when none does)"""
        for match, chains in self.radios:
            if "driver" in match and match["driver"] != driver:
                continue
            if "serial" in match and str(serial) != str(match["serial"]):
                continue
            if "label" in match and (label is None or match["label"].lower() not in str(label).lower()):
                continue
            return chains
        return []

    def apply(self, caps, driver, serial=None, label=None):
        """`caps` with the chains this radio has (itself, if it has none)"""
        chains = self.chains_for(driver, serial, label)
        if caps is None or not chains:
            return caps
        out = copy.copy(caps)
        out.chains = list(chains)
        return out


def parse(data) -> Frontends:
    """A `Frontends` from what `json` or `tomllib` read (registers the antennas it defines). Raises ValueError saying what is wrong"""
    _check("the file", data, {"antenna", "radio"})
    for name, table in (data.get("antenna") or {}).items():
        _check(f"[antenna.{name}]", table, _ANTENNA_KEYS)
        if "size" in table and "length" in table:
            raise ValueError(f"[antenna.{name}] has both 'size' and its old name 'length': keep one")
        size = table.get("size", table.get("length"))
        try:
            _antennas.register(_antennas.Antenna(
                name, usable=_ranges(f"antenna.{name}.usable", table.get("usable", [])),
                optimal=_ranges(f"antenna.{name}.optimal", table.get("optimal", [])), doc=str(table.get("doc", "")),
                kind=str(table["kind"]) if "kind" in table else None,
                size=tuple(float(v) for v in size) if size is not None else None,
                impedance=float(table["impedance"]) if "impedance" in table else None), replace=True)
        except (TypeError, ValueError) as e:
            raise ValueError(f"[antenna.{name}]: {e}") from None
    radios = []
    for i, radio in enumerate(data.get("radio") or []):
        where = f"[[radio]] #{i + 1}"
        _check(where, radio, _RADIO_KEYS)
        match = {k: str(radio[k]) for k in ("driver", "serial", "label") if k in radio}
        if not match:
            raise ValueError(f"{where} needs a driver, serial or label to say which radio it is")
        chains = []
        for j, c in enumerate(radio.get("chain") or []):
            cwhere = f"{where} chain #{j + 1}"
            _check(cwhere, c, _CHAIN_KEYS)
            if c.get("antenna") and _antennas.find(c["antenna"]) is None:
                _antennas.get(c["antenna"])    # raises, naming the known ones
            chains.append(_caps.Chain(direction=c.get("direction", "rx"), port=c.get("port"), antenna=c.get("antenna"),
                                      lo=float(c.get("lo", 0.0)), invert=bool(c.get("invert", False)),
                                      usable=_ranges(f"{cwhere} usable", c.get("usable", [])),
                                      optimal=_ranges(f"{cwhere} optimal", c.get("optimal", [])), name=str(c.get("name", ""))))
        radios.append((match, chains))
    return Frontends(radios)


def load(path) -> Frontends:
    """Read a frontends file: JSON, or TOML when the name ends in `.toml` or it is not valid JSON. Raises ValueError (with the
    file's name) when it cannot be read or is wrong"""
    try:
        with open(path, "rb") as f:
            raw = f.read()
        if str(path).lower().endswith(".toml"):
            data = tomllib.loads(raw.decode())
        else:
            try:
                data = json.loads(raw)
            except ValueError as first:      # not JSON: TOML, and if that is not either, say why it is not JSON (the default)
                try:
                    data = tomllib.loads(raw.decode())
                except (tomllib.TOMLDecodeError, UnicodeDecodeError):
                    raise ValueError(f"not valid JSON ({first}), nor TOML") from None
        return parse(data)
    except (OSError, tomllib.TOMLDecodeError, ValueError, UnicodeDecodeError) as e:
        raise ValueError(f"{path}: {e}") from None
