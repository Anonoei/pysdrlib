from ..base.device import Device
from .. import err, warn

from .config import ConfigB210 as config

class B210(Device):
    NAME = "UHD B210"
    CONFIG = config
    CAN_TRANSMIT = True
    DUPLEX = True
