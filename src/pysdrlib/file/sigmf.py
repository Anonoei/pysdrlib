"""SigMF recordings (https://sigmf.org): samples in `<name>.sigmf-data`, what they are in `<name>.sigmf-meta` (JSON)

pysdrlib reads and writes the core namespace: the datatype, sample rate, the captures (the centre frequency from each
sample on, and when the recording started), and annotations (pysdrlib marks samples lost while recording with one).
Only single-channel, little-endian recordings in a datatype `Formats` has (see `Formats.sigmf`).
"""
import datetime as _dt
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..base import Formats

VERSION = "1.2.0"
_SUFFIXES = (".sigmf-data", ".sigmf-meta", ".sigmf")

def is_sigmf(path) -> bool:
    """`path` names a SigMF recording: one of its files (".sigmf-data", ".sigmf-meta"), or ".sigmf" for both"""
    return Path(path).suffix in _SUFFIXES

def paths(path):
    """(data path, meta path) of the recording `path` names (any of its files, ".sigmf", or the name without a suffix)"""
    p = Path(path)
    base = p.with_suffix("") if p.suffix in _SUFFIXES else p
    return base.with_name(base.name + ".sigmf-data"), base.with_name(base.name + ".sigmf-meta")

def now():
    """The current time, as SigMF writes it (ISO 8601, UTC)"""
    return _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")

@dataclass
class Meta:
    """What a `.sigmf-meta` says, as pysdrlib uses it. captures: [(first sample, frequency or None)], in order;
    raw: the whole JSON, for any other field"""
    fmt: Formats
    rate: Optional[float] = None
    captures: list = field(default_factory=list)
    datetime: Optional[str] = None
    annotations: list = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @property
    def freq(self):
        """Centre frequency of the first sample (None: not recorded)"""
        return self.captures[0][1] if self.captures else None

    def freq_at(self, sample):
        """Centre frequency at `sample`: the one of the last capture that starts at or before it"""
        freq = self.freq
        for start, f in self.captures:
            if start > sample:
                break
            freq = f if f is not None else freq
        return freq

def read(path) -> Meta:
    """The metadata of the recording `path` names. Raises ValueError for one pysdrlib can't read"""
    _, meta_path = paths(path)
    with open(meta_path, encoding="utf-8") as f:
        raw = json.load(f)
    glob = raw.get("global", {})
    datatype = glob.get("core:datatype")
    try:
        fmt = Formats.get(datatype)
    except KeyError as e:
        raise ValueError(f"{meta_path}: datatype {datatype!r} is not supported: {e}") from None
    if int(glob.get("core:num_channels", 1)) != 1:
        raise ValueError(f"{meta_path}: {glob['core:num_channels']} channels; only single-channel recordings are supported")
    caps = sorted(raw.get("captures", []), key=lambda c: c.get("core:sample_start", 0))
    captures = [(int(c.get("core:sample_start", 0)), c.get("core:frequency")) for c in caps]
    when = next((c["core:datetime"] for c in caps if "core:datetime" in c), None)
    rate = glob.get("core:sample_rate")
    return Meta(fmt, float(rate) if rate is not None else None, captures, when, raw.get("annotations", []), raw)

def write(path, fmt, rate=None, captures=(), datetime=None, annotations=(), extra=None):
    """Write the `.sigmf-meta` of the recording `path` names. captures: [(first sample, frequency)]; extra: more
    global fields ({"core:hw": ...}). Written to a temporary file and renamed, so a reader never sees half of it"""
    fmt = Formats.get(fmt)
    if fmt.sigmf is None:
        raise ValueError(f"SigMF has no datatype for {fmt.name}: use one of "
                         f"{', '.join(f.name for f in Formats if f.sigmf)}")
    _, meta_path = paths(path)
    from .. import __version__
    glob = {"core:datatype": fmt.sigmf, "core:version": VERSION, "core:recorder": f"pysdrlib {__version__}"}
    if rate is not None:
        glob["core:sample_rate"] = float(rate)
    glob.update(extra or {})
    caps = []
    for k, (start, freq) in enumerate(captures or [(0, None)]):
        c = {"core:sample_start": int(start)}
        if freq is not None:
            c["core:frequency"] = float(freq)
        if k == 0 and datetime is not None:
            c["core:datetime"] = datetime
        caps.append(c)
    doc = {"global": glob, "captures": caps, "annotations": list(annotations)}
    tmp = meta_path.with_name(meta_path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
    os.replace(tmp, meta_path)
