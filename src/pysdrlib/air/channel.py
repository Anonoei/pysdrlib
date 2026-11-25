"""`TxChannel` and `RxChannel`: what a device attaches to an `Air` to transmit into it, and to receive from it"""
import itertools
import os
import time

import numpy as np

_IDS = itertools.count()
CLOCKS = ("wall", "samples")
LEAD = 0.05   # seconds a wall-clock transmit channel publishes ahead of now (a radio's queue); `write` waits for the rest


def _clock(clock):
    if clock not in CLOCKS:
        raise ValueError(f"{clock!r}: a channel's clock is one of {', '.join(CLOCKS)}")
    return clock


class TxChannel:
    """One transmit channel's stream in an air: `write(samples)` places them at their time (see `pysdrlib.air`)"""
    def __init__(self, air, Fs, cf, clock="wall"):
        from ..shm.stream import ShmStream
        self.air, self.clock = air, _clock(clock)
        self.Fs, self.cf = float(Fs), float(cf)
        self.stream = ShmStream(f"{air.prefix}{os.getpid()}-{next(_IDS)}", self.Fs, self.cf, capacity=air.capacity,
                                directory=air.directory, clock=clock, air=air.name)
        self.t0 = None if clock == "wall" else 0.0   # the time of index 0 of this generation (wall: at the first write)
        self.index = 0                               # the index of the next sample, in this generation

    def __repr__(self):
        return f"TxChannel({self.air.name!r}, {self.stream.name!r})"

    def write(self, samples, Fs=None, cf=None, scale=1.0):
        """Transmit complex64 `samples` (full scale +-1) into the air, scaled by `scale` (linear). A new `Fs` or `cf` is a
        new generation, starting after what was written before. On the wall clock this waits, as a radio's queue does,
        so no more than ~0.1 s is ahead of now. Returns the count written"""
        x = np.ascontiguousarray(samples, dtype=np.complex64)
        if scale != 1.0:
            x = x * np.float32(scale)
        Fs = self.Fs if Fs is None else float(Fs)
        cf = self.cf if cf is None else float(cf)
        if (Fs, cf) != (self.Fs, self.cf):
            self._retune(Fs, cf)
        if self.clock == "samples":
            self._publish(x)
            return len(x)
        now = time.time()
        if self.t0 is None:
            self.t0 = now
        due = (now - self.t0) * self.Fs
        if self.index < due - 0.01 * self.Fs:   # written late (a pause): it goes out now, the pause was silence
            self.index = int(due)
        step = max(1, min(int(LEAD * self.Fs), self.stream.writer.capacity // 4))
        for i in range(0, len(x), step):   # paced a piece at a time: a long write never laps its own ring
            self._publish(x[i:i + step])
            ahead = self.t0 + self.index / self.Fs - time.time()
            if ahead > LEAD:
                time.sleep(ahead - LEAD)
        return len(x)

    def _publish(self, x):
        if len(x):
            self.stream.publish(x, self.index, wall_time=self.t0 + self.index / self.Fs)
            self.index += len(x)

    def _retune(self, Fs, cf):
        if self.clock == "wall":   # after what is queued still (never overlapping it), or now
            now = time.time()
            self.t0 = now if self.t0 is None else max(now, self.t0 + self.index / self.Fs)
        else:
            self.t0 += self.index / self.Fs
        self.Fs, self.cf, self.index = Fs, cf, 0
        self.stream.retune(Fs=Fs, cf=cf)

    def close(self):
        """The end of this channel's transmission (receivers that have seen it still hear what was written)"""
        self.stream.close()


class RxChannel:
    """One receive channel from an air: `receive` is what it hears in a block of its samples (see `pysdrlib.air`).
    Used from one thread"""
    def __init__(self, air):
        self.air = air
        self._streams = {}   # segment -> (manifest, ShmReader): every stream seen, heard to its end
        self._t0 = 0.0       # the time of sample `_first`, on the clock (wall: epoch seconds)
        self._first = 0
        self._off = 0.0      # seconds from _t0 to sample _first, across rate changes since the anchor
        self._Fs = None      # the rate since _first

    def __repr__(self):
        return f"RxChannel({self.air.name!r})"

    def anchor(self, t0, first=0):
        """Its sample `first` is at time `t0` (seconds on its clock)"""
        self._t0, self._first, self._off = float(t0), int(first), 0.0

    def _elapsed(self, first, Fs):
        """Seconds from the anchor to sample `first`: small, so exact enough for any sample (an epoch time isn't)"""
        if self._Fs is None:
            self._Fs = Fs
        elif Fs != self._Fs:   # the rate changed: the samples before `first` were at the old one
            self._off += (first - self._first) / self._Fs
            self._first, self._Fs = first, Fs
        return self._off + (first - self._first) / Fs

    def _discover(self, clock):
        from ..shm.ring import ShmReader
        for m in self.air.channels(fresh=clock == "samples"):
            seg = m["segment"]
            if seg in self._streams or m.get("wall_time") is None or (m.get("info") or {}).get("clock") != clock:
                continue
            try:
                self._streams[seg] = (m, ShmReader(seg))
            except (FileNotFoundError, ValueError):   # gone before we got to it
                continue

    def _forget(self, seg):
        _, r = self._streams.pop(seg)
        try:
            r.close()
        except Exception:
            pass

    def receive(self, first, n, cf, Fs, clock):
        """What it hears at `cf`, `Fs` in its samples first..first+n, on `clock` ("wall" or "samples"): complex64, before
        any gain of its own"""
        Fs = float(Fs)
        out = np.zeros(n, np.complex64)
        elapsed = self._elapsed(first, Fs)
        self._discover(_clock(clock))
        gain = np.float32(10 ** (-self.air.loss_db / 20))
        for seg, (m, r) in list(self._streams.items()):
            fs_tx = float(m["Fs"])
            rel = (self._t0 - m["wall_time"]) + elapsed   # seconds from the stream's first sample to our first
            u0 = m["wall_index"] + rel * fs_tx            # the stream's index of our first sample
            step = fs_tx / Fs
            k0 = int(np.floor(u0))
            k1 = int(np.ceil(u0 + (n - 1) * step)) + 2
            try:
                head, closed = r.head, r.closed
                if k0 >= head and closed:   # heard to its end, and its transmitter is done: forgotten
                    self._forget(seg)
                    continue
                df = float(m["cf"]) - cf
                if abs(df) >= Fs / 2 or k1 <= m["wall_index"] or k0 >= head:
                    continue
                buf = _read(r, k0, k1)
            except ValueError:   # (the ring was let go of)
                self._forget(seg)
                continue
            if step == 1.0:   # the same rate: the nearest sample, on our grid
                off = int(np.floor(u0 - k0 + 0.5))
                piece = buf[off:off + n]
            else:
                u = (u0 - k0) + np.arange(n) * step
                i = np.floor(u).astype(np.int64)
                w = (u - i).astype(np.float32)
                piece = buf[i] * (1 - w) + buf[i + 1] * w
            if df:   # phase-continuous from block to block: from the stream's first sample
                cyc = ((df * rel) % 1.0 + df * np.arange(n) / Fs) % 1.0
                piece = piece * np.exp(np.float32(2 * np.pi) * 1j * cyc.astype(np.float32))
            out += piece * gain
        return out

    def close(self):
        for seg in list(self._streams):
            self._forget(seg)


def _read(r, k0, k1):
    """Samples k0..k1 of a ring (zeros where it has none: before its start, lost, or not written yet)"""
    buf = np.zeros(k1 - k0, np.complex64)
    pos = max(k0, 0)
    while pos < k1:
        got, start = r.read(buf[pos - k0:], pos)
        if got == 0:
            if start <= pos:
                break
            pos = start
            continue
        pos = start + got
    return buf
