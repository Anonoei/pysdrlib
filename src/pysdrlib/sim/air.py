"""The air simulated radios share: what one transmits, every one listening hears

    tx = pysdrlib.device("sim", transmit=True, air="lab")      # transmits into the air "lab"
    rx = pysdrlib.device("sim", air="lab")                     # hears it (in this process or any other)
    tx.open(); tx.set_tx_freq(433.92e6); tx.set_tx_sample_rate(1e6); tx.start_tx(); tx.write(x)
    rx.open(); rx.initialize(cf=433.9e6, Fs=2e6); rx.start_rx(); y = rx.read(65536)

A sim made with `air=` (a name, or an `Air`) transmits into it and hears it: each transmitting channel is published as
a shared-memory stream (`pysdrlib.shm.ShmStream`, named `air-<name>.<pid>-<n>`, its manifest saying the rate, frequency
and time of its samples), and a receiving sim adds to every block it makes what each stream in its air has for that
block's time: shifted by the difference of the frequencies (phase-continuous in absolute time), resampled to its rate,
attenuated by the air's `loss_db`, before its gain and ADC (`gain_ref`, clipping) as its own tones are. A transmitter
whose centre is beyond +-Fs/2 of the receiver's is not heard. A sim hears its own transmission too.

**Time.** A realtime sim is on the wall clock: what it transmits is paced to its rate (`write` waits, as a radio's
queue does) and placed at the time it goes out; written late, after a pause, it starts again at the time it is written
(the pause is silence). Processes on one machine share the clock, so a receiver in one hears a transmitter in another,
in time, `latency` (0.1 s) late. A sim that isn't realtime counts its own samples from 0 instead (receive: from `open`; transmit: from
`start_tx`), so in one process a test is exact and repeatable: write, then `pump` the receiver. Realtime and not don't
hear each other.

**Lifetime.** A transmitter's stream lasts until its sim is closed (`stop_tx` is silence); a receiver that has seen it
hears it to its end even after that (its transmitter's process may be gone), so one that is behind still hears it all. A sim closes an air it made from a name; an `Air` given to it is the caller's to close. Each ring holds `capacity` samples (default: a second); a receiver further behind than that
misses the oldest.

Resampling is linear (exact when the rates are equal: the nearest sample, on the time grid). It is a simulator: no
multipath, no Doppler; add noise with the receiver's `noise=`.
"""
import itertools
import os
import time

import numpy as np

_IDS = itertools.count()

