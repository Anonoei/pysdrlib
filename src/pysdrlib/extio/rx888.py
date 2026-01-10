from ..base.device import Device
from .. import err, warn

from .config import ConfigRX888 as config

class RX888(Device):
    NAME = "RX-888"
    CONFIG = config
    CAN_TRANSMIT = False
    DUPLEX = False
