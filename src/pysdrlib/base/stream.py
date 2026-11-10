"""What came with a block of samples (`Device.read(..., meta=True)`), and the stream object (`Device.stream`)

    with sdr.stream(65536, meta=True) as s:        # starts receiving, stops (and flushes) on exit
        for x, m in s:
            if m.dropped: ...                      # samples were lost before or inside this block (m.gaps)
            if m.events: ...                       # the frequency / rate / gain changed inside it (m.events)
"""
import time
from dataclasses import dataclass, field
from typing import Optional

@dataclass(frozen=True)
class Event:
    """A setting that changed while receiving. offset: first sample of the block it applies to (it may still
    be settling for `Capabilities.retune_settle` seconds after); kind: "freq", "rate", "gain", "bandwidth"..."""
    offset: int
    kind: str
    value: object
    channel: int = 0

@dataclass(frozen=True)
class RxMeta:
    """index: position of the block's first sample in the stream (samples since start_rx, dropped ones counted)
    gaps: (offset in the block, samples missing just before it), for every place samples were lost
    discarded: samples skipped on purpose (settling after a retune with flush=True) before this block
    time: time.monotonic() at which the first sample was received (estimated from the deliveries, smoothed so it never goes backwards), or None
    freq / rate: what the device was set to for the first sample; events: changes inside the block
    """
    index: int
    count: int
    freq: float
    rate: float
    channel: int = 0
    gaps: tuple = ()
    discarded: int = 0
    time: Optional[float] = None
    events: tuple = ()

    @property
    def dropped(self):
        """Samples lost in or just before this block"""
        return sum(n for _, n in self.gaps)

class RxStream:
    """Fixed-size blocks from a device, receiving started on entry (if it wasn't) and stopped on exit

    Iterate it for blocks (or (block, RxMeta) with meta=True); `read()` gets one. channel: one, or several (blocks are
    then (channels, count), aligned; see Device.read). A `timeout` (seconds) bounds each
    block's wait; None waits as long as the device keeps delivering (a stalled device raises err.StreamStalled).
    """
    def __init__(self, device, count, timeout=None, meta=False, channel=0, flush=True):
        self.device, self.count, self.timeout, self.meta, self.channel = device, int(count), timeout, meta, channel
        self._flush = flush
        self._started = False

    def __enter__(self):
        if not self.device.state["rx"]:
            chans = (self.channel,) if isinstance(self.channel, int) else tuple(self.channel)
            self.device.start_rx(channels=chans if chans != (0,) else None)
            self._started = True
        elif self._flush:
            self.device.flush()
        return self

    def __exit__(self, *exc):
        if self._started and self.device.state["rx"]:
            self.device.stop_rx()
        self._started = False

    def read(self):
        return self.device.read(self.count, timeout=self.timeout, meta=self.meta, channel=self.channel)

    def __iter__(self):
        while self.device.state["rx"]:
            yield self.read()

def sample_time(rate, total, age, index):
    """time.monotonic() of sample `index`, from the last delivery: `total` samples were in by `age` seconds ago"""
    if age is None or not rate:
        return None
    return time.monotonic() - age - (total - index) / rate