class Air:
    """A shared medium: a name (letters, digits, `_`, `-`), the streams under `air-<name>.` in the shm directory.
    loss_db: attenuation from any transmitter to any receiver. capacity: samples each transmitter's ring holds.
    latency: seconds a realtime receiver hears the air late (a radio's transfer latency), so what was just written, by
    a transmitter just made, is there to hear"""
    def __init__(self, name="sim", directory=None, loss_db=0.0, capacity=None, latency=0.1):
        if not name or not all(c.isalnum() or c in "_-" for c in name):
            raise ValueError(f"{name!r}: an air's name is letters, digits, '_' and '-'")
        self.name, self.directory = name, directory
        self.loss_db, self.capacity, self.latency = float(loss_db), capacity, float(latency)
        self.prefix = f"air-{name}."
        self._readers = {}   # segment -> ShmReader
        self._manifests, self._listed = [], 0.0
        self._seen = {}      # segment -> manifest: a stream heard is heard to its end, even once its transmitter is gone

    def __repr__(self):
        return f"Air({self.name!r})"

    def transmitter(self, Fs, cf, clock):
        """A transmitting channel's stream in this air"""
        return Transmitter(self, Fs, cf, clock)

    def streams(self, fresh=True):
        """The manifests of the transmitters in this air"""
        if fresh or time.monotonic() - self._listed > 0.05:
            from ..shm.stream import streams
            self._manifests = [m for m in streams(self.directory) if str(m.get("name", "")).startswith(self.prefix)]
            self._listed = time.monotonic()
        return self._manifests

    def _reader(self, segment):
        r = self._readers.get(segment)
        if r is None:
            from ..shm.ring import ShmReader
            try:
                r = self._readers[segment] = ShmReader(segment)
            except FileNotFoundError:
                return None
        return r

    def hear(self, first, n, cf, Fs, t0, clock):
        """What a receiver at `cf`, `Fs` hears of this air in its samples first..first+n, its sample 0 being at time `t0`
        (seconds on `clock`: "wall" or "samples")"""
        out = np.zeros(n, np.complex64)
        t_first = t0 + first / Fs
        gain = np.float32(10 ** (-self.loss_db / 20))
        for m in self.streams(fresh=clock == "samples"):
            if m.get("wall_time") is not None:
                self._seen[m["segment"]] = m
        for seg, m in list(self._seen.items()):
            info = m.get("info") or {}
            if info.get("clock") != clock or m.get("wall_time") is None:
                continue
            df, fs_tx = float(m["cf"]) - cf, float(m["Fs"])
            if abs(df) >= Fs / 2:
                continue
            r = self._reader(m["segment"])
            if r is None:
                continue
            u0 = m["wall_index"] + (t_first - m["wall_time"]) * fs_tx   # the transmitter's index of our first sample
            step = fs_tx / Fs
            k0 = int(np.floor(u0))
            k1 = int(np.ceil(u0 + (n - 1) * step)) + 2
            try:
                head = r.head
                if k0 >= head and r.closed: # Heard to its end, and its transmitter is gone: forgotten
                    self._seen.pop(seg, None)
                    self._readers.pop(seg, None).close()
                    continue
                if k1 <= m["wall_index"] or k0 >= head:
                    continue
                buf = self._read(r, k0, k1)
            except ValueError: # (the ring was let go of)
                self._readers.pop(seg, None)
                self._seen.pop(seg, None)
                continue
            if step == 1.0:   # the same rate: the nearest sample, on our grid
                off = int(np.floor(u0 - k0 + 0.5))
                seg = buf[off:off + n]
            else:
                u = (u0 - k0) + np.arange(n) * step
                i = np.floor(u).astype(np.int64)
                w = (u - i).astype(np.float32)
                seg = buf[i] * (1 - w) + buf[i + 1] * w
            if df:
                cyc = ((df * t_first) % 1.0 + df * np.arange(n) / Fs) % 1.0
                seg = seg * np.exp(np.float32(2 * np.pi) * 1j * cyc.astype(np.float32))
            out += (seg * gain).astype(np.complex64)
        return out

    @staticmethod
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

    def close(self):
        for r in self._readers.values():
            r.close()
        self._readers, self._seen = {}, {}

class Transmitter:
    """One transmitting channel's stream in an air: `write(samples, Fs, cf)` places them at their time"""
    def __init__(self, air, Fs, cf, clock):
        from ..shm.stream import ShmStream
        self.air, self.clock = air, clock
        self.Fs, self.cf = float(Fs), float(cf)
        self.stream = ShmStream(f"{air.prefix}{os.getpid()}-{next(_IDS)}", self.Fs, self.cf, capacity=air.capacity,
                                directory=air.directory, clock=clock, air=air.name)
        self.t0 = None if clock == "wall" else 0.0   # the time of index 0 of this generation (wall: at the first write)
        self.index = 0

    def write(self, samples, Fs, cf):
        x = np.ascontiguousarray(samples, dtype=np.complex64)
        if self.t0 is None:
            self.t0 = time.time()
        if (float(Fs), float(cf)) != (self.Fs, self.cf):   # retuned: a new generation, its indices from 0
            self.t0 = time.time() if self.clock == "wall" else self.t0 + self.index / self.Fs
            self.Fs, self.cf, self.index = float(Fs), float(cf), 0
            self.stream.retune(Fs=self.Fs, cf=self.cf)
        if self.clock == "wall":
            due = (time.time() - self.t0) * self.Fs
            if self.index < due - 0.01 * self.Fs:   # written late (a pause): it goes out now, the pause was silence
                self.index = int(due)
        self.stream.publish(x, self.index, wall_time=self.t0 + self.index / self.Fs)
        self.index += len(x)
        if self.clock == "wall":   # paced: a radio's queue holds a little; the writer waits for the rest
            ahead = self.t0 + self.index / self.Fs - time.time()
            if ahead > 0.05:
                time.sleep(ahead - 0.05)

    def close(self):
        self.stream.close()
