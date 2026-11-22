"""The warnings pysdrlib issues, all of the category `warn._Warning` (a `UserWarning`), so they can be filtered as one.

`Dropped`: samples were lost (`sdr.on_drop = "warn"`, the default). `RateMismatch`: samples arrive at a rate other than
the one declared (a TCP stream). `InvalidValue`: a value was clamped or snapped to what the radio can do.
"""
import os
import warnings

# Each warning is attributed to the first line outside pysdrlib (the caller's `read`, `set_rx_gain`...), not to here
_HERE = (os.path.dirname(os.path.abspath(__file__)) + os.sep,)

class _Warning(UserWarning):
    """pysdrlib warning"""

def InvalidValue(msg):
    warnings.warn(msg, _Warning, skip_file_prefixes=_HERE)

def Dropped(msg):
    """Samples were lost, either by the device or because they were not read in time"""
    warnings.warn(msg, _Warning, skip_file_prefixes=_HERE)

def RateMismatch(msg):
    """Samples arrive at a rate other than the declared sample rate"""
    warnings.warn(msg, _Warning, skip_file_prefixes=_HERE)
