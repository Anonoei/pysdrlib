"""Errors of the compiled extension, made when it is imported (see hardware_pybind.hpp)"""

class BladeRFError(Exception):
    ...

class CHECKSUM(BladeRFError):
    ...

class FPGA_OP(BladeRFError):
    ...

class INVAL(BladeRFError):
    ...

class IO(BladeRFError):
    ...

class MEM(BladeRFError):
    ...

class MISALIGNED(BladeRFError):
    ...

class NODEV(BladeRFError):
    ...

class NOT_FOUND(BladeRFError):
    """No bladeRF matched (none attached, or it could not be found at the address)"""

class NOT_INIT(BladeRFError):
    ...

class NOT_OPEN(BladeRFError):
    ...

class NO_FILE(BladeRFError):
    ...

class PERMISSION(BladeRFError):
    """No permission to open the device (udev rules?)"""

class QUEUE_FULL(BladeRFError):
    ...

class RANGE(BladeRFError):
    ...

class TIMEOUT(BladeRFError):
    ...

class TIME_PAST(BladeRFError):
    ...

class UNEXPECTED(BladeRFError):
    ...

class UNSUPPORTED(BladeRFError):
    ...

class UNSUPPORTED_BOARD(BladeRFError):
    """A bladeRF, but not a 2.0 micro: only that one is supported"""

class UPDATE_FPGA(BladeRFError):
    """The FPGA image is too old for this libbladeRF"""

class UPDATE_FW(BladeRFError):
    """The firmware is too old for this libbladeRF"""

class VALUE(BladeRFError):
    ...

class WOULD_BLOCK(BladeRFError):
    ...
