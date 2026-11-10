from ..base.native import NativeDevice
from ..base import convert
from ..base.caps import Capabilities, GainStage, Range, Setting
from .. import err

from . import lib

class RX888(NativeDevice):
    NAME = "RX-888"
    # The ADC runs at 64 MHz and the DDC decimates by powers of two.
    # Both gain stages are in dB and move in model- and input-dependent steps (HF: attenuator -31.5..0, VGA about
    # -25..33; VHF: tuner 0..49.6 and -4.7..40.8). These are the outer limits across inputs; the driver snaps to the
    # nearest step of the active one.
    CAPS = Capabilities(
        driver="",
        freq=[Range(10_000, 1_750_000_000)], default_freq=10_000_000,
        rates=[Range(2_000_000, 32_000_000)], default_rate=8_000_000,
        gains={"rx": {"RF": GainStage(0, -32, 50, 0), "IF": GainStage(0, -25, 41, 0)}, "tx": {}},
    )
    CAN_TRANSMIT = False
    DUPLEX = False
    SAMPLE_FORMAT = convert.FLOAT32 # the driver's DDC delivers interleaved float32 I/Q
    RETUNE_SETTLE = 0.01   # the DDC's NCO moves at once; the VHF tuner's PLL and the DDC's filters take a few ms
    SETTINGS = {
        "dither": Setting(bool, "ADC dither (spreads spurs at the cost of a little noise)", "set_dither", default=False),
        "random": Setting(bool, "ADC output randomizer (cuts digital interference)", "set_random", default=False),
        "pga": Setting(bool, "ADC programmable gain amplifier (+3.5 dB)", "set_pga", default=False),
    }

    def __init__(self):
        super().__init__()
        self.device: lib.extio.device = None # pyright: ignore[reportAttributeAccessIssue]

    def _open(self):
        try:
            return lib.extio.open() # pyright: ignore[reportAttributeAccessIssue]
        except lib.err.NOT_FOUND as exc:
            raise err.NoDevice("No RX888 found, or no permission to access it (check your udev rules)") from exc

    def _set_rx_sample_rate(self, Fs, channel=0):
        actual = self.device.set_sample_rate(Fs)
        if actual != Fs:
            self._invalid(f"Sample rate must be one of {self.device.sample_rates()}, using {actual:,}")
            self._Fs = actual
        self._rate_changed()
    def _set_rx_freq(self, freq, channel=0):
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

    def _set_rx_gain(self, gain, channel=0):
        """Total gain: the IF stage takes what it can, the rest goes to the RF stage (or attenuates it)"""
        stages = self.caps.gains["rx"]
        if gain == "default":
            rf, if_ = stages["RF"].default, stages["IF"].default
        else:
            if_ = min(max(gain, stages["IF"].min), stages["IF"].max)
            rf = gain - if_
        return {"rf": self.set_rx_rf_gain(rf), "if": self.set_rx_if_gain(if_)}
    def _set_rx_rf_gain(self, gain, channel=0):
        self.device.set_rx_rf_gain(float(gain))
    def _set_rx_if_gain(self, gain, channel=0):
        self.device.set_rx_if_gain(float(gain))

    def _set_bias_t(self, bias):
        """Bias tee on the active input"""
        self.device.set_bias_t(bool(bias))

    def _capabilities(self, caps):
        try:
            caps.rates = [Range(r, r) for r in self.device.sample_rates()]
        except Exception:
            pass
        return caps

    # ADC controls
    def set_dither(self, enable):
        self.device.set_dither(bool(enable))
    def set_random(self, enable):
        self.device.set_random(bool(enable))
    def set_pga(self, enable):
        self.device.set_pga(bool(enable))
