from ..base.config import ConfigDevice

class _ConfigHackRF(ConfigDevice):
    __instance = None

    def __init__(self):
        super().__init__(
            {
                "DEFAULT": {
                    "FREQ":       900_000_000,
                    "SAMPLE_RATE": 10_000_000,

                    "GAIN_RX_RF": 0,
                    "GAIN_RX_IF": 16,
                    "GAIN_RX_BB": 16,
                    "GAIN_TX_RF": 0,
                    "GAIN_TX_IF": 0,
                },
                "FREQ_MIN":    1_000_000,
                "FREQ_MAX": 6000_000_000,
                "SAMPLE_RATE_MIN": 2_000_000,
                "SAMPLE_RATE_MAX": 20_000_000,
                "GAIN": {
                    "RX_RF_STEP": 11, # RF amplifier, both Tx and Rx
                    "RX_RF_MIN":   0,
                    "RX_RF_MAX":  11,
                    "RX_IF_STEP": 8, # LNA gain
                    "RX_IF_MIN":  0,
                    "RX_IF_MAX": 40,
                    "RX_BB_STEP": 2, # VGA gain
                    "RX_BB_MIN":  0,
                    "RX_BB_MAX": 62,
                    "TX_RF_STEP": 11, # RF amplifier
                    "TX_RF_MIN":   0,
                    "TX_RF_MAX":  11,
                    "TX_IF_STEP": 1, # TXVGA gain
                    "TX_IF_MIN":  0,
                    "TX_IF_MAX": 47,
                },
            }
        )
        self.FREQ_MIN_ABS =            0
        self.FREQ_MAX_ABS = 7250_000_000

        self.IF_MIN       = 2170_000_000
        self.IF_MAX       = 2740_000_000
        self.IF_MIN_ABS   = 2000_000_000
        self.IF_MAX_ABS   = 3000_000_000

        self.LO_MIN       =   84_375_000
        self.LO_MAX       = 5400_000_000

        self.BASEBAND_FILTER_MIN =  1_750_000
        self.BASEBAND_FILTER_MAX = 28_000_000
        self.BASEBAND_FILTERS = (
            1_750_000,  2_500_000,  3_500_000,  5_000_000,
            5_500_000,  6_000_000,  7_000_000,  8_000_000,
            9_000_000, 10_000_000, 12_000_000, 14_000_000,
            15_000_000, 20_000_000, 24_000_000, 28_000_000
        )

        self.DEFAULT_LO   = 1000_000_000
        self.DEFAULT_BASEBAND_FILTER_BANDWIDTH = 5_000_000

ConfigHackRF = _ConfigHackRF()
