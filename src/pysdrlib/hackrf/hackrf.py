from threading import Lock
import numpy as np

from ..base.device import Device
from .. import err, warn
from ..base.buffer import RotatingBuffer

from .config import ConfigHackRF as config
from . import lib

cb = RotatingBuffer()

def rx_callback(buffer, valid_length, buffer_length):
    accepted_samples = buffer[:valid_length].astype(np.int8) # -128 to 127
    accepted_samples = accepted_samples[0::2] + 1j * accepted_samples[1::2]  # Convert to complex type (de-interleave the IQ)
    accepted_samples /= 128 # -1 to +1

    cb.update(accepted_samples)
    return 0

class HackRF(Device):
    NAME = "HackRF"
    CONFIG = config
    CAN_TRANSMIT = True
    DUPLEX = False

    def __init__(self):
        super().__init__()
        lib.hackrf.init()
        self.device: lib.hackrf.device = None

    def _open(self, serial_number=None):
        try:
            if serial_number is None:
                device = lib.hackrf.open()
            else:
                device = lib.hackrf.open_by_serial(serial_number)
        except lib.err.NOT_FOUND as exc:
                raise err.NoDevice("No HackRF devices found!") from exc
        self.device = device

    def _close(self):
        self.device.close()

    def _start_rx(self):
        self.device.set_rx_callback(rx_callback)
        cb.reset(int(0.5*self._Fs))
        self.device.start_rx()
    def _stop_rx(self):
        self.device.stop_rx()
    def _get_samples(self):
        return cb.samples

    def _set_sample_rate(self, Fs):
        self.device.set_sample_rate(Fs)
    def _set_freq(self, freq):
        self.device.set_freq(freq)

    def _set_rx_gain(self, gain):
        if gain == "default":
            self.set_rx_rf_gain(type(self).CONFIG.DEFAULT_GAIN_RX_RF)
            self.set_rx_if_gain(type(self).CONFIG.DEFAULT_GAIN_RX_IF)
            self.set_rx_bb_gain(type(self).CONFIG.DEFAULT_GAIN_RX_BB)
        else:
            print(f"Set gain: {gain}")
            if gain > (type(self).CONFIG.GAIN_RX_IF_MAX + type(self).CONFIG.GAIN_RX_BB_MAX):
                self.set_rx_rf_gain(type(self).CONFIG.GAIN_RX_RF_MAX) # enable amp
                gain -= type(self).CONFIG.GAIN_RX_RF_MAX
                print(f"Enabled amplifier")
            hgain = gain//2
            if hgain > type(self).CONFIG.GAIN_RX_IF_MAX:
                if_gain = type(self).CONFIG.GAIN_RX_IF_MAX
                bb_gain = gain - if_gain
            else:
                if_gain = hgain - (hgain % type(self).CONFIG.GAIN_RX_IF_STEP)
                bb_gain = hgain - (hgain % type(self).CONFIG.GAIN_RX_BB_STEP)
            self.set_rx_if_gain(if_gain)
            self.set_rx_bb_gain(bb_gain)
    def _set_rx_rf_gain(self, gain):
        gain = bool(gain)
        # self.device.set_amp_enable(gain)
    def _set_rx_if_gain(self, gain):
        pass
        # self.device.set_lna_gain(gain)
    def _set_rx_bb_gain(self, gain):
        pass
        # self.device.set_vga_gain(gain)

    def _set_tx_gain(self, gain):
        if gain == "default":
            self.set_tx_rf_gain(type(self).CONFIG.DEFAULT_GAIN_TX_RF)
            self.set_tx_if_gain(type(self).CONFIG.DEFAULT_GAIN_TX_IF)
        else:
            if gain > type(self).CONFIG.GAIN_RX_IF_MAX:
                self.set_tx_rf_gain(type(self).CONFIG.GAIN_TX_RF_MAX) # enable amp
                gain -= type(self).CONFIG.GAIN_TX_RF_MAX
            self.set_tx_if_gain(gain)
    def _set_tx_rf_gain(self, gain):
        gain = bool(gain)
        # self.device.set_amp_enable(gain)
    def _set_tx_if_gain(self, gain):
        pass
        # self.device.set_txvga_gain(gain)

    def set_bias_t(self, bias):
        self.device.set_antenna_enable(bias) # TODO: fix
