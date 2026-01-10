from ..base.config import ConfigDevice

class _ConfigBladeRF(ConfigDevice):
    __instance = None

    def __init__(self):
        super().__init__({})

ConfigBladeRF = _ConfigBladeRF()
