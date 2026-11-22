"""The exceptions pysdrlib raises, the same for every driver.

The ones code usually handles: `NotSupported` (a control this radio doesn't have; also a `NotImplementedError`),
`InvalidValue` (out of range with `sdr.strict`, or always for nonsense), `NoDevice` (nothing to open, or it can't be
opened), `Timeout` (a `read` with a `timeout`; also a `TimeoutError`), `StreamStalled` (no samples for
`sdr.stall_timeout`), `Lost` (the radio or its server is gone: reopen), `Dropped` (`on_drop="raise"` and samples were
lost), `UnknownFormat` (a sample format not known or not supported; also a `ValueError`). From a server: `Busy`, `NotController`, `RemoteError`, `ProtocolError`. Warnings are in `pysdrlib.warn`.
"""
class _Error(Exception):
    """pysdrlib Error"""

class InvalidValue(_Error):
    """Error thrown when an invalid value is provided"""

class NoDevice(_Error):
    """Error thrown when unable to connect to device. `reason` says why, where a server could tell (None otherwise):

    - "none_attached": the server has no radio attached, and nothing it hosts is free (it may still open a type that
      needs no hardware: `discover` lists those as "openable")
    - "none_free": it has radios attached, but every one has a controller
    - "unmet": it has free radios, but none meets `need` (the message says why each was left out)
    - "no_type": it cannot open a device of the type asked for
    """
    def __init__(self, *args, reason: str | None = None):
        super().__init__(*args)
        self.reason = reason

class UnknownFormat(_Error, ValueError):
    """A sample format pysdrlib can't name or doesn't support: a file whose extension and metadata don't say, an
    unsupported SigMF datatype, or an unknown stream format name. A `ValueError`"""

class NotOpen(_Error):
    """Error thrown when attempting to control device without it open"""

class StreamStalled(_Error):
    """Error thrown by `get_samples` when the device has stopped delivering samples"""

class Lost(StreamStalled):
    """Error thrown by `read` when the device is gone: unplugged, closed or failing, or the server it is on no longer
    answers. Reopen it (or connect again) to go on. Raised by a `read` of one channel, it carries what that read had
    received before the end: `.samples` (complex64, possibly empty) and `.meta` (their RxMeta, or None when there are none)"""
    samples = None
    meta = None

class RemoteError(_Error):
    """Error raised by a remote pysdrlib server that has no more specific type"""

class NotController(_Error):
    """Error thrown when a viewer (or a client without the controller token) attempts to control a remote device"""

class Busy(_Error):
    """Error thrown when a remote device already has a controller"""

class ProtocolError(_Error):
    """Error thrown when a remote peer sends something that isn't valid pysdrlib protocol"""

class Timeout(_Error, TimeoutError):
    """Error thrown by `read` when the samples asked for did not arrive in time"""

class NotStreaming(_Error):
    """Error thrown when reading from a device that isn't receiving"""

class Dropped(_Error):
    """Error thrown when samples were lost and the device's `on_drop` is 'raise', or inside a block read with
    on_drop="raise" (that one carries the block: `.samples`, `.meta`, and `.lost`, how many samples)"""

class Overflow(_Error, EOFError):
    """Error thrown by `File` when asked for samples past the end of the file (or before its start)"""

class Underflow(_Error):
    """Error thrown by `File` when the file held fewer samples than its size says, e.g. it was truncated while being read"""

class NotSupported(_Error, NotImplementedError):
    """Error thrown when a device has no such control (see `Device.caps`)"""
