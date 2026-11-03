from .. import err, warn
from .buffer import RotatingBuffer
from .device import Device
from .watchdog import StreamStats, Watchdog

class NativeDevice(Device):
    """A Device driven by a compiled extension (pybind11, see `_ext/hardware_pybind.hpp`).

    The extension's device object, `self.device`, streams raw samples to a callback; this converts them
    to complex64 with `SAMPLE_FORMAT` and buffers them for `get_samples`. A device only implements what
    is particular to its hardware: `_open`, tuning and gain.

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
        self._buffer = RotatingBuffer()
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
        return self._watchdog.stats(self._buffer.dropped)

    def _rx_callback(self, buffer, valid_length, buffer_length):
        """Called by the extension on its streaming thread with raw samples; 0 keeps streaming"""
        samples = type(self).SAMPLE_FORMAT.to_complex64(buffer, valid_length)
        self._buffer.update(samples)
        self._watchdog.feed(len(samples))
        return 0

    def _rate_changed(self):
        """Size the buffer, and judge the stream, for the current sample rate; call when it changes"""
        self._buffer.reset(int(type(self).BUFFER_SECONDS * self._Fs))
        self._dropped_reported = 0
        self._watchdog.set_rate(self._Fs)

    def _close(self):
        self.device.close()

    def _start_rx(self):
        self.device.set_rx_callback(self._rx_callback)   # before streaming begins
        self._rate_changed()
        self._watchdog.start(self._Fs)   # before the device starts, so its startup counts
        self.device.start_rx()
    def _stop_rx(self):
        self._watchdog.stop()
        self.device.stop_rx()

    def _get_samples(self):
        samples = self._buffer.samples
        self._check_stream(have_samples=len(samples) > 0)
        return samples

    def _check_stream(self, have_samples):
        report = self._watchdog.poll()
        dropped = self._buffer.dropped
        if dropped > self._dropped_reported:
            warn.Dropped(f"{type(self).NAME}: {dropped - self._dropped_reported:,} samples dropped because "
                         f"get_samples was not called within {type(self).BUFFER_SECONDS}s")
            self._dropped_reported = dropped
        if report.missing:
            warn.Dropped(f"{type(self).NAME}: delivered about {report.missing:,} fewer samples than "
                         f"{self._Fs:,.0f} samples/s requires")
        if report.stalled and not have_samples:
            stats = self.stats
            raise err.StreamStalled(
                f"{type(self).NAME} stopped delivering samples {stats.age:.1f}s ago ({stats.samples:,} received "
                "since start_rx). Call stop_rx() and start_rx() to restart it; if that does not help, close and "
                "reopen the device.")
