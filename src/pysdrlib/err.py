"""Error types"""
class _Error(Exception):
    """pysdrlib Error"""

class InvalidValue(_Error):
    """Error thrown when an invalid value is provided"""
