"""Errors of the compiled extension, made when it is imported (see hardware_pybind.hpp)"""

class RTLSDRError(Exception):
    ...

class BUSY(RTLSDRError):
    ...

class INVALID_PARAM(RTLSDRError):
    ...

class NOT_FOUND(RTLSDRError):
    ...

class NOT_OPEN(RTLSDRError):
    ...

class OPEN_FAILED(RTLSDRError):
    """The dongle is there but could not be opened: no permission, or something else has it"""
