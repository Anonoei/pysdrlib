from ..base.config import ConfigDevice

class _ConfigRTL_SDR(ConfigDevice):
    __instance = None

    def __init__(self):
        super().__init__({"DEFAULT": {}, "GAIN": {}})

ConfigRTL_SDR = _ConfigRTL_SDR()
