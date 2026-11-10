from ..base.native import NativeDevice
from ..base import convert
from ..base.caps import Capabilities, GainStage, Range
from .. import err

from . import lib

# The analog baseband filter bandwidths [Hz] the MAX2837 has
BASEBAND_FILTERS = (
    1_750_000,  2_500_000,  3_500_000,  5_000_000,
    5_500_000,  6_000_000,  7_000_000,  8_000_000,
    9_000_000, 10_000_000, 12_000_000, 14_000_000,
    15_000_000, 20_000_000, 24_000_000, 28_000_000,
)

class HackRF(NativeDevice):
    NAME = "HackRF"
    CAPS = Capabilities(
        driver="",
        freq=[Range(1_000_000, 6_000_000_000)], default_freq=900_000_000,
        rates=[Range(2_000_000, 20_000_000)], default_rate=10_000_000,
        gains={
            "rx": {"RF": GainStage(11, 0, 11, 0),    # RF amplifier, both Tx and Rx
                   "IF": GainStage(8, 0, 40, 16),    # LNA
                   "BB": GainStage(2, 0, 62, 16)},   # VGA
            "tx": {"RF": GainStage(11, 0, 11, 0),    # RF amplifier
                   "IF": GainStage(1, 0, 47, 0)},    # TXVGA
        },
    )
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

    def _set_rx_sample_rate(self, Fs, channel=0):
        self.device.set_sample_rate(int(Fs))
        self._rate_changed()
    def _set_rx_freq(self, freq, channel=0):
        self.device.set_freq(int(freq))

    # HackRF gain stages: RF amp, IF (LNA), baseband (VGA), plus the TX amp and TXVGA.
    # Device-specific controls: `set_rx_if_gain` and the rest are made from CAPS (see Device._make_stage_setters),
    # these apply them. A total (`set_gain`, `set_rx_gain`) is split across them by Device.split_gain.
    def _set_rx_rf_gain(self, gain, channel=0):
        gain = bool(gain)
        self.device.set_amp_enable(gain)
    def _set_rx_if_gain(self, gain, channel=0):
        self.device.set_lna_gain(int(gain))
    def _set_rx_bb_gain(self, gain, channel=0):
        self.device.set_vga_gain(int(gain))

    def _set_tx_rf_gain(self, gain, channel=0):
        gain = bool(gain)
        self.device.set_amp_enable(gain)
    def _set_tx_if_gain(self, gain, channel=0):
        self.device.set_txvga_gain(int(gain))

    def _set_bias_t(self, bias):
        self.device.set_antenna_enable(bool(bias))

    # --- generic controls --- #
    def _capabilities(self, caps):
        filters = [Range(bw, bw) for bw in BASEBAND_FILTERS]
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

    def _set_bandwidth(self, direction, bandwidth, channel=0):
        # One baseband filter serves both directions
        self.device.set_baseband_filter_bandwidth(int(bandwidth))
        return bandwidth


    # --- transmit --- #
    # HackRF is half-duplex: one synthesizer and one data path, so it receives or transmits, never both at once.
    def _start_rx(self):
        if self.state["tx"]:
            raise err.Busy(f"{type(self).NAME} is half-duplex: stop_tx() before start_rx()")
        super()._start_rx()

    def _start_tx(self):
        if self.state["rx"]:
            raise err.Busy(f"{type(self).NAME} is half-duplex: stop_rx() before start_tx()")
        super()._start_tx()
