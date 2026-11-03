import warnings

class _Warning(UserWarning):
    """pysdrlib warning"""

def InvalidValue(msg):
    warnings.warn(msg, _Warning)

def Dropped(msg):
    """Samples were lost, either by the device or because they were not read in time"""
    warnings.warn(msg, _Warning)
