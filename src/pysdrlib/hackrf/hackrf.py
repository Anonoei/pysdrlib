from threading import Lock
import numpy as np

from ..base.device import Device
from .. import err, warn
from ..base.buffer import RotatingBuffer

from . import config
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
    CAN_TRANSMIT = True

    def __init__(self):
        lib.hackrf.init()
        self.device: lib.hackrf.device = None
        self._cf = 900_000_000
        self._Fs = 10_000_000

    def open(self, serial_number=None):
        try:
            if serial_number is None:
                device = lib.hackrf.open()
            else:
                device = lib.hackrf.open_by_serial(serial_number)
        except lib.err.NOT_FOUND as exc:
                raise err.NoDevice("No HackRF devices found!") from exc
        self.device = device
    def close(self):
        self.device.close()

    def start_rx(self):
        self.device.set_rx_callback(rx_callback)
        cb.reset(int(0.5*self._Fs))
        self.device.start_rx()
    def stop_rx(self):
        self.device.stop_rx()
    def get_samples(self):
        return cb.samples

    def set_sample_rate(self, Fs):
        self._Fs = Fs
        self.device.set_sample_rate(Fs)
    def set_freq(self, freq):
        self._cf = freq
        self.device.set_freq(freq)

    def set_rx_gain(self, gain=None):
        if gain is None:
            self.set_rx_rf_gain(config.DEFAULT_GAIN_RX_RF)
            self.set_rx_if_gain(config.DEFAULT_GAIN_RX_IF)
            self.set_rx_bb_gain(config.DEFAULT_GAIN_RX_BB)
        else:
            raise NotImplementedError() # TODO: split gain between rf/if, then bb
    def set_rx_rf_gain(self, gain):
        gain = self._check_gain(gain, "Rx RF", config.GAIN_RX_RF_STEP, config.GAIN_RX_IF_MIN, config.GAIN_RX_IF_MAX)
        gain = bool(gain)
        self.device.set_amp_enable(gain)
    def set_rx_if_gain(self, gain):
        gain = self._check_gain(gain, "Rx IF", config.GAIN_RX_IF_STEP, config.GAIN_RX_IF_MIN, config.GAIN_RX_IF_MAX)
        self.device.set_lna_gain(gain)
    def set_rx_bb_gain(self, gain):
        gain = self._check_gain(gain, "Rx BB", config.GAIN_RX_BB_STEP, config.GAIN_RX_BB_MIN, config.GAIN_RX_BB_MAX)
        self.device.set_vga_gain(gain)

    def get_tx_gain(self, gain=None):
        if gain is None:
            self.set_tx_rf_gain(config.DEFAULT_GAIN_TX_RF)
            self.set_tx_if_gain(config.DEFAULT_GAIN_TX_IF)
    def set_tx_rf_gain(self, gain):
        gain = self._check_gain(gain, "Rx BB", config.GAIN_TX_RF_STEP, config.GAIN_TX_RF_MIN, config.GAIN_TX_RF_MAX)
        gain = bool(gain)
        self.device.set_amp_enable(gain)
    def set_tx_if_gain(self, gain):
        gain = self._check_gain(gain, "Rx BB", config.GAIN_TX_IF_STEP, config.GAIN_TX_IF_MIN, config.GAIN_TX_IF_MAX)
        self.device.set_txvga_gain(gain)

    def set_bias_t(self, bias):
        self.device.set_antenna_enable(bias) # TODO: fix
