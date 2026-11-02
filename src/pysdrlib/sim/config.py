from ..base.config import ConfigDevice

class _ConfigSim(ConfigDevice):
    __instance = None

    def __init__(self):
        super().__init__(
            {
                "DEFAULT": {
                    "FREQ":       100_000_000,
                    "SAMPLE_RATE": 10_000_000,

                    "GAIN_RX_IF": 16,
                    "GAIN_RX_BB": 16,
                },
                "FREQ_MIN":             1,
                "FREQ_MAX": 10_000_000_000,
                "SAMPLE_RATE_MIN":      1_000,
                "SAMPLE_RATE_MAX": 200_000_000,
                "GAIN": {
                    "RX_IF_STEP": 1,
                    "RX_IF_MIN":  0,
                    "RX_IF_MAX": 40,
                    "RX_BB_STEP": 2,
                    "RX_BB_MIN":  0,
                    "RX_BB_MAX": 62,
                },
            }
        )

ConfigSim = _ConfigSim()
