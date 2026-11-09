class Overflow(Exception):
    """Asked for samples past the end of the file (or before its start)"""

class Underflow(Exception):
    """The file held fewer samples than its size says, e.g. it was truncated while being read"""
