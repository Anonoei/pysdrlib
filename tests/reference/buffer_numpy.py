"""The original pure-numpy RotatingBuffer, kept ONLY as a test oracle for the C++ ring
(src/pysdrlib/_native/include/hardware_ring.hpp). Not shipped, not imported by the library."""
from threading import Lock

import numpy as np

class RotatingBuffer:
    """Thread-safe ring buffer between a driver's streaming thread and a consumer.

    Producer (`update`) never blocks on the consumer and is O(len(new samples)) at any
    fill level. Consumer gets samples in order, oldest first:

    - `samples` / `read()`: everything pending, and clears it.
    - `read(n)`: the oldest `n` pending samples, leaving the rest for the next call. Use this
      to work through a backlog in bounded chunks.
    - If the consumer falls behind by more than the capacity, the *oldest* pending samples are
      dropped so reads always return the most recent data. `dropped` counts them: if it stays
      0 you have seen every sample the device produced.

    Capacity is set with `reset(size)`; size it for the longest gap between reads.
    """
    def __init__(self):
        self._samples = np.empty(0, dtype=np.complex64)  # ring storage
        self._N = 0         # capacity
        self._idx = 0       # number of pending (unread) samples
        self._last_idx = 0  # reserved
        self._head = 0      # next write position
        self._dropped = 0
        self._lock = Lock()

    def reset(self, size: int):
        """Set capacity to `size`; discards pending samples and clears `dropped`"""
        with self._lock:
            self._N = int(size)
            self._samples = np.empty(self._N, dtype=np.complex64)
            self._idx = 0
            self._last_idx = 0
            self._head = 0
            self._dropped = 0

    def update(self, samples):
        """Append samples, dropping the oldest pending ones if capacity is exceeded"""
        samples = np.asarray(samples, dtype=np.complex64)  # no copy if already complex64
        n = len(samples)
        if n == 0:
            return
        with self._lock:
            N = self._N
            if N == 0:
                self._dropped += n
            elif n >= N:  # new data alone fills the ring
                self._dropped += self._idx + (n - N)
                self._samples[:] = samples[-N:]
                self._head = 0
                self._idx = N
            else:
                end = self._head + n
                if end <= N:
                    self._samples[self._head:end] = samples
                else:  # wraps around the end of the ring
                    k = N - self._head
                    self._samples[self._head:] = samples[:k]
                    self._samples[:end - N] = samples[k:]
                self._head = end % N
                pending = self._idx + n
                if pending > N:
                    self._dropped += pending - N
                    pending = N
                self._idx = pending

    def read(self, count=None):
        """Remove and return up to `count` of the oldest pending samples (all if None)"""
        with self._lock:
            take = self._idx if count is None else max(0, min(int(count), self._idx))
            out = np.empty(take, dtype=np.complex64)
            if take:
                start = (self._head - self._idx) % self._N
                first = min(take, self._N - start)
                out[:first] = self._samples[start:start + first]
                out[first:] = self._samples[:take - first]
                self._idx -= take
            return out

    @property
    def samples(self):
        """All pending samples, oldest first. Reading clears them."""
        return self.read()

    @property
    def available(self):
        """Number of pending samples"""
        return self._idx

    def __len__(self):
        return self._idx

    @property
    def dropped(self):
        """Samples discarded (never delivered) since the last `reset()` or `clear_dropped()`"""
        return self._dropped

    def clear_dropped(self):
        """Return the dropped count and reset it to 0"""
        with self._lock:
            dropped, self._dropped = self._dropped, 0
            return dropped
