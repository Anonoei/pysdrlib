from ..base.device import Device
from .. import err, warn

from .config import ConfigBladeRF as config

class BladeRF(Device):
    NAME = "BladeRF"
    CONFIG = config
    CAN_TRANSMIT = True
    DUPLEX = True
