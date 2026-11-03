from .lib import samples as _samples

class RotatingBuffer(_samples.Ring):
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

    The ring is C++ (`_native/include/hardware_ring.hpp`), the same one a native device's streaming thread fills.
    """
    @property
    def samples(self):
        """All pending samples, oldest first. Reading clears them."""
        return self.read()

    def __len__(self):
        return self.available
