from .. import logger

class ConfigDevice:
    __instance = None
    __slots__ = (
        "_log",
        "FREQ_MIN", "FREQ_MAX",
        "SAMPLE_RATE_MIN", "SAMPLE_RATE_MAX",
        "GAIN_RX_RF", "GAIN_RX_RF_STEP", "GAIN_RX_RF_MIN", "GAIN_RX_RF_MAX",
        "GAIN_RX_IF", "GAIN_RX_IF_STEP", "GAIN_RX_IF_MIN", "GAIN_RX_IF_MAX",
        "GAIN_RX_BB", "GAIN_RX_BB_STEP", "GAIN_RX_BB_MIN", "GAIN_RX_BB_MAX",
        "GAIN_RX_MIN", "GAIN_RX_MAX",
        "GAIN_TX_RF", "GAIN_TX_RF_STEP", "GAIN_TX_RF_MIN", "GAIN_TX_RF_MAX",
        "GAIN_TX_IF", "GAIN_TX_IF_STEP", "GAIN_TX_IF_MIN", "GAIN_TX_IF_MAX",
        "GAIN_TX_BB", "GAIN_TX_BB_STEP", "GAIN_TX_BB_MIN", "GAIN_TX_BB_MAX",
        "GAIN_TX_MIN", "GAIN_TX_MAX",
        "DEFAULT_FREQ", "DEFAULT_SAMPLE_RATE",
        "DEFAULT_GAIN_RX", "DEFAULT_GAIN_RX_RF", "DEFAULT_GAIN_RX_IF", "DEFAULT_GAIN_RX_BB",
        "DEFAULT_GAIN_TX", "DEFAULT_GAIN_TX_RF", "DEFAULT_GAIN_TX_IF", "DEFAULT_GAIN_TX_BB",
    )
    def __new__(cls, *args, **kwargs):
        if cls.__instance is None:
            cls.__instance = super().__new__(cls)
            cls.__instance.__init__(*args, **kwargs)
        return cls.__instance

    def _pop(self, sets, name, default=None):
        v = sets.get(name, default)
        # self._log.trace(f"Setting {name} = {v}")
        if v is not default:
            del sets[name]
        return v

    def __init__(self, sets):
        self._log = logger.new(f"device.{type(self).__name__}")
        self.FREQ_MIN = self._pop(sets, "FREQ_MIN")
        self.FREQ_MAX = self._pop(sets, "FREQ_MAX")

        self.SAMPLE_RATE_MIN = self._pop(sets, "SAMPLE_RATE_MIN")
        self.SAMPLE_RATE_MAX = self._pop(sets, "SAMPLE_RATE_MAX")

        self.GAIN_RX_RF_STEP = self._pop(sets["GAIN"], "RX_RF_STEP")
        self.GAIN_RX_RF_MIN =  self._pop(sets["GAIN"], "RX_RF_MIN")
        self.GAIN_RX_RF_MAX =  self._pop(sets["GAIN"], "RX_RF_MAX")
        self.GAIN_RX_RF = not None in (self.GAIN_RX_RF_STEP, self.GAIN_RX_RF_MIN, self.GAIN_RX_RF_MAX)

        self.GAIN_RX_IF_STEP = self._pop(sets["GAIN"], "RX_IF_STEP")
        self.GAIN_RX_IF_MIN =  self._pop(sets["GAIN"], "RX_IF_MIN")
        self.GAIN_RX_IF_MAX =  self._pop(sets["GAIN"], "RX_IF_MAX")
        self.GAIN_RX_IF = not None in (self.GAIN_RX_IF_STEP, self.GAIN_RX_IF_MIN, self.GAIN_RX_IF_MAX)

        self.GAIN_RX_BB_STEP = self._pop(sets["GAIN"], "RX_BB_STEP")
        self.GAIN_RX_BB_MIN =  self._pop(sets["GAIN"], "RX_BB_MIN")
        self.GAIN_RX_BB_MAX =  self._pop(sets["GAIN"], "RX_BB_MAX")
        self.GAIN_RX_BB = not None in (self.GAIN_RX_BB_STEP, self.GAIN_RX_BB_MIN, self.GAIN_RX_BB_MAX)

        self.GAIN_RX_MIN =    (self.GAIN_RX_RF_MIN if self.GAIN_RX_RF else 0) \
                            + (self.GAIN_RX_IF_MIN if self.GAIN_RX_IF else 0) \
                            + (self.GAIN_RX_BB_MIN if self.GAIN_RX_BB else 0)
        self.GAIN_RX_MAX =    (self.GAIN_RX_RF_MAX if self.GAIN_RX_RF else 0) \
                            + (self.GAIN_RX_IF_MAX if self.GAIN_RX_IF else 0) \
                            + (self.GAIN_RX_BB_MAX if self.GAIN_RX_BB else 0)

        self.GAIN_TX_RF_STEP = self._pop(sets["GAIN"], "TX_RF_STEP")
        self.GAIN_TX_RF_MIN =  self._pop(sets["GAIN"], "TX_RF_MIN")
        self.GAIN_TX_RF_MAX =  self._pop(sets["GAIN"], "TX_RF_MAX")
        self.GAIN_TX_RF = not None in (self.GAIN_TX_RF_STEP, self.GAIN_TX_RF_MIN, self.GAIN_TX_RF_MAX)

        self.GAIN_TX_IF_STEP = self._pop(sets["GAIN"], "TX_IF_STEP")
        self.GAIN_TX_IF_MIN =  self._pop(sets["GAIN"], "TX_IF_MIN")
        self.GAIN_TX_IF_MAX =  self._pop(sets["GAIN"], "TX_IF_MAX")
        self.GAIN_TX_IF = not None in (self.GAIN_TX_IF_STEP, self.GAIN_TX_IF_MIN, self.GAIN_TX_IF_MAX)

        self.GAIN_TX_BB_STEP = self._pop(sets["GAIN"], "TX_BB_STEP")
        self.GAIN_TX_BB_MIN =  self._pop(sets["GAIN"], "TX_BB_MIN")
        self.GAIN_TX_BB_MAX =  self._pop(sets["GAIN"], "TX_BB_MAX")
        self.GAIN_TX_BB = not None in (self.GAIN_TX_BB_STEP, self.GAIN_TX_BB_MIN, self.GAIN_TX_BB_MAX)

        self.GAIN_TX_MIN =    (self.GAIN_TX_RF_MIN if self.GAIN_TX_RF else 0) \
                            + (self.GAIN_TX_IF_MIN if self.GAIN_TX_IF else 0) \
                            + (self.GAIN_TX_BB_MIN if self.GAIN_TX_BB else 0)
        self.GAIN_TX_MAX =    (self.GAIN_TX_RF_MAX if self.GAIN_TX_RF else 0) \
                            + (self.GAIN_TX_IF_MAX if self.GAIN_TX_IF else 0) \
                            + (self.GAIN_TX_BB_MAX if self.GAIN_TX_BB else 0)

        self.DEFAULT_FREQ =        self._pop(sets["DEFAULT"], "FREQ")
        self.DEFAULT_SAMPLE_RATE = self._pop(sets["DEFAULT"], "SAMPLE_RATE")

        self.DEFAULT_GAIN_RX_RF = self._pop(sets["DEFAULT"], "GAIN_RX_RF")
        self.DEFAULT_GAIN_RX_IF = self._pop(sets["DEFAULT"], "GAIN_RX_IF")
        self.DEFAULT_GAIN_RX_BB = self._pop(sets["DEFAULT"], "GAIN_RX_BB")
        self.DEFAULT_GAIN_RX =    (self.DEFAULT_GAIN_RX_RF if self.GAIN_RX_RF else 0) \
                                + (self.DEFAULT_GAIN_RX_IF if self.GAIN_RX_IF else 0) \
                                + (self.DEFAULT_GAIN_RX_BB if self.GAIN_RX_BB else 0)

        self.DEFAULT_GAIN_TX_RF = self._pop(sets["DEFAULT"], "GAIN_TX_RF")
        self.DEFAULT_GAIN_TX_IF = self._pop(sets["DEFAULT"], "GAIN_TX_IF")
        self.DEFAULT_GAIN_TX_BB = self._pop(sets["DEFAULT"], "GAIN_TX_BB")
        self.DEFAULT_GAIN_TX =    (self.DEFAULT_GAIN_TX_RF if self.GAIN_TX_RF else 0) \
                                + (self.DEFAULT_GAIN_TX_IF if self.GAIN_TX_IF else 0) \
                                + (self.DEFAULT_GAIN_TX_BB if self.GAIN_TX_BB else 0)

    def json(self):
        rtr = {
            "DEFAULT": {},
            "GAIN": {}
        }
        for key in self.__slots__:
            if key.startswith("_"):
                continue
            if key.startswith("DEFAULT_"):
                rtr["DEFAULT"][key[8:]] = getattr(self, key)
            elif key.startswith("GAIN_"):
                rtr["GAIN"][key[5:]] = getattr(self, key)
            else:
                rtr[key] = getattr(self, key)
        return rtr
