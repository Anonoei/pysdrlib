class UHDError(Exception):
    pass

class INVALID_DEVICE(UHDError):
    pass

class INDEX(UHDError):
    pass

class KEY(UHDError):
    pass

class NOT_IMPLEMENTED(UHDError):
    pass

class USB(UHDError):
    pass

class IO(UHDError):
    pass

class OS(UHDError):
    pass

class ASSERTION(UHDError):
    pass

class LOOKUP(UHDError):
    pass

class TYPE(UHDError):
    pass

class VALUE(UHDError):
    pass

class RUNTIME(UHDError):
    pass

class ENVIRONMENT(UHDError):
    pass

class SYSTEM(UHDError):
    pass

class NOT_FOUND(UHDError):
    """No USRP matched (none attached, or it could not be found at the address)"""

class NOT_OPEN(UHDError):
    pass

class TIMEOUT(UHDError):
    pass

class OVERFLOW(UHDError):
    pass

class OTHER(UHDError):
    pass
