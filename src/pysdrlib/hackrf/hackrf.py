from ..base.native import NativeDevice
from ..base import convert
from .. import err

from .config import ConfigHackRF as config
from . import lib

class HackRF(NativeDevice):
    NAME = "HackRF"
    CONFIG = config
    CAN_TRANSMIT = True
    DUPLEX = False
    SAMPLE_FORMAT = convert.INT8 # libhackrf delivers interleaved signed 8-bit I/Q

    def __init__(self):
        super().__init__()
        lib.hackrf.init() # pyright: ignore[reportAttributeAccessIssue]
        self.device: lib.hackrf.device = None # pyright: ignore[reportAttributeAccessIssue]

    def _open(self, serial_number=None):
        try:
            if serial_number is None:
                device = lib.hackrf.open() # pyright: ignore[reportAttributeAccessIssue]
            else:
                device = lib.hackrf.open_by_serial(serial_number) # pyright: ignore[reportAttributeAccessIssue]
        except lib.err.NOT_FOUND as exc:
                raise err.NoDevice("No HackRF devices found!") from exc
        return device

    def _set_sample_rate(self, Fs):
        self.device.set_sample_rate(int(Fs))
        self._rate_changed()
    def _set_freq(self, freq):
        self.device.set_freq(int(freq))

    # HackRF gain stages: RF amp, IF (LNA), baseband (VGA), plus the TX amp and TXVGA.
    # Device-specific controls; `set_rx_gain`/`set_tx_gain` split a total across them.
    def set_rx_rf_gain(self, gain=None):
        """RF amp (0 or 11 dB)"""
        return self._set_gain_stage("RX", "RF", gain, self._set_rx_rf_gain)
    def set_rx_if_gain(self, gain=None):
        """IF (LNA) gain, 0-40 dB in 8 dB steps"""
        return self._set_gain_stage("RX", "IF", gain, self._set_rx_if_gain)
    def set_rx_bb_gain(self, gain=None):
        """Baseband (VGA) gain, 0-62 dB in 2 dB steps"""
        return self._set_gain_stage("RX", "BB", gain, self._set_rx_bb_gain)
    def set_tx_rf_gain(self, gain=None):
        """RF amp (0 or 11 dB)"""
        return self._set_gain_stage("TX", "RF", gain, self._set_tx_rf_gain)
    def set_tx_if_gain(self, gain=None):
        """TXVGA gain, 0-47 dB in 1 dB steps"""
        return self._set_gain_stage("TX", "IF", gain, self._set_tx_if_gain)

    def _set_rx_gain(self, gain):
        rtr = {"rf": None, "if": None, "bb": None}
        if gain == "default":
            rtr["rf"] = self.set_rx_rf_gain(type(self).CONFIG.DEFAULT_GAIN_RX_RF)
            rtr["if"] = self.set_rx_if_gain(type(self).CONFIG.DEFAULT_GAIN_RX_IF)
            rtr["bb"] = self.set_rx_bb_gain(type(self).CONFIG.DEFAULT_GAIN_RX_BB)
        else:
            if gain > (type(self).CONFIG.GAIN_RX_IF_MAX + type(self).CONFIG.GAIN_RX_BB_MAX):
                rtr["rf"] = self.set_rx_rf_gain(type(self).CONFIG.GAIN_RX_RF_MAX) # enable amp
                gain -= type(self).CONFIG.GAIN_RX_RF_MAX
            hgain = gain//2
            if hgain > type(self).CONFIG.GAIN_RX_IF_MAX:
                if_gain = type(self).CONFIG.GAIN_RX_IF_MAX
                bb_gain = gain - if_gain
            else:
                if_gain = hgain - (hgain % type(self).CONFIG.GAIN_RX_IF_STEP)
                bb_gain = hgain - (hgain % type(self).CONFIG.GAIN_RX_BB_STEP)
            rtr["if"] = self.set_rx_if_gain(if_gain)
            rtr["bb"] = self.set_rx_bb_gain(bb_gain)
        return rtr
    def _set_rx_rf_gain(self, gain):
        gain = bool(gain)
        self.device.set_amp_enable(gain)
    def _set_rx_if_gain(self, gain):
        self.device.set_lna_gain(int(gain))
    def _set_rx_bb_gain(self, gain):
        self.device.set_vga_gain(int(gain))

    def _set_tx_gain(self, gain):
        rtr = {"rf": None, "if": None}
        if gain == "default":
            rtr["rf"] = self.set_tx_rf_gain(type(self).CONFIG.DEFAULT_GAIN_TX_RF)
            rtr["if"] = self.set_tx_if_gain(type(self).CONFIG.DEFAULT_GAIN_TX_IF)
        else:
            if gain > type(self).CONFIG.GAIN_TX_IF_MAX: # TXVGA can't supply it alone: enable the amp
                rtr["rf"] = self.set_tx_rf_gain(type(self).CONFIG.GAIN_TX_RF_MAX) # enable amp
                gain -= type(self).CONFIG.GAIN_TX_RF_MAX
            rtr["if"] = self.set_tx_if_gain(gain)
        return rtr
    def _set_tx_rf_gain(self, gain):
        gain = bool(gain)
        self.device.set_amp_enable(gain)
    def _set_tx_if_gain(self, gain):
        self.device.set_txvga_gain(int(gain))

    def _set_bias_t(self, bias):
        self.device.set_antenna_enable(bool(bias))
