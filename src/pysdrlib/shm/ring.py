"""The ring: complex64 samples in a named shared-memory segment, one writer and any number of readers

The ring itself is C++ (`_native/include/hardware_shm.hpp`, the ShmRing class in `pysdrlib/base/lib/_samples`), correct
across processes; this maps the memory and cleans up. `ShmStream` (in `stream`) adds the manifest that names it; a
`pysdrlib server` uses the ring alone, and tells its clients the segment's name (see `pysdrlib.remote.protocol`).

    writer = ShmWriter(capacity=fit_capacity(1 << 20), stream_id=1)
    writer.publish(samples, first_index)
    reader = ShmReader(writer.name)                          # another process
    n, first = reader.read(out, pos)                         # up to len(out) samples from stream index pos

- One writer per ring, on one machine. Samples are complex64; `out` must be a C-contiguous complex64 array.
- `publish(samples, first)`: `first` at or after `head` (lower raises ValueError; higher records a gap). Only the
  1024 most recent gaps are remembered.
- The ring carries only samples, their indices and `stream_id`: the rate, frequency and time go beside it (`ShmStream`).
- Size the ring with `fit_capacity` first: a segment is only filled as it is written, so one larger than `/dev/shm`
  has room for crashes the writer later, not now.
- There is no blocking wait: poll `head` (a cheap atomic load) and sleep a few ms. Start a new reader at `head`
  (minus the backlog it wants), not at 0.
- `destroy` removes the name; readers that have the ring mapped keep it until `close`, and see `closed`.
"""
import os
import secrets
import threading
from multiprocessing import shared_memory

from ..base.lib import samples as _samples

MIN_CAPACITY = 1 << 18   # samples
MAX_CAPACITY = 1 << 24


def fit_capacity(wanted: int, floor: int = MIN_CAPACITY) -> int:
    """`wanted` samples, held to what the machine can map: a segment is only filled as it is written, so one larger than
    /dev/shm has room for would crash the writer when it runs out. Returns 0 if not even the smallest ring fits"""
    wanted = min(MAX_CAPACITY, max(floor, int(wanted)))
    try:
        free = os.statvfs("/dev/shm").f_bavail * os.statvfs("/dev/shm").f_frsize
    except (OSError, AttributeError):   # no /dev/shm to ask (macOS, Windows): the system decides
        return wanted
    fits = int(free * 0.8 - _samples.shm_segment_size(0)) // 8
    return wanted if fits >= wanted else (fits if fits >= floor else 0)


_attach_lock = threading.Lock()

def _attach(name: str) -> shared_memory.SharedMemory:
    """Map an existing segment without letting this process's resource tracker claim it (before Python 3.13 it
    would remove the writer's segment when this process exits)"""
    try:
        return shared_memory.SharedMemory(name=name, track=False)
    except TypeError:
        pass
    from multiprocessing import resource_tracker
    with _attach_lock:
        register = resource_tracker.register
        resource_tracker.register = lambda n, rtype: None if rtype == "shared_memory" else register(n, rtype)
        try:
            return shared_memory.SharedMemory(name=name)
        finally:
            resource_tracker.register = register


class ShmWriter:
    """The server's end: creates the segment, and removes it with `destroy`"""
    def __init__(self, capacity: int, stream_id: int = 0):
        self.capacity = int(capacity)
        # short: macOS limits the name to 31 characters
        self.shm = shared_memory.SharedMemory(create=True, size=_samples.shm_segment_size(self.capacity),
                                              name=f"psdr-{secrets.token_hex(5)}-{stream_id}")
        self.ring = _samples.ShmRing(self.shm.buf, self.capacity, stream_id)

    @property
    def name(self) -> str:
        return self.shm.name

    def publish(self, samples, first: int):
        """Append complex64 samples whose first has stream index `first`"""
        self.ring.publish(samples, first)

    def close_stream(self):
        """No more samples are coming (readers can still read what is there)"""
        if self.shm is None:    # already destroyed (a new stream replaced this ring)
            return
        try:
            self.ring.close()
        except ValueError:      # destroyed on another thread between the check and here
            pass

    def destroy(self):
        """Remove the segment. Readers that have it mapped keep it until they let go"""
        if self.shm is None:
            return
        shm, self.shm = self.shm, None
        self.ring.close()
        self.ring.release()
        shm.close()
        try:
            shm.unlink()
        except FileNotFoundError:
            pass

    def __del__(self):
        try:
            self.destroy()
        except Exception:
            pass


class ShmReader:
    """A client's end. Raises `FileNotFoundError` if there is no such segment here (the server is on another machine)"""
    def __init__(self, name: str):
        self.shm = _attach(name)
        try:
            self.ring = _samples.ShmRing(self.shm.buf)
        except Exception:
            self.shm.close()
            raise
        self.name = name

    @property
    def head(self) -> int:
        """The stream index after the newest sample"""
        return self.ring.head

    @property
    def stream_id(self) -> int:
        return self.ring.stream_id

    @property
    def closed(self) -> bool:
        return self.ring.closed

    def read(self, out, pos: int):
        """Up to len(out) samples from stream index `pos` into `out`: (count, index of the first). The index is past `pos`
        when this reader was lapped or was inside a gap; with a count of 0 it is where to go on from"""
        return self.ring.read(out, pos)

    def close(self):
        if self.shm is None:
            return
        shm, self.shm = self.shm, None
        self.ring.release()
        shm.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
