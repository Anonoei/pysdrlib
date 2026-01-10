import numpy as np

from .. import logger
from .. import warn
from .. import err
from .config import ConfigDevice

class Device:
    NAME = "pysdrlib Device"
    CONFIG: ConfigDevice = None # type: ignore
    CAN_TRANSMIT = False
    DUPLEX = False

    __slots__ = (
        "log",
        "device", "is_open",
        "_cf", "_Fs"
    )
    def __init__(self):
        self.log = logger.new(f"device.{type(self).__name__}")
        self.device = None
        self.is_open = False
        self._cf: float = type(self).CONFIG.DEFAULT_FREQ # type: ignore
        self._Fs: float = type(self).CONFIG.DEFAULT_SAMPLE_RATE # type: ignore

    def open(self, *args, **kwargs):
        """Connect to device"""
        self.log.trace("open(args:%s, kwargs:%s)", *args, **kwargs)
        self.device = self._open(*args, **kwargs)
    def initialize(self, cf=None, Fs=None, rx_gain=None, tx_gain=None):
        """Initialize the device settings"""
        self.log.trace("initialize(cf=%s, Fs=%s, rx_gain=%s, tx_gain=%s)", cf, Fs, rx_gain, tx_gain)
        self._ensure_open("initialize")
        self.set_freq(cf)
        self.set_sample_rate(Fs)
        self.set_rx_gain(rx_gain)
        self.set_tx_gain(tx_gain)
        self._initialize(cf, Fs, rx_gain, tx_gain)

    def close(self):
        """Disconnect from device"""
        self.log.trace("close()")
        self._ensure_open("close")
        self._close()

    def __enter__(self):
        self.open()
        return self
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def start_rx(self):
        """Start receiving"""
        self.log.trace("start_rx()")
        self._ensure_open("start_rx")
        self._start_rx()
    def stop_rx(self):
        """Stop receiving"""
        self.log.trace("stop_rx()")
        self._ensure_open("stop_rx")
        self._stop_rx()
    def get_samples(self) -> np.ndarray:
        """Get samples from buffer"""
        self.log.trace("get_samples()")
        self._ensure_open("get_samples")
        return self._get_samples()

    def start_tx(self):
        """Start transmitting"""
        self.log.trace("start_tx()")
        self._ensure_open("start_rx")
        self._start_tx()
    def stop_tx(self):
        """Stop transmitting"""
        self.log.trace("stop_tx()")
        self._ensure_open("stop_tx")
        self._stop_tx()

    def set_sample_rate(self, Fs=None):
        """Set sample rate"""
        self.log.trace("set_sample_rate(%s)", Fs)
        self._ensure_open("set_sample_rate")
        if Fs is None:
            Fs = type(self).CONFIG.DEFAULT_SAMPLE_RATE
        if Fs < type(self).CONFIG.SAMPLE_RATE_MIN:
            warn.InvalidValue(f"Sample rate must be greater than {type(self).CONFIG.SAMPLE_RATE_MIN}")
            Fs = type(self).CONFIG.SAMPLE_RATE_MIN
        elif Fs > type(self).CONFIG.SAMPLE_RATE_MAX:
            warn.InvalidValue(f"Sample rate must be less than {type(self).CONFIG.SAMPLE_RATE_MAX}")
            Fs = type(self).CONFIG.SAMPLE_RATE_MAX
        self._Fs = Fs
        self._set_sample_rate(Fs)
    def set_freq(self, freq=None):
        """Set center frequency"""
        self.log.trace("set_freq(%s)", freq)
        self._ensure_open("set_freq")
        if freq is None:
            freq = type(self).CONFIG.DEFAULT_FREQ
        if freq < type(self).CONFIG.FREQ_MIN:
            warn.InvalidValue(f"Frequency must be greater than {type(self).CONFIG.FREQ_MIN}")
            freq = type(self).CONFIG.FREQ_MIN
        elif freq > type(self).CONFIG.FREQ_MAX:
            warn.InvalidValue(f"Frequency must be less than {type(self).CONFIG.FREQ_MAX}")
            freq = type(self).CONFIG.FREQ_MAX
        self._cf = freq
        self._set_freq(freq)
    def set_freq_cf(self, freq):
        """Explicitly set center frequency"""
        self.log.trace("set_freq_cf(%s)", freq)
        self._ensure_open("set_freq_cf")
        self._set_freq_cf(freq)
    def set_freq_if(self, freq):
        """Explicitly set IF frequency"""
        self.log.trace("set_freq_if(%s)", freq)
        self._ensure_open("set_freq_if")
        self._set_freq_if(freq)
    def set_freq_lo(self, freq):
        """Explicitly set LO frequency"""
        self.log.trace("set_freq_lo(%s)", freq)
        self._ensure_open("set_freq_lo")
        self._set_freq_lo(freq)

    def set_rx_gain(self, gain=None):
        """Set abstract receive gain"""
        self.log.trace("set_rx_gain(%s)", gain)
        self._ensure_open("set_rx_gain")
        if gain is None:
            gain = "default"
        else:
            gain = self._check_gain(gain, "RX")
        self._set_rx_gain(gain)
    def set_rx_rf_gain(self, gain=None):
        """Set RF receive gain"""
        self.log.trace("set_rx_rf_gain(%s)", gain)
        self._ensure_open("set_rx_rf_gain")
        if not type(self).CONFIG.GAIN_RX_RF:
            raise NotImplementedError()
        if gain is None:
            gain = type(self).CONFIG.DEFAULT_GAIN_RX_RF
        gain = self._check_gain(gain, "Rx RF")
        self._set_rx_rf_gain(gain)
    def set_rx_if_gain(self, gain=None):
        """Set IF receive gain"""
        self.log.trace("set_rx_if_gain(%s)", gain)
        self._ensure_open("rx_rx_if_gain")
        if not type(self).CONFIG.GAIN_RX_IF:
            raise NotImplementedError()
        if gain is None:
            gain = type(self).CONFIG.DEFAULT_GAIN_RX_IF
        gain = self._check_gain(gain, "Rx IF")
        self._set_rx_rf_gain(gain)
    def set_rx_bb_gain(self, gain=None):
        """Set baseband receive gain"""
        self.log.trace("set_rx_bb_gain(%s)", gain)
        self._ensure_open("set_rx_bb_gain")
        if not type(self).CONFIG.GAIN_RX_BB:
            raise NotImplementedError()
        if gain is None:
            gain = type(self).CONFIG.DEFAULT_GAIN_RX_BB
        gain = self._check_gain(gain, "Rx BB")
        self._set_rx_rf_gain(gain)

    def set_tx_gain(self, gain=None):
        """Set abstract transmit gain"""
        self.log.trace("set_tx_gain(%s)", gain)
        self._ensure_open("set_rx_gain")
        if gain is None:
            gain = "default"
        else:
            gain = self._check_gain(gain, "TX")
        self._set_tx_gain(gain)
    def set_tx_rf_gain(self, gain=None):
        """Set RF transmit gain"""
        self.log.trace("set_tx_rf_gain(%s)", gain)
        self._ensure_open("set_rx_rf_gain")
        if not type(self).CONFIG.GAIN_TX_RF:
            raise NotImplementedError()
        if gain is None:
            gain = type(self).CONFIG.DEFAULT_GAIN_TX_RF
        gain = self._check_gain(gain, "Tx RF")
        self._set_rx_rf_gain(gain)
    def set_tx_if_gain(self, gain=None):
        """Set IF transmit gain"""
        self.log.trace("set_tx_if_gain(%s)", gain)
        self._ensure_open("set_tx_if_gain")
        if not type(self).CONFIG.GAIN_TX_IF:
            raise NotImplementedError()
        if gain is None:
            gain = type(self).CONFIG.DEFAULT_GAIN_TX_IF
        gain = self._check_gain(gain, "Tx RF")
        self._set_rx_rf_gain(gain)
    def set_tx_bb_gain(self, gain=None):
        """Set baseband transmit BB gain"""
        self.log.trace("set_tx_bb_gain(%s)", gain)
        self._ensure_open("set_tx_bb_gain")
        if not type(self).CONFIG.GAIN_TX_BB:
            raise NotImplementedError()
        if gain is None:
            gain = type(self).CONFIG.DEFAULT_GAIN_TX_BB
        gain = self._check_gain(gain, "Tx RF")
        self._set_rx_rf_gain(gain)

    def set_bias_t(self, bias):
        """Set bias-t"""
        self.log.trace("set_bias_t(%s)", bias)
        self._ensure_open("set_bias_t")
        self._set_bias_t(bias)

    @property
    def Fs(self):
        """get_sample_rate wrapper"""
        return self._Fs
    @Fs.setter
    def Fs(self, Fs):
        """set_sample_rate wrapper"""
        self.set_sample_rate(Fs)

    @property
    def CF(self):
        """get_freq wrapper"""
        return self._cf
    @CF.setter
    def CF(self, cf):
        """set_freq wrapper"""
        self.set_freq(cf)

    # --- Helpers/Utils --- #
    def _check_gain(self, gain, name):
        cname = name.upper().replace(" ", "_")
        gstep = getattr(type(self).CONFIG, f"GAIN_{cname}_STEP", None)
        gmin = getattr(type(self).CONFIG, f"GAIN_{cname}_MIN")
        gmax = getattr(type(self).CONFIG, f"GAIN_{cname}_MAX")
        if gstep is not None:
            if gain % gstep:
                warn.InvalidValue(f"{name} gain must be a multiple of {gstep}")
                gain -= gain % gstep # default to lowering gain to next valid value
        if gain < gmin:
            warn.InvalidValue(f"{name} gain must greater than {gmin}")
            gain = gmin
        elif gain > gmax:
            warn.InvalidValue(f"{name} gain must be less than {gmax}")
            gain = gmax
        return gain

    def _ensure_open(self, name):
        if not self.is_open:
            raise err.NotOpen(f"Cannot call `{name}` when device isn't open")

    # --- Defined in child classes --- #
    def _open(self, *args, **kwargs):
        raise NotImplementedError()
    def _close(self):
        raise NotImplementedError()

    def _start_rx(self):
        raise NotImplementedError()
    def _stop_rx(self):
        raise NotImplementedError()
    def _get_samples(self) -> np.ndarray:
        raise NotImplementedError()

    def _start_tx(self):
        pass
    def _stop_tx(self):
        pass

    def _initialize(self, cf, Fs, rx_gain, tx_gain):
        pass
    def _set_sample_rate(self, Fs):
        ...
    def _set_freq(self, freq):
        ...
    def _set_freq_cf(self, freq):
        raise NotImplementedError()
    def _set_freq_if(self, freq):
        raise NotImplementedError()
    def _set_freq_lo(self, freq):
        raise NotImplementedError()

    def _set_rx_gain(self, gain):
        raise NotImplementedError()
    def _set_rx_rf_gain(self, gain):
        raise NotImplementedError()
    def _set_rx_if_gain(self, gain):
        raise NotImplementedError()
    def _set_rx_bb_gain(self, gain):
        raise NotImplementedError()

    def _set_tx_gain(self, gain):
        raise NotImplementedError()
    def _set_tx_rf_gain(self, gain):
        raise NotImplementedError()
    def _set_tx_if_gain(self, gain):
        raise NotImplementedError()
    def _set_tx_bb_gain(self, gain):
        raise NotImplementedError()

    def _set_bias_t(self, bias):
        raise NotImplementedError()
