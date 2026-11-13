"""Choosing a radio by what a program needs, without knowing which radios a server has.

A client says what it needs (`need`: below this it is of no use) and what it would like (`want`):

    pysdrlib.connect(addr, program="pyspecan", need={"freq": 433e6, "rate": 2e6}, want={"freq": 433.92e6, "rate": 10e6})

Both are dicts with any of these keys:

    freq        Hz to tune to, or a (low, high) pair of Hz the radio has to be able to tune across without a gap
    rate        samples/s
    bandwidth   Hz of spectrum seen (the analog filter where the radio has one, else its sample rate)
    channels    how many channels
    tx          True if it must be able to transmit

A radio is a candidate when it meets all of `need`. Of the candidates the one that misses the fewest keys of `want`, and
then comes nearest to it, wins; a radio whose capabilities are unknown comes after every one whose are known. A `freq` pair
means the radio can tune across the whole span (retuning between its ends), not that it sees all of it at once: that is
`bandwidth`. More rate or bandwidth than `want` asks for is no miss (only a small cost); less is. What the radio can actually
do nearest to `want` is reported back as `granted`.

This module is pure functions of `pysdrlib.base.caps.Capabilities`: no sockets, no hardware.
"""
import math

from ..base import caps as _caps

FIELDS = ("freq", "rate", "bandwidth", "channels", "tx")


def _number(what, key, value):
    try:
        if isinstance(value, bool):
            raise TypeError
        out = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{what}[{key!r}] is a number, not {value!r}") from None
    if not math.isfinite(out):
        raise ValueError(f"{what}[{key!r}] must be finite, not {value!r}")
    return out


_BOOLS = {"true": True, "yes": True, "on": True, "1": True, "false": False, "no": False, "off": False, "0": False}


def _bool(what, key, value):
    if isinstance(value, str):
        if value.strip().lower() in _BOOLS:
            return _BOOLS[value.strip().lower()]
    elif isinstance(value, (bool, int)) and value in (0, 1):
        return bool(value)
    raise ValueError(f"{what}[{key!r}] is true or false, not {value!r}")


def normalize(spec, what="need"):
    """`spec` as a clean dict (None -> {}); raises ValueError for a key or value that makes no sense"""
    if not spec:
        return {}
    if not isinstance(spec, dict):
        raise ValueError(f"{what} is a dict of {', '.join(FIELDS)}, not {type(spec).__name__}")
    out = {}
    for key, value in spec.items():
        if key not in FIELDS:
            raise ValueError(f"{what} has no {key!r} (it takes {', '.join(FIELDS)})")
        if key == "tx":
            out[key] = _bool(what, key, value)
        elif key == "freq" and isinstance(value, (list, tuple)):
            if len(value) != 2:
                raise ValueError(f"{what}['freq'] is a Hz number or a (low, high) pair, not {value!r}")
            lo, hi = _number(what, key, value[0]), _number(what, key, value[1])
            if not 0 <= lo <= hi:
                raise ValueError(f"{what}['freq'] is a Hz number or a (low, high) pair, not {value!r}")
            out[key] = [lo, hi]
        else:
            num = _number(what, key, value)
            if num < 0 or (key == "channels" and num < 1):
                raise ValueError(f"{what}[{key!r}] must be positive, not {value!r}")
            if key == "channels":
                if num != int(num):
                    raise ValueError(f"{what}['channels'] is a whole number, not {value!r}")
                num = int(num)
            out[key] = num
    return out


def _span(value):
    return (value, value) if not isinstance(value, (list, tuple)) else (value[0], value[1])


def _fmt(hz):
    for unit, div in (("GHz", 1e9), ("MHz", 1e6), ("kHz", 1e3)):
        if abs(hz) >= div:
            return f"{hz / div:g} {unit}"
    return f"{hz:g} Hz"


def _merged(ranges):
    """`ranges` as sorted (lo, hi) with those that touch or overlap joined (a span can cross where one ends and the next begins)"""
    out = []
    for lo, hi in sorted((r.lo, r.hi) for r in ranges):
        if out and lo <= out[-1][1]:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return [tuple(r) for r in out]


def _freq_ok(caps, value):
    lo, hi = _span(value)
    return not caps.freq or any(a <= lo and hi <= b for a, b in _merged(caps.freq))


def _freq_granted(caps, value):
    """The span (or frequency) nearest `value` that fits one stretch of what the radio tunes"""
    lo, hi = _span(value)
    if not caps.freq:
        return lo if lo == hi else [lo, hi]
    best = None
    for a, b in _merged(caps.freq):
        g = (min(max(lo, a), b), min(max(hi, a), b))
        cost = abs(g[0] - lo) + abs(g[1] - hi)
        if best is None or cost < best[0]:
            best = (cost, g)
    g = best[1]
    if lo == hi:
        return _caps.nearest(caps.freq, lo)
    return [g[0], g[1]]


