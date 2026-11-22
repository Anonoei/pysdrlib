"""Error types"""
class _Error(Exception):
    """pysdrlib Error"""

class InvalidValue(_Error):
    """Error thrown when an invalid value is provided"""

class NoDevice(_Error):
    """Error thrown when unable to connect to device"""

class NotOpen(_Error):
    """Error thrown when attempting to control device without it open"""

class StreamStalled(_Error):
    """Error thrown by `get_samples` when the device has stopped delivering samples"""

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
