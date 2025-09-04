from ..base.device import Device
from .. import err, warn

class HackRF(Device):
    NAME = "HackRF"
    CAN_TRANSMIT = True

    DEFAULT_RX_RF = 0
    DEFAULT_RX_IF = 30
    DEFAULT_RX_BB = 50

    DEFAULT_TX_RF = 11
    DEFAULT_TX_IF = 0

    def set_sample_rate(self, Fs):
        pass

    def set_freq(self, freq):
        pass

    def set_antenna_enable(self, enable):
        pass

    def set_amp_enable(self, enable):
        pass

    def set_lna_gain(self, gain):
        """Set LNA gain

        Args:
            gain (int): LNA gain between 0-40 in 8 dB steps
        """
        if gain < 0:
            warn.InvalidValue("LNA Gain must greater than 0: setting to 0")
            gain = 0
        elif gain > 40:
            warn.InvalidValue("LNA Gain must be less than 40: setting to 40")
            gain = 40
        gain = gain + (gain % 8)

    def set_vga_gain(self, gain):
        """Set VGA (Tx) gain

        Args:
            gain (int): VGA gain between 0-62 in 2 dB steps
        """
        if gain < 0:
            warn.InvalidValue("VGA Gain must greater than 0: setting to 0")
            gain = 0
        elif gain > 62:
            warn.InvalidValue("VGA Gain must be less than 62: setting to 62")
            gain = 62
        gain = gain + (gain % 2)
