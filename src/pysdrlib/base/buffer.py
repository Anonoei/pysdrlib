from threading import Lock

import numpy as np

class RotatingBuffer:
    def __init__(self):
        self._samples = np.empty([], dtype=np.complex64)
        self._N = 0
        self._idx = 0
        self._last_idx = 0
        self._lock = Lock()

    def reset(self, size: int):
        self._N = size
        self._samples = np.empty(self._N, dtype=np.complex64)
        self._last_idx = 0

    def update(self, samples):
        N = len(samples)
        # print(f"buffer.update({N})")
        with self._lock:
            if self._idx+N < self._N:
                self._samples[self._idx:self._idx+N] = samples
                self._idx += N
            else:
                if self._idx == self._N:
                    self._samples = np.roll(self._samples, -N)
                    self._samples[-N:] = samples
                else:
                    self._samples = np.roll(self._samples, self._N-(self._idx+N))
                    self._samples[-N:] = samples
                    self._idx = self._N

    @property
    def samples(self):
        N = self._idx
        self._idx = 0
        # print(f"buffer.samples returning self._samples[:{N}]")
        samples = self._samples[:N]
        with self._lock:
            self._samples = np.roll(self._samples, -N)
        return samples
