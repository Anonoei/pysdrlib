"""Error types"""
class _Error(Exception):
    """pysdrlib Error"""

class InvalidValue(_Error):
    """Error thrown when an invalid value is provided"""

class NoDevice(_Error):
    """Error thrown when unable to connect to device"""
