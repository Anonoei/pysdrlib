from ..base.config import ConfigDevice

class _ConfigRTL_SDR(ConfigDevice):
    __instance = None

    def __init__(self):
        super().__init__(
            {
                "DEFAULT": {
                    "FREQ":       100_000_000,
                    "SAMPLE_RATE":  2_400_000,

                    "GAIN_RX_RF": 30,
                },
                # The R820T/R820T2/R828D tuner on nearly every current dongle. The older E4000
                # (52 - 2200 MHz, with a gap) and FC00xx tuners differ; the driver rejects what they can't tune.
                "FREQ_MIN":     24_000_000,
                "FREQ_MAX":  1_766_000_000,
                # The RTL2832U accepts 225,001 - 300,000 and 900,001 - 3,200,000 Hz. Over about 2.56 MHz
                # a dongle on USB 2.0 usually drops samples.
                "SAMPLE_RATE_MIN":   225_001,
                "SAMPLE_RATE_MAX": 3_200_000,
                # Tuner gain in dB. It moves in uneven steps (R820T: 0, 0.9, 1.4, 2.7, ... 49.6), which the
                # driver snaps to the nearest of.
                "GAIN": {
                    "RX_RF_STEP": 0,
                    "RX_RF_MIN":  0,
                    "RX_RF_MAX": 50,
                },
            }
        )
        # Sample rates between these are not accepted (exclusive low, inclusive high)
        self.SAMPLE_RATE_GAP = (300_000, 900_001)

ConfigRTL_SDR = _ConfigRTL_SDR()
