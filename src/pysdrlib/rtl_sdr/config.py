from ..base.config import ConfigDevice

class _ConfigRTL_SDR(ConfigDevice):
    __instance = None

    def __init__(self):
        super().__init__({})

ConfigRTL_SDR = _ConfigRTL_SDR()
