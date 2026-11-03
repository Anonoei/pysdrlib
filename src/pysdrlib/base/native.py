from .. import err, warn
from .device import Device
from .watchdog import StreamStats, Watchdog

class NativeDevice(Device):
    """A Device driven by a compiled extension (pybind11, see `_native/include/hardware_pybind.hpp`).

    The extension's device object, `self.device`, streams on a thread of its own. That thread converts the
    raw samples to complex64 (with the device's sample format) and queues them, all in C++ and without the
    GIL, so a busy Python thread (a GUI redraw, say) cannot hold up the driver and overflow the hardware's
    buffers. `get_samples` drains the queue. A device only implements what is particular to its hardware:
    `_open`, tuning and gain.

        class MyDevice(NativeDevice):
            SAMPLE_FORMAT = convert.INT8
            def _open(self): return lib.mydev.open()
            def _set_freq(self, freq): self.device.set_freq(int(freq))

    Receiving is watched (see `Watchdog`), each time `get_samples` is called:
    - the device stops delivering for `stall_timeout` seconds: raises `err.StreamStalled`, once any
      samples still buffered have been returned
    - samples were dropped, by the device or because `get_samples` was not called often enough: `warn.Dropped`
    `stats` has the counts.
    """
    BUFFER_SECONDS = 0.5   # how much stream `get_samples` may fall behind by before dropping the oldest
    STALL_TIMEOUT = 1.0    # seconds without samples before the stream is considered stalled; None: never

    def __init__(self):
        super().__init__()
        self._watchdog = Watchdog(type(self).STALL_TIMEOUT)
        self._dropped_reported = 0

    @property
    def stall_timeout(self):
        """Seconds without samples before `get_samples` raises `err.StreamStalled`; None disables"""
        return self._watchdog.stall_timeout
    @stall_timeout.setter
    def stall_timeout(self, seconds):
        self._watchdog.stall_timeout = seconds

    @property
    def stats(self) -> StreamStats:
        """Samples delivered, dropped and missing, and stalls, since `start_rx`"""
        native = self.device.rx_stats()
        self._watchdog.observe(native["samples"], native["age"], native["max_chunk"])
        return self._watchdog.stats(native["dropped"])

    def _rate_changed(self):
        """Size the queue, and judge the stream, for the current sample rate; call when it changes"""
        self.device.set_rx_buffer(int(type(self).BUFFER_SECONDS * self._Fs))
        self._dropped_reported = 0
        self._watchdog.set_rate(self._Fs)

    def _close(self):
        self.device.close()

    def _start_rx(self):
        self._rate_changed()
        self.device.reset_rx_stats()
        self._watchdog.start(self._Fs)   # before the device starts, so its startup counts
        self.device.start_rx()
    def _stop_rx(self):
        self._watchdog.stop()
        self.device.stop_rx()

    def _get_samples(self):
        samples = self.device.read_rx()
        self._check_stream(have_samples=len(samples) > 0)
        return samples

    def _check_stream(self, have_samples):
        native = self.device.rx_stats()
        self._watchdog.observe(native["samples"], native["age"], native["max_chunk"])
        report = self._watchdog.poll()
        dropped = native["dropped"]
        if dropped > self._dropped_reported:
            warn.Dropped(f"{type(self).NAME}: {dropped - self._dropped_reported:,} samples dropped because "
                         f"get_samples was not called within {type(self).BUFFER_SECONDS}s")
            self._dropped_reported = dropped
        if report.shortfall_start:   # once per episode; stats.missing keeps the total
            warn.Dropped(f"{type(self).NAME}: delivering fewer samples than {self._Fs:,.0f} samples/s requires "
                         f"(about {report.missing:,} missing in the last window)")
        if report.stalled and not have_samples:
            stats = self.stats
            raise err.StreamStalled(
                f"{type(self).NAME} stopped delivering samples {stats.age:.1f}s ago ({stats.samples:,} received "
                "since start_rx). Call stop_rx() and start_rx() to restart it; if that does not help, close and "
                "reopen the device.")
