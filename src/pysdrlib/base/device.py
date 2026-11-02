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
    SAMPLE_FORMAT = None # convert.SampleFormat of the raw stream the driver delivers (None: driver converts itself)

    __slots__ = (
        "log",
        "device", "state",
        "_cf", "_Fs"
    )
    def __init__(self):
        self.log = logger.new(f"device.{type(self).__name__}")
        self.device = None
        self.state = {
            "open": False,
            "rx": False,
            "tx": False
        }
        self._cf: float = type(self).CONFIG.DEFAULT_FREQ # type: ignore
        self._Fs: float = type(self).CONFIG.DEFAULT_SAMPLE_RATE # type: ignore

    def __str__(self):
        return f"{type(self).__name__}: {self.state}"

    def open(self, *args, **kwargs):
        """Connect to device"""
        if self.state["open"]:
            self.log.warning("Device is already open!")
            return
        self.log.trace("open(args:%s, kwargs:%s)", args, kwargs)
        self.device = self._open(*args, **kwargs)
        self.state["open"] = True
    def initialize(self, cf=None, Fs=None, rx_gain=None, tx_gain=None):
        """Initialize the device settings"""
        self.log.trace("initialize(cf=%s, Fs=%s, rx_gain=%s, tx_gain=%s)", cf, Fs, rx_gain, tx_gain)
        self._ensure_open("initialize")
        cf = self.set_freq(cf)
        Fs = self.set_sample_rate(Fs)
        rx_gain = self.set_rx_gain(rx_gain)
        tx_gain = self.set_tx_gain(tx_gain) if type(self).CAN_TRANSMIT else None
        self._initialize(cf, Fs, rx_gain, tx_gain)

    def close(self):
        """Disconnect from device"""
        self.log.trace("close()")
        self._ensure_open("close")
        self._close()
        self.state["open"] = False

    def __enter__(self):
        self.open()
        return self
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def start_rx(self):
        """Start receiving"""
        if self.state["rx"]:
            self.log.warning("Device is already receiving!")
            return
        self.log.trace("start_rx()")
        self._ensure_open("start_rx")
        self._start_rx()
        self.state["rx"] = True
    def stop_rx(self):
        """Stop receiving"""
        if not self.state["rx"]:
            self.log.warning("Device isn't receiving!")
            return
        self.log.trace("stop_rx()")
        self._ensure_open("stop_rx")
        self._stop_rx()
        self.state["rx"] = False
    def get_samples(self) -> np.ndarray:
        """Get samples from buffer"""
        # self.log.trace("get_samples()")
        self._ensure_open("get_samples")
        return self._get_samples()

    def start_tx(self):
        """Start transmitting"""
        if self.state["tx"]:
            self.log.warning("Device is already transmitting!")
            return
        self.log.trace("start_tx()")
        self._ensure_open("start_rx")
        self._start_tx()
        self.state["tx"] = True
    def stop_tx(self):
        """Stop transmitting"""
        if not self.state["tx"]:
            self.log.warning("Device isn't transmitting!")
            return
        self.log.trace("stop_tx()")
        self._ensure_open("stop_tx")
        self._stop_tx()
        self.state["tx"] = False

    def set_sample_rate(self, Fs=None):
        """Set sample rate"""
        self.log.trace("set_sample_rate(%s)", Fs)
        self._ensure_open("set_sample_rate")
        if Fs is None:
            Fs = type(self).CONFIG.DEFAULT_SAMPLE_RATE
        if Fs < type(self).CONFIG.SAMPLE_RATE_MIN:
            warn.InvalidValue(f"Sample rate must be greater than {type(self).CONFIG.SAMPLE_RATE_MIN:,}")
            Fs = type(self).CONFIG.SAMPLE_RATE_MIN
        elif Fs > type(self).CONFIG.SAMPLE_RATE_MAX:
            warn.InvalidValue(f"Sample rate must be less than {type(self).CONFIG.SAMPLE_RATE_MAX:,}")
            Fs = type(self).CONFIG.SAMPLE_RATE_MAX
        self._Fs = Fs
        self._set_sample_rate(Fs)
        return self._Fs
    def get_sample_rate(self):
        return self._Fs
    def set_freq(self, freq=None):
        """Set center frequency"""
        self.log.trace("set_freq(%s)", freq)
        self._ensure_open("set_freq")
        if freq is None:
            freq = type(self).CONFIG.DEFAULT_FREQ
        if freq < type(self).CONFIG.FREQ_MIN:
            warn.InvalidValue(f"Frequency must be greater than {type(self).CONFIG.FREQ_MIN:,}")
            freq = type(self).CONFIG.FREQ_MIN
        elif freq > type(self).CONFIG.FREQ_MAX:
            warn.InvalidValue(f"Frequency must be less than {type(self).CONFIG.FREQ_MAX:,}")
            freq = type(self).CONFIG.FREQ_MAX
        self._cf = freq
        self._set_freq(freq)
        return self._cf
    def get_freq(self):
        return self._cf
    def set_rx_gain(self, gain=None):
        """Set abstract receive gain"""
        self.log.trace("set_rx_gain(%s)", gain)
        self._ensure_open("set_rx_gain")
        if gain is None:
            gain = "default"
        else:
            gain = self._check_gain(gain, "RX")
        return self._set_rx_gain(gain)
    def set_tx_gain(self, gain=None):
        """Set abstract transmit gain"""
        self.log.trace("set_tx_gain(%s)", gain)
        self._ensure_open("set_tx_gain")
        if gain is None:
            gain = "default"
        else:
            gain = self._check_gain(gain, "TX")
        return self._set_tx_gain(gain)
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
    def _clamp_gain(self, gain, label, step, lo, hi):
        """Round `gain` down to a multiple of `step` and clamp it to [lo, hi], warning if changed"""
        if step:
            if gain % step:
                warn.InvalidValue(f"{label} gain must be a multiple of {step}")
                gain -= gain % step # default to lowering gain to next valid value
        if gain < lo:
            warn.InvalidValue(f"{label} gain must greater than {lo}")
            gain = lo
        elif gain > hi:
            warn.InvalidValue(f"{label} gain must be less than {hi}")
            gain = hi
        return gain

    def _check_gain(self, gain, direction):
        """Validate a total ("RX"/"TX") gain against the device's overall limits"""
        config = type(self).CONFIG
        return self._clamp_gain(gain, direction, None,
                                getattr(config, f"GAIN_{direction}_MIN"), getattr(config, f"GAIN_{direction}_MAX"))

    def _set_gain_stage(self, direction, stage, gain, setter):
        """For devices to build their own per-stage gain setters on.

        A device exposes whatever stages its hardware has, under its own names, e.g.

            def set_rx_mixer_gain(self, gain=None):
                return self._set_gain_stage("RX", "MIXER", gain, self._set_rx_mixer_gain)

        Handles the open check, the device's default, validation against the stage's limits declared
        in its config, and then calls `setter(validated_gain)`. Returns the gain that was set.
        """
        label = f"{direction} {stage}".replace("_", " ")
        self.log.trace("set_%s_%s_gain(%s)", direction.lower(), stage.lower(), gain)
        self._ensure_open(f"set_{direction.lower()}_{stage.lower()}_gain")
        spec = type(self).CONFIG.stage(direction, stage)
        if spec is None:
            raise NotImplementedError(f"{type(self).NAME} has no {label} gain stage")
        if gain is None:
            gain = spec.default
        gain = self._clamp_gain(gain, label, spec.step, spec.min, spec.max)
        setter(gain)
        return gain

    def _ensure_open(self, name):
        if not self.state["open"]:
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

    def _set_rx_gain(self, gain):
        raise NotImplementedError()

    def _set_tx_gain(self, gain):
        raise NotImplementedError()

    def _set_bias_t(self, bias):
        raise NotImplementedError()
