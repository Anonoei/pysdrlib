from ..base.native import NativeDevice
from ..base import convert
from .. import err, warn

from .config import ConfigRTL_SDR as config
from . import lib

class RTLSDR(NativeDevice):
    NAME = "RTL-SDR"
    CONFIG = config
    CAN_TRANSMIT = False
    DUPLEX = False
    SAMPLE_FORMAT = convert.UINT8 # the RTL2832U delivers interleaved unsigned 8-bit I/Q, offset binary

    def __init__(self):
        super().__init__()
        self.device: lib.rtl_sdr.device = None # pyright: ignore[reportAttributeAccessIssue]

    def _open(self, serial_number=None, index=0):
        """Open the dongle with this serial number, or else the `index`th one attached"""
        try:
            if serial_number is None:
                return lib.rtl_sdr.open(index) # pyright: ignore[reportAttributeAccessIssue]
            return lib.rtl_sdr.open_by_serial(serial_number) # pyright: ignore[reportAttributeAccessIssue]
        except lib.err.NOT_FOUND as exc:
            raise err.NoDevice("No RTL-SDR devices found!") from exc
        except lib.err.OPEN_FAILED as exc:
            raise err.NoDevice(str(exc)) from exc

    def _set_sample_rate(self, Fs):
        low, high = type(self).CONFIG.SAMPLE_RATE_GAP
        if low < Fs < high: # the hardware has no rates between these
            snapped = low if Fs - low < high - Fs else high
            warn.InvalidValue(f"Sample rate must be at most {low:,} or at least {high:,}, using {snapped:,}")
            self._Fs = Fs = snapped
        actual = self.device.set_sample_rate(int(Fs))
        if abs(actual - Fs) >= 1:
            warn.InvalidValue(f"Sample rate {Fs:,} is not available, using {actual:,}")
            self._Fs = actual
        self._rate_changed()
    def _set_freq(self, freq):
        self.device.set_freq(int(freq))

    # The tuner has a single gain control, with discrete steps; the driver snaps to the nearest.
    # `set_rx_gain` is the same control.
    def set_rx_rf_gain(self, gain=None):
        """Tuner gain, 0-49.6 dB. Turns automatic gain off"""
        return self._set_gain_stage("RX", "RF", gain, self._set_rx_rf_gain)

    def _set_rx_gain(self, gain):
        if gain == "default":
            gain = type(self).CONFIG.DEFAULT_GAIN_RX_RF
        return {"rf": self.set_rx_rf_gain(gain)}
    def _set_rx_rf_gain(self, gain):
        self.device.set_rx_rf_gain(float(gain))

    def set_agc(self, enable):
        """Let the tuner choose its gain, or (False) return to the gain last set"""
        self._ensure_open("set_agc")
        self.device.set_agc(bool(enable))

    def _set_bias_t(self, bias):
        """Bias tee on the antenna port (RTL-SDR Blog V3 and V4 only; no effect on other dongles)"""
        self.device.set_bias_t(bool(bias))

    def set_freq_correction(self, ppm):
        """Correct for the crystal's frequency error, in parts per million"""
        self._ensure_open("set_freq_correction")
        self.device.set_freq_correction(int(ppm))
