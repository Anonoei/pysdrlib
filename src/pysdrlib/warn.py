import warnings

class _Warning(UserWarning):
    """pysdrlib warning"""

def InvalidValue(msg):
    warnings.warn(msg, _Warning)
