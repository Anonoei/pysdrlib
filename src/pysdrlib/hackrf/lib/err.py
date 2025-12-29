class HackRFError(Exception):
    pass

class INVALID_PARAM(HackRFError):
    pass

class NOT_FOUND(HackRFError):
    pass

class BUSY(HackRFError):
    pass

class NO_MEM(HackRFError):
    pass

class LIBUSB(HackRFError):
    pass

class THREAD(HackRFError):
    pass

class STREAMING_THREAD_ERR(HackRFError):
    pass

class STREAMING_STOPPED(HackRFError):
    pass

class STREAMING_EXIT_CALLED(HackRFError):
    pass

class USB_API_VERSION(HackRFError):
    pass

class NOT_LAST_DEVICE(HackRFError):
    pass

class OTHER(HackRFError):
    pass
