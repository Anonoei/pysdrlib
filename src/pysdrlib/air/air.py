"""`Air`: a medium's name and settings, and the transmit channels in it"""
import threading
import time

PREFIX = "air-"           # an air's streams are named air-<name>.<pid>-<n>
LIST_SECONDS = 0.05       # how long a listing is reused, when a fresh one isn't asked for


class Air:
    """A shared medium: a name (letters, digits, `_`, `-`), the streams under `air-<name>.` in the shm directory.

    loss_db: attenuation from any transmitter to any receiver. capacity: samples each transmit channel's ring holds
    (default: a second). latency: seconds a realtime receiver hears the air late (a radio's transfer latency), so what
    was just written, by a transmitter just made, is there to hear. directory: the shm directory
    (`pysdrlib.shm.stream.stream_dir`).

    It holds nothing open: two `Air`s of one name (in one process or two) are the same air."""
    def __init__(self, name="sim", directory=None, loss_db=0.0, capacity=None, latency=0.1):
        if not name or not all(c.isalnum() or c in "_-" for c in name):
            raise ValueError(f"{name!r}: an air's name is letters, digits, '_' and '-'")
        self.name, self.directory = name, directory
        self.loss_db, self.capacity, self.latency = float(loss_db), capacity, float(latency)
        self.prefix = f"{PREFIX}{name}."
        self._lock = threading.Lock()
        self._listing, self._listed = [], 0.0

    def __repr__(self):
        return f"Air({self.name!r})"

    def channels(self, fresh=True) -> list:
        """The manifests of the transmit channels in this air (`fresh=False`: a listing up to 50 ms old will do)"""
        with self._lock:
            if fresh or time.monotonic() - self._listed > LIST_SECONDS:
                from ..shm.stream import streams
                self._listing = streams(self.directory, prefix=self.prefix)
                self._listed = time.monotonic()
            return self._listing

    def tx_channel(self, Fs, cf, clock="wall"):
        """A transmit channel into this air, at `Fs` and `cf`, on `clock` ("wall" or "samples"). Close it when done"""
        from .channel import TxChannel
        return TxChannel(self, Fs, cf, clock)

    def rx_channel(self):
        """A receive channel from this air. Close it when done"""
        from .channel import RxChannel
        return RxChannel(self)


def airs(directory=None) -> dict:
    """{name: [manifests of its transmit channels]} of the airs on this machine with a transmit channel open (receivers
    alone leave no trace)"""
    from ..shm.stream import streams
    found = {}
    for m in streams(directory, prefix=PREFIX):
        name = (m.get("info") or {}).get("air")
        if name:
            found.setdefault(name, []).append(m)
    return found
