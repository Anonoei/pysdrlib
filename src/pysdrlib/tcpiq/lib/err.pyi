"""Errors of the compiled extension, made when it is imported (see hardware_pybind.hpp)"""

class TcpIqError(Exception):
    ...

class BIND(TcpIqError):
    """Could not listen on the address: in use, not this machine's, or no permission"""

class BUSY(TcpIqError):
    ...

class CONNECT(TcpIqError):
    """Could not connect to the server"""

class FAILED(TcpIqError):
    ...

class NOT_OPEN(TcpIqError):
    ...
