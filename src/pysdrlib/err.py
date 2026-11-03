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
