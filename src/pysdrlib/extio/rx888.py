from ..base.native import NativeDevice
from ..base import convert
from .. import err, warn

from .config import ConfigRX888 as config
from . import lib

class RX888(NativeDevice):
    NAME = "RX-888"
    CONFIG = config
    CAN_TRANSMIT = False
    DUPLEX = False
    SAMPLE_FORMAT = convert.FLOAT32 # the driver's DDC delivers interleaved float32 I/Q

    def __init__(self):
        super().__init__()
        self.device: lib.extio.device = None # pyright: ignore[reportAttributeAccessIssue]

    def _open(self):
        try:
            return lib.extio.open() # pyright: ignore[reportAttributeAccessIssue]
        except lib.err.NOT_FOUND as exc:
            raise err.NoDevice("No RX888 found, or no permission to access it (check your udev rules)") from exc

    def _set_sample_rate(self, Fs):
        actual = self.device.set_sample_rate(Fs)
        if actual != Fs:
            warn.InvalidValue(f"Sample rate must be one of {self.device.sample_rates()}, using {actual:,}")
            self._Fs = actual
        self._rate_changed()
    def _set_freq(self, freq):
        self.device.set_freq(int(freq))

    # Gain stages, in dB. Their meaning depends on the active input (chosen by the frequency, so set
    # the frequency first): HF direct sampling has an attenuator (RF) and a VGA (IF); VHF/UHF has the
    # tuner's RF and IF gain. The original RX888 has no IF stage on HF.
    def set_rx_rf_gain(self, gain=None):
        """RF attenuator (HF) / tuner RF gain (VHF), in dB"""
        return self._set_gain_stage("RX", "RF", gain, self._set_rx_rf_gain)
    def set_rx_if_gain(self, gain=None):
        """VGA (HF) / tuner IF gain (VHF), in dB"""
        return self._set_gain_stage("RX", "IF", gain, self._set_rx_if_gain)

    def _set_rx_gain(self, gain):
        """Total gain: the IF stage takes what it can, the rest goes to the RF stage (or attenuates it)"""
        conf = type(self).CONFIG
        if gain == "default":
            rf, if_ = conf.DEFAULT_GAIN_RX_RF, conf.DEFAULT_GAIN_RX_IF
        else:
            if_ = min(max(gain, conf.GAIN_RX_IF_MIN), conf.GAIN_RX_IF_MAX)
            rf = gain - if_
        return {"rf": self.set_rx_rf_gain(rf), "if": self.set_rx_if_gain(if_)}
    def _set_rx_rf_gain(self, gain):
        self.device.set_rx_rf_gain(float(gain))
    def _set_rx_if_gain(self, gain):
        self.device.set_rx_if_gain(float(gain))

    def _set_bias_t(self, bias):
        """Bias tee on the active input"""
        self.device.set_bias_t(bool(bias))

    # ADC controls
    def set_dither(self, enable):
        self.device.set_dither(bool(enable))
    def set_random(self, enable):
        self.device.set_random(bool(enable))
    def set_pga(self, enable):
        self.device.set_pga(bool(enable))
