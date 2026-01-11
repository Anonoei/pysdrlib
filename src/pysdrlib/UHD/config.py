from ..base.config import ConfigDevice

class _ConfigB210(ConfigDevice):
    __instance = None

    def __init__(self):
        super().__init__({"DEFAULT": {}, "GAIN": {}})

ConfigB210 = _ConfigB210()
