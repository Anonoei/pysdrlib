"""The warnings pysdrlib issues, all of the category `warn._Warning` (a `UserWarning`), so they can be filtered as one.

`Dropped`: samples were lost (`sdr.on_drop = "warn"`, the default). `RateMismatch`: samples arrive at a rate other than
the one declared (a TCP stream). `InvalidValue`: a value was clamped or snapped to what the radio can do.
"""
import warnings

class _Warning(UserWarning):
    """pysdrlib warning"""

def InvalidValue(msg):
    warnings.warn(msg, _Warning)

def Dropped(msg):
    """Samples were lost, either by the device or because they were not read in time"""
    warnings.warn(msg, _Warning)

def RateMismatch(msg):
    """Samples arrive at a rate other than the declared sample rate"""
    warnings.warn(msg, _Warning)
