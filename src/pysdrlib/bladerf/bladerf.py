from ..base.caps import Capabilities
from ..base.device import Device

class BladeRF(Device):
    NAME = "BladeRF"
    CAPS = Capabilities(driver="")
    CAN_TRANSMIT = True
    DUPLEX = True
