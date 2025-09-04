from ..base.device import Device
from .. import err, warn

class RTLSDR(Device):
    NAME = "RTL-SDR"
    def set_sample_rate(self, Fs):
        pass

    def set_freq(self, freq):
        pass

    def set_antenna_enable(self, enable):
        pass

    def set_amp_enable(self, enable):
        pass

    def set_gain(self, gain):
        pass
