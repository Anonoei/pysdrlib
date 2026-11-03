from ..base.config import ConfigDevice

class _ConfigRX888(ConfigDevice):
    __instance = None

    def __init__(self):
        super().__init__(
            {
                "DEFAULT": {
                    "FREQ":        10_000_000,
                    "SAMPLE_RATE":  8_000_000,

                    "GAIN_RX_RF": 0,
                    "GAIN_RX_IF": 0,
                },
                "FREQ_MIN":        10_000,
                "FREQ_MAX": 1_750_000_000,
                # The ADC runs at 64 MHz and the DDC decimates by powers of two
                "SAMPLE_RATE_MIN":  2_000_000,
                "SAMPLE_RATE_MAX": 32_000_000,
                # Both stages are in dB and move in model- and input-dependent steps (HF: attenuator
                # -31.5..0, VGA about -25..33; VHF: tuner 0..49.6 and -4.7..40.8). These are the outer
                # limits across inputs; the driver snaps to the nearest step of the active one.
                "GAIN": {
                    "RX_RF_STEP": 0,
                    "RX_RF_MIN": -32,
                    "RX_RF_MAX":  50,
                    "RX_IF_STEP": 0,
                    "RX_IF_MIN": -25,
                    "RX_IF_MAX":  41,
                },
            }
        )

ConfigRX888 = _ConfigRX888()
