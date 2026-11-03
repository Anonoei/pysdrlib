class RTLSDRError(Exception):
    pass

class INVALID_PARAM(RTLSDRError):
    pass

class NOT_FOUND(RTLSDRError):
    pass

class NOT_OPEN(RTLSDRError):
    pass

class OPEN_FAILED(RTLSDRError):
    """The dongle is there but could not be opened: no permission, or something else has it"""

class BUSY(RTLSDRError):
    pass
