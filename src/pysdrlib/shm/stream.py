"""The writer's end of a named stream: a ring (`ring.ShmWriter`) and a manifest that says what is in it

    with ShmStream("rpps.ch3", Fs=250e3, cf=433.92e6, bw=200e3) as out:   # extras go to the manifest's "info"
        out.publish(x, first)          # complex64; `first` is the stream index of x[0] (later than the last: a gap)
        out.retune(cf=434.1e6)         # a new generation: readers follow it, with a freq / rate event
"""
import json
import os
import re
import tempfile
import time
from typing import Optional

from . import ring

FORMAT = 1                    # the manifest's "format": a reader refuses a later one
DIR_ENV = "PYSDRLIB_SHM_DIR"
ANCHORS = 8                   # earlier generations whose time anchor the manifest keeps
NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")


def stream_dir(directory=None) -> str:
    """Where the manifests are: `directory`, else $PYSDRLIB_SHM_DIR, else `pysdrlib-shm` in the temporary directory"""
    return str(directory or os.environ.get(DIR_ENV) or os.path.join(tempfile.gettempdir(), "pysdrlib-shm"))


def _alive(pid) -> bool:
    """The process `pid` is still running (True where that can't be asked)"""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name != "posix":
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:   # someone else's, and running
        return True
    return True


def read_manifest(path) -> Optional[dict]:
    """The manifest at `path`, or None if there is none (a stream closed), it isn't one, or it's of a later format"""
    try:
        with open(path, encoding="utf-8") as f:
            m = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(m, dict) or m.get("format") != FORMAT or not isinstance(m.get("segment"), str):
        return None
    return m


def manifest_path(name, directory=None) -> str:
    return os.path.join(stream_dir(directory), f"{name}.json")


def streams(directory=None, live=True, prefix="") -> list:
    """The manifests of the streams published here, by name. live: only those whose writer is still running. prefix:
    only the streams whose name starts with it (the others' manifests aren't read)"""
    d = stream_dir(directory)
    try:
        files = sorted(f for f in os.listdir(d) if f.endswith(".json") and f.startswith(prefix))
    except OSError:
        return []
    found = []
    for f in files:
        m = read_manifest(os.path.join(d, f))
        if m is not None and (not live or _alive(m.get("pid"))):
            found.append(m)
    return found


class ShmStream:
    """Publish a stream of samples under `name`, for any process on this machine to receive (`pysdrlib.open("shm@NAME")`)

    name: letters, digits, `_`, `.` and `-` (a `.` makes a namespace: "rpps.ch3"). Fs, cf: the sample rate and centre
    frequency. capacity: samples the ring holds (default one second, at least `ring.MIN_CAPACITY`; held to what
    `/dev/shm` has room for). directory: where the manifest goes (see `stream_dir`). Every other keyword is written to
    the manifest's "info" (JSON values: a bandwidth, a label...) for readers to show.

    Raises FileExistsError if a running process publishes `name` already, OSError if `/dev/shm` has no room for a ring.
    """
    def __init__(self, name: str, Fs: float, cf: float, capacity: Optional[int] = None, directory=None, **info):
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ValueError(f"{name!r}: a stream name is letters, digits, '_', '.' and '-'")
        self.name = name
        self.directory = stream_dir(directory)
        os.makedirs(self.directory, exist_ok=True)
        self.path = manifest_path(name, self.directory)
        taken = read_manifest(self.path)
        if taken is not None and taken.get("pid") != os.getpid() and _alive(taken.get("pid")):
            raise FileExistsError(f"the stream {name!r} is published already (by process {taken['pid']})")
        self.gen = taken["gen"] + 1 if taken is not None and isinstance(taken.get("gen"), int) else 0
        self.Fs, self.cf, self.info = float(Fs), float(cf), dict(info)
        self._capacity = capacity
        self._anchor = None            # (wall time, stream index) of this generation's first sample published
        self._anchors = {}             # segment -> [wall time, index] of the last generations (a reader may still be on one)
        self.writer = None
        self.writer = self._new_ring()
        self._write_manifest()

    def _new_ring(self):
        wanted = self._capacity if self._capacity is not None else max(ring.MIN_CAPACITY, int(self.Fs))
        capacity = ring.fit_capacity(wanted)
        if not capacity:
            raise OSError(f"/dev/shm has no room for a ring of {ring.MIN_CAPACITY} samples")
        return ring.ShmWriter(capacity, self.gen)

    @property
    def manifest(self) -> dict:
        """What readers find in the manifest"""
        wall, index = self._anchor if self._anchor else (None, None)
        return {"format": FORMAT, "name": self.name, "segment": self.writer.name, "gen": self.gen,
                "capacity": self.writer.capacity, "Fs": self.Fs, "cf": self.cf, "pid": os.getpid(),
                "wall_time": wall, "wall_index": index, "anchors": self._anchors, "info": self.info}

    def _write_manifest(self):
        """Replace the manifest at once (a reader never sees half of one)"""
        fd, tmp = tempfile.mkstemp(prefix=f".{self.name}.", suffix=".tmp", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.manifest, f)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    @property
    def head(self) -> int:
        """The stream index after the newest sample published"""
        return self.writer.ring.head

    def publish(self, samples, first: int, wall_time: Optional[float] = None):
        """Append complex64 `samples` whose first has stream index `first` (at or after `head`; later is a gap).
        wall_time: `time.time()` of samples[0], when the source knows it (`RxMeta.wall_time`); the first publish of a
        generation anchors the readers' timestamps there (default: now)"""
        if self.writer is None:
            raise ValueError(f"the stream {self.name!r} is closed")
        if self._anchor is None and len(samples):
            self._anchor = (float(wall_time) if wall_time is not None else time.time(), int(first))
            self._write_manifest()   # before the samples: a reader that has them finds the anchor
        self.writer.publish(samples, first)

    def retune(self, Fs: Optional[float] = None, cf: Optional[float] = None, capacity: Optional[int] = None, **info):
        """The samples published from now on are at a new rate and/or frequency: a new generation, on a new ring. Readers
        drain the old one, then follow (a freq or rate event in their RxMeta). Stream indices start over with what is
        published next. `info` keywords update the manifest's "info" (None removes one)"""
        if self.writer is None:
            raise ValueError(f"the stream {self.name!r} is closed")
        old = self.writer
        self.gen += 1
        self.Fs = float(Fs) if Fs is not None else self.Fs
        self.cf = float(cf) if cf is not None else self.cf
        self._capacity = capacity if capacity is not None else self._capacity
        for k, v in info.items():
            if v is None:
                self.info.pop(k, None)
            else:
                self.info[k] = v
        if self._anchor is not None:   # a reader that hasn't reached this generation's samples yet needs its anchor
            self._anchors[old.name] = list(self._anchor)
            while len(self._anchors) > ANCHORS:
                del self._anchors[next(iter(self._anchors))]
        self._anchor = None
        self.writer = self._new_ring()
        self._write_manifest()   # first: a reader that sees the old ring closed finds the new one
        old.close_stream()
        old.destroy()

    def close(self):
        """No more samples: readers drain what is there, then their stream ends (err.Lost)"""
        if self.writer is None:
            return
        writer, self.writer = self.writer, None
        writer.close_stream()
        m = read_manifest(self.path)
        if m is not None and m.get("segment") == writer.name:   # (not one a later publisher of the name wrote)
            try:
                os.unlink(self.path)
            except FileNotFoundError:
                pass
        writer.destroy()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
