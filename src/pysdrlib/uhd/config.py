from ..base.config import ConfigDevice

class _ConfigB210(ConfigDevice):
    __instance = None

    def __init__(self):
        super().__init__(
            {
                "DEFAULT": {
                    "FREQ":        915_000_000,
                    "SAMPLE_RATE":  10_000_000,

                    "GAIN_RX_PGA": 40,
                    "GAIN_TX_PGA": 40,
                },
                "FREQ_MIN":      50_000_000,
                "FREQ_MAX":   6_000_000_000,
                # 61.44 MS/s with one channel, 30.72 MS/s with two (USB 3 carries sc16 at that rate)
                "SAMPLE_RATE_MIN":    200_000,
                "SAMPLE_RATE_MAX": 61_440_000,
                "GAIN": {
                    # AD9361 PGA: the only gain the B210 exposes through UHD
                    "RX_PGA_STEP": 1,
                    "RX_PGA_MIN":  0,
                    "RX_PGA_MAX":  76,
                    "TX_PGA_STEP": 0.25,
                    "TX_PGA_MIN":  0,
                    "TX_PGA_MAX":  89.75,
                },
            }
        )
        self.SAMPLE_RATE_MAX_DUAL = 30_720_000
        self.BANDWIDTH_MIN = 200_000
        self.BANDWIDTH_MAX = 56_000_000
        self.MASTER_CLOCK_MIN = 5_000_000
        self.MASTER_CLOCK_MAX = 61_440_000
        self.MASTER_CLOCK_MAX_DUAL = 30_720_000   # with two channels in a stream (UHD refuses more)
        self.MASTER_CLOCK_GOOD_MIN = 25_000_000   # below this the AD9361's converters are outside their best range
        self.DEFAULT_MASTER_CLOCK = None   # None: UHD picks one for the sample rate ("automatic")
        self.CLOCK_SOURCES = ("internal", "external", "gpsdo")
        self.TIME_SOURCES = ("none", "internal", "external", "gpsdo")
        self.ANTENNAS_RX = ("TX/RX", "RX2")
        self.ANTENNAS_TX = ("TX/RX",)

ConfigB210 = _ConfigB210()