def _widest(ranges):
    return max((r.hi for r in ranges), default=None)


def _bandwidth_limit(caps):
    """Most spectrum the radio can see: its widest analog filter, or failing that its top sample rate (None: unknown)"""
    return _widest(caps.bandwidths.get("rx", [])) or _widest(caps.rates)


def problems(caps, spec) -> list:
    """What of `spec` the radio described by `caps` cannot do, as sentences (empty: it meets all of it).
    A limit the radio does not declare is taken as met."""
    spec = normalize(spec)
    out = []
    if "freq" in spec and not _freq_ok(caps, spec["freq"]):
        lo, hi = _span(spec["freq"])
        wanted = _fmt(lo) if lo == hi else f"{_fmt(lo)}-{_fmt(hi)}"
        have = ", ".join(f"{_fmt(r.lo)}-{_fmt(r.hi)}" for r in caps.freq)
        out.append(f"cannot tune {wanted} (tunes {have})")
    if "rate" in spec:
        top = _widest(caps.rates)
        if top is not None and spec["rate"] > top:
            out.append(f"top sample rate is {_fmt(top)}, not {_fmt(spec['rate'])}")
    if "bandwidth" in spec:
        top = _bandwidth_limit(caps)
        if top is not None and spec["bandwidth"] > top:
            out.append(f"sees at most {_fmt(top)}, not {_fmt(spec['bandwidth'])}")
    if "channels" in spec and caps.channels.get("rx", 1) < spec["channels"]:
        out.append(f"has {caps.channels.get('rx', 1)} receive channel(s), not {spec['channels']}")
    if spec.get("tx") and not caps.channels.get("tx", 0):
        out.append("cannot transmit")
    return out


def granted(caps, spec) -> dict:
    """What the radio can do nearest to `spec`: for each key the value it would give (a frequency pair is the span clipped
    to what it tunes; a key the radio puts no limit on keeps the asked value)"""
    spec = normalize(spec)
    out = {}
    for key, value in spec.items():
        if key == "freq":
            out[key] = _freq_granted(caps, value)
        elif key == "rate":
            out[key] = _caps.nearest(caps.rates, value) if caps.rates else value
        elif key == "bandwidth":
            top = _bandwidth_limit(caps)
            out[key] = min(value, top) if top is not None else value
        elif key == "channels":
            out[key] = min(value, caps.channels.get("rx", 1))
        else:
            out[key] = bool(value and caps.channels.get("tx", 0))
    return out


OVER = 0.1     # cost of a radio that can only do more rate than wanted (it has to run faster than the program asked)


def score(caps, spec) -> tuple:
    """(keys missed, shortfall) of the radio against `spec`: how many keys it cannot meet, and how far from them it is as
    the share of each unmet key (so 1.0 is one key missed entirely), summed. (0, 0.0) meets all of `spec`. Rate above
    what was wanted costs a little and is not a miss."""
    spec = normalize(spec)
    got = granted(caps, spec)
    missed, total = 0, 0.0
    for key, value in spec.items():
        if got[key] == value:
            continue
        a, b = _span(value), _span(got[key])
        if key == "freq":
            scale = max(abs(a[1] - a[0]), abs(a[0]), 1.0)
            cost = min(1.0, (abs(a[0] - b[0]) + abs(a[1] - b[1])) / scale)
        elif key == "tx":
            cost = 1.0
        else:
            cost = min(1.0, abs(a[0] - b[0]) / max(abs(a[0]), 1.0))
            if key == "rate" and b[0] > a[0]:
                total += cost * OVER
                continue
        missed += 1
        total += cost
    return missed, total


def shortfall(caps, spec) -> float:
    """How far the radio is from `spec`, 0.0 when it meets all of it (see `score`). Orders radios that all meet `need`."""
    return score(caps, spec)[1]


def known(caps) -> bool:
    """Whether `caps` says anything about what the radio tunes or samples. One that does not (a driver that declares nothing,
    or a radio whose capabilities could not be read) meets every limit by default, so it is ranked after those that do."""
    return caps is not None and bool(caps.freq or caps.rates)


def rank(caps, want) -> tuple:
    """Sort key of a radio that meets `need`, smallest best: unknown capabilities last, then fewest keys of `want` missed, then nearest"""
    missed, total = score(caps, want) if caps is not None else (0, 0.0)
    return (not known(caps), missed, total)


def met(caps, spec) -> bool:
    """Whether the radio meets all of `spec`"""
    return not problems(caps, spec)
