import threading
import time
from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class StreamStats:
    """What a receive stream has done since `start_rx`"""
    samples: int                # delivered by the device
    dropped: int                # discarded because `get_samples` was not called often enough
    missing: int                # the device should have delivered these but did not (estimated from the sample rate)
    stalls: int                 # times the device stopped delivering
    age: Optional[float]        # seconds since the device last delivered, or None when not receiving

@dataclass(frozen=True)
class Report:
    """What `Watchdog.poll` found since the last poll"""
    stalled: bool       # delivering has stopped (and not resumed)
    new_stall: bool     # ... and this is the first poll to see it
    missing: int        # samples the device failed to deliver in the window just closed

class Watchdog:
    """Notices when a device's sample stream stops, or delivers fewer samples than its rate requires.

    A driver's streaming thread calls `feed(n)` with every chunk; the consumer calls `poll()` when it reads.
    Nothing runs in the background, so a stall is noticed the next time the consumer looks.

    - stalled: no samples for `stall_timeout` seconds (from `start`, if none ever arrive). Set it to None
      to disable the watchdog.
    - missing: over each window of at least `window` seconds, samples received fall short of
      `rate * elapsed` by more than `tolerance` of it, beyond two chunks of slack (chunks arrive whole).
    """
    def __init__(self, stall_timeout=1.0, tolerance=0.1, window=0.25, clock=time.monotonic):
        self.stall_timeout = stall_timeout
        self.tolerance = tolerance
        self.window = window
        self._clock = clock
        self._lock = threading.Lock()
        self.start(0)
        self.stop()

    def start(self, rate):
        """Begin watching a stream of `rate` samples per second"""
        with self._lock:
            now = self._clock()
            self._running = True
            self._rate = rate
            self._last = now
            self._samples = self._stalls = self._missing = 0
            self._stalled = False
            self._max_chunk = 0
            self._win_t, self._win_n = None, 0   # opened by the first chunk

    def stop(self):
        with self._lock:
            self._running = False

    def set_rate(self, rate):
        """The stream's rate changed: judge it by the new rate from now on"""
        with self._lock:
            self._rate = rate
            self._win_t, self._win_n = None, 0

    def feed(self, n):
        """`n` samples arrived (called from the driver's thread)"""
        with self._lock:
            now = self._clock()
            self._samples += n
            self._last = now
            self._max_chunk = max(self._max_chunk, n)
            if self._win_t is None:
                self._win_t, self._win_n = now, self._samples   # the first chunk marks time zero

    def poll(self) -> Report:
        with self._lock:
            if not self._running or self.stall_timeout is None:
                return Report(False, False, 0)
            now = self._clock()
            stalled = now - self._last > self.stall_timeout
            new_stall = stalled and not self._stalled
            self._stalls += new_stall
            if self._stalled and not stalled:   # resumed: the gap is not a rate shortfall
                self._win_t, self._win_n = None, 0
            self._stalled = stalled
            missing = 0
            if not stalled and self._win_t is not None and now - self._win_t >= self.window:
                expected = self._rate * (now - self._win_t)
                short = expected - (self._samples - self._win_n) - 2 * self._max_chunk
                if short > self.tolerance * expected:
                    missing = int(short)
                    self._missing += missing
                self._win_t, self._win_n = now, self._samples
            return Report(stalled, new_stall, missing)

    def stats(self, dropped=0) -> StreamStats:
        """Snapshot; `dropped` is the consumer-side drop count, which the watchdog cannot see"""
        with self._lock:
            age = self._clock() - self._last if self._running else None
            return StreamStats(self._samples, dropped, self._missing, self._stalls, age)
