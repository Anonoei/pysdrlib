class ExtIOError(Exception):
    pass

class INVALID_PARAM(ExtIOError):
    pass

class NOT_FOUND(ExtIOError):
    pass

class NOT_OPEN(ExtIOError):
    pass
