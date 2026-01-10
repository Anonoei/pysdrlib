from ..base.config import ConfigDevice

class _ConfigRX888(ConfigDevice):
    __instance = None

    def __init__(self):
        super().__init__({})

ConfigRX888 = _ConfigRX888()
