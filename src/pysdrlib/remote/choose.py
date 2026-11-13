"""Choosing a radio by what a program needs, without knowing which radios a server has.

A client says what it needs (`need`: below this it is of no use) and what it would like (`want`):

    pysdrlib.connect(addr, program="pyspecan", need={"freq": 433e6, "rate": 2e6}, want={"freq": 433.92e6, "rate": 10e6})

Both are dicts with any of these keys:

    freq        Hz over the air to tune to, or a (low, high) pair of Hz the radio has to be able to tune across without a gap
                (through whatever LNB or converter the server says is on the radio: see `Chain`)
    rate        samples/s; a radio can run at it or faster (the client decimates), never slower
    bandwidth   Hz of spectrum seen (the analog filter where the radio has one, else its sample rate)
    channels    how many channels
    tx          True if it must be able to transmit

A radio is a candidate when it meets all of `need`. Of the candidates the one that misses the fewest keys of `want`, and
then comes nearest to it, wins; a radio whose capabilities are unknown comes after every one whose are known. A `freq` pair
means the radio can tune across the whole span (retuning between its ends), not that it sees all of it at once: that is
`bandwidth`. More rate or bandwidth than `want` asks for is no miss (only a small cost, growing with how much more, so
the radio nearest above wins); less is. Of radios that do equally well, the one whose antenna is best at the wanted frequency
comes first. What the radio can actually do nearest to `want` (and `need`) is reported back as `granted`: the rate it will really run at, and
for a radio behind a converter the frequency it tunes (`if_freq`), the `antenna` port and whether the spectrum is `inverted`.

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


def _covers(ranges, lo, hi):
    return any(a <= lo and hi <= b for a, b in _merged(ranges))


IDENTITY = _caps.Chain()


def _chains(caps, direction):
    """The chains of one direction of the radio (`IDENTITY`, nothing between it and the air, when it has none)"""
    return [c for c in caps.chains if c.direction == direction] or [IDENTITY]


def _declares(caps, chain):
    """Whether the radio or the chain says what is reachable (else it is taken to reach anything)"""
    return bool(caps.freq or chain.usable_ranges())


def _reach(caps, chain):
    """The over-the-air ranges the radio tunes through `chain`"""
    return chain.rf_ranges(caps.freq) if caps.freq else chain.usable_ranges()


def _fits(caps, chain, value):
    lo, hi = _span(value)
    return not _declares(caps, chain) or _covers(_reach(caps, chain), lo, hi)


def _optimal(chain, value):
    """How far from its best `chain` is at `value`: 0 when its antenna is best there (or says nothing), else the share of the
    frequency it is off by, capped at 1"""
    best = chain.optimal_ranges()
    if not best:
        return 0.0
    lo, hi = _span(value)
    if _covers(best, lo, hi):
        return 0.0
    gap = min(max(a - hi, lo - b, 0.0) for a, b in _merged(best))
    return max(1e-9, min(1.0, gap / max(abs(hi), 1.0)))


def _pick(caps, direction, value):
    """(chain, how far it is from optimal) of the chain that reaches `value` best, None when none reaches it"""
    fit = [(_optimal(c, value), i, c) for i, c in enumerate(_chains(caps, direction)) if _fits(caps, c, value)]
    if not fit:
        return None
    off, _, chain = min(fit, key=lambda f: f[:2])
    return chain, off


def _freq_ok(caps, value, direction="rx"):
    return _pick(caps, direction, value) is not None


def _clip(ranges, value):
    lo, hi = _span(value)
    best = None
    for a, b in _merged(ranges):
        g = (min(max(lo, a), b), min(max(hi, a), b))
        cost = abs(g[0] - lo) + abs(g[1] - hi)
        if best is None or cost < best[0]:
            best = (cost, g)
    return best


def _freq_granted(caps, value, direction="rx"):
    """(the span (or frequency) nearest `value` that fits one stretch of what the radio tunes, the chain it is through)"""
    lo, hi = _span(value)
    picked = _pick(caps, direction, value)
    if picked is not None:
        chain = picked[0]
        return (lo if lo == hi else [lo, hi]), chain
    best = None
    for chain in _chains(caps, direction):
        reach = _reach(caps, chain)
        if not reach:
            continue
        g = _clip(reach, value)
        if best is None or g[0] < best[0]:
            best = (g[0], g[1], chain)
    if best is None:    # no chain reaches anywhere at all
        return (lo if lo == hi else [lo, hi]), _chains(caps, direction)[0]
    g, chain = best[1], best[2]
    if lo == hi:
        return g[0], chain
    return [g[0], g[1]], chain


def _reach_text(caps, direction):
    out = []
    for c in _chains(caps, direction):
        text = ", ".join(f"{_fmt(a)}-{_fmt(b)}" for a, b in _merged(_reach(caps, c)))
        out.append(text + (f" via {c.name or c.antenna or c.port}" if c is not IDENTITY and (c.name or c.antenna or c.port) else ""))
    return "; ".join(out)


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
    if "freq" in spec:
        lo, hi = _span(spec["freq"])
        wanted = _fmt(lo) if lo == hi else f"{_fmt(lo)}-{_fmt(hi)}"
        if not _freq_ok(caps, spec["freq"]):
            out.append(f"cannot tune {wanted} (tunes {_reach_text(caps, 'rx')})")
        if spec.get("tx") and caps.channels.get("tx", 0) and not _freq_ok(caps, spec["freq"], "tx"):
            out.append(f"cannot transmit on {wanted} (transmits {_reach_text(caps, 'tx')})")
    if "rate" in spec and caps.rates and _caps.at_least(caps.rates, spec["rate"]) is None:
        # a faster radio will do (the client decimates), a slower one is of no use
        out.append(f"top sample rate is {_fmt(_widest(caps.rates))}, not {_fmt(spec['rate'])}")
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
    to what it tunes; a rate is the slowest it runs at that is not below the asked one, else its top; a key the radio puts no
    limit on keeps the asked value). Through a converter the frequency is still over the air, with `if_freq` (what the radio
    tunes), `antenna` and `inverted` beside it"""
    spec = normalize(spec)
    out = {}
    for key, value in spec.items():
        if key == "freq":
            out[key], chain = _freq_granted(caps, value)
            if chain is not IDENTITY:
                lo, hi = _span(value)
                out["if_freq"] = chain.to_if(lo) if lo == hi else list(chain.span_to_if(lo, hi))
                out["antenna"] = chain.antenna or chain.port
                out["inverted"] = chain.invert
        elif key == "rate":
            out[key] = (_caps.at_least(caps.rates, value) or _widest(caps.rates)) if caps.rates else value
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


def antenna_off(caps, want) -> float:
    """How far the antenna(s) the radio would use for `want` are from their best at the wanted frequency: 0.0 when optimal there or
    when nothing says (see `Chain.optimal`)"""
    want = normalize(want)
    if caps is None or "freq" not in want:
        return 0.0
    off = 0.0
    for direction in ("rx", "tx") if want.get("tx") else ("rx",):
        picked = _pick(caps, direction, want["freq"])
        off += picked[1] if picked else 0.0
    return off


def rank(caps, want) -> tuple:
    """Sort key of a radio that meets `need`, smallest best: unknown capabilities last, then fewest keys of `want` missed, then
    nearest, then the antenna nearest its best at the wanted frequency"""
    missed, total = score(caps, want) if caps is not None else (0, 0.0)
    return (not known(caps), missed, total, antenna_off(caps, want))


def met(caps, spec) -> bool:
    """Whether the radio meets all of `spec`"""
    return not problems(caps, spec)
