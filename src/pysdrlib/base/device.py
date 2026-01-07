from .. import warn

class Device:
    NAME = "pysdrlib Device"
    CAN_TRANSMIT = False

    def open(self):
        """Open device"""
    def close(self):
        """Close device"""

    def __enter__(self):
        self.open()
        return self
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def start_rx(self):
        """Start receiving"""
    def stop_rx(self):
        """Stop receiving"""

    def set_sample_rate(self, Fs):
        """Set sample rate"""
    def set_freq(self, freq):
        """Set center frequency"""

    def read_samples(self, samples: int):
        """Read <samples> samples"""

    def _check_gain(self, gain, name, gstep, gmin, gmax):
        if gain % gstep:
            warn.InvalidValue(f"{name} gain must be a multiple of {gstep}")
            gain -= gain % gstep
        if gain < gmin:
            warn.InvalidValue(f"{name} gain must greater than {gmin}")
            gain = gmin
        elif gain > gmax:
            warn.InvalidValue(f"{name} gain must be less than {gmax}")
            gain = gmax
        return gain

    def set_rx_gain(self, gain=None):
        """Set generic receive gain"""
    def set_rx_rf_gain(self, gain):
        """Set receive RF gain"""
    def set_rx_if_gain(self, gain):
        """Set receive IF gain"""
    def set_rx_bb_gain(self, gain):
        """Set receive BB gain"""

    def set_tx_gain(self, gain=None):
        """Set generic transmit gain"""
    def set_tx_rf_gain(self, gain):
        """Set transmit RF gain"""
    def set_tx_if_gain(self, gain):
        """Set transmit IF gain"""
    def set_tx_bb_gain(self, gain):
        """Set transmit BB gain"""

    def set_bias_t(self, bias):
        """Set bias-t"""
