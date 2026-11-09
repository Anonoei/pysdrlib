from ..base.native import NativeDevice
from ..base import convert
from ..base.caps import Range
from .. import err

from .config import ConfigHackRF as config
from . import lib

class HackRF(NativeDevice):
    NAME = "HackRF"
    CONFIG = config
    CAN_TRANSMIT = True
    DUPLEX = False
    SAMPLE_FORMAT = convert.INT8 # libhackrf delivers interleaved signed 8-bit I/Q
    # After a retune: the 256 KiB USB transfer being filled (131072 samples) still holds samples from before it,
    # and the synthesizer settles ~0.6 ms later. Measured on a HackRF One (firmware 2024.02.1) at 2, 10 and 20 MS/s,
    # retuning at random moments onto an FM station: at most 131072 + 12352 samples after the mark (20 MS/s);
    # skipping one transfer + 5 ms gave 120/120 clean blocks
    RETUNE_SAMPLES = 131072
    RETUNE_SETTLE = 0.005
    # Great Scott Gadgets: start at RF off, IF 16, baseband 16 (the defaults); raise or lower IF and baseband roughly
    # equally; turn the RF amp on when weak signals need it. So a total keeps IF and baseband about equal in dB, and
    # the 11 dB amps are used only when the LNA/VGA (TXVGA) can't reach it
    GAIN_RESERVE = {"rx": ("RF",), "tx": ("RF",)}
    GAIN_BALANCE = "db"

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
    # Device-specific controls; a total (`set_gain`, `set_rx_gain`) is split across them by Device.split_gain.
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

    def _set_rx_rf_gain(self, gain):
        gain = bool(gain)
        self.device.set_amp_enable(gain)
    def _set_rx_if_gain(self, gain):
        self.device.set_lna_gain(int(gain))
    def _set_rx_bb_gain(self, gain):
        self.device.set_vga_gain(int(gain))

    def _set_tx_rf_gain(self, gain):
        gain = bool(gain)
        self.device.set_amp_enable(gain)
    def _set_tx_if_gain(self, gain):
        self.device.set_txvga_gain(int(gain))

    def _set_bias_t(self, bias):
        self.device.set_antenna_enable(bool(bias))

    # --- generic controls --- #
    def _capabilities(self, caps):
        filters = [Range(bw, bw) for bw in type(self).CONFIG.BASEBAND_FILTERS]
        caps.bandwidths = {"rx": filters, "tx": list(filters)}
        try:
            caps.info["part_id"], caps.info["serial"] = self.device.board_partid_serialno_read()
        except Exception:
            pass
        for key, read in (("board_id", "board_id_read"), ("firmware", "version_string_read")):
            try:
                caps.info[key] = getattr(self.device, read)()
            except Exception:
                pass
        return caps

    def _set_bandwidth(self, direction, bandwidth):
        # One baseband filter serves both directions
        self.device.set_baseband_filter_bandwidth(int(bandwidth))
        return bandwidth

