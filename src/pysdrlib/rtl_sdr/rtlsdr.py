import dataclasses

from ..base.native import NativeDevice
from ..base import convert
from ..base.caps import Capabilities, GainStage, Range
from .. import err

from . import lib

class RTLSDR(NativeDevice):
    NAME = "RTL-SDR"
    # The R820T/R820T2/R828D tuner on nearly every current dongle. The older E4000 (52 - 2200 MHz, with a gap) and
    # FC00xx tuners differ; the driver rejects what they can't tune.
    # The RTL2832U accepts 225,001 - 300,000 and 900,001 - 3,200,000 Hz (`SAMPLE_RATE_GAP` is between them). Over
    # about 2.56 MHz a dongle on USB 2.0 usually drops samples.
    # Tuner gain in dB. It moves in uneven steps (R820T: 0, 0.9, 1.4, 2.7, ... 49.6), which the driver snaps to the
    # nearest of (the real table is read once open).
    CAPS = Capabilities(
        driver="",
        freq=[Range(24_000_000, 1_766_000_000)], default_freq=100_000_000,
        rates=[Range(225_001, 3_200_000)], default_rate=2_400_000,
        gains={"rx": {"RF": GainStage(0, 0, 50, 30)}, "tx": {}},
    )
    SAMPLE_RATE_GAP = (300_000, 900_001)   # rates between these are not accepted (exclusive low, inclusive high)
    CAN_TRANSMIT = False
    DUPLEX = False
    SAMPLE_FORMAT = convert.UINT8 # the RTL2832U delivers interleaved unsigned 8-bit I/Q, offset binary
    # After a retune: up to two of librtlsdr's 256 KiB transfers (131072 samples each) were filled before it,
    # and the tuner's PLL takes a few milliseconds
    RETUNE_SAMPLES = 2 * 131072
    RETUNE_SETTLE = 0.005

    device: lib.rtl_sdr.device   # the extension's device (None until `open`)

    def __init__(self):
        super().__init__()
        self._ppm_hw = 0   # whole ppm the hardware corrects (see set_freq_correction)

    def _open(self, serial_number=None, index=0):
        """Open the dongle with this serial number, or else the `index`th one attached"""
        try:
            if serial_number is None:
                return lib.rtl_sdr.open(index)
            return lib.rtl_sdr.open_by_serial(serial_number)
        except lib.err.NOT_FOUND as exc:
            raise err.NoDevice("No RTL-SDR devices found!") from exc
        except lib.err.OPEN_FAILED as exc:
            raise err.NoDevice(str(exc)) from exc

    def _set_rx_sample_rate(self, Fs, channel=0):
        low, high = self.SAMPLE_RATE_GAP
        if low < Fs < high: # the hardware has no rates between these
            snapped = low if Fs - low < high - Fs else high
            self._invalid(f"Sample rate must be at most {low:,} or at least {high:,}, using {snapped:,}")
            self._Fs = Fs = snapped
        actual = self.device.set_sample_rate(int(Fs))
        if abs(actual - Fs) >= 1:
            self._invalid(f"Sample rate {Fs:,} is not available, using {actual:,}")
            self._Fs = actual
        self._rate_changed()
    def _set_rx_freq(self, freq, channel=0):
        self.device.set_freq(int(freq))

    # The tuner has a single gain control, with discrete steps; the driver snaps to the nearest.
    # `set_rx_gain` is the same control.
    def set_rx_rf_gain(self, gain=None):
        """Tuner gain, 0-49.6 dB. Turns automatic gain off"""
        return self._set_gain_stage("RX", "RF", gain, self._set_rx_rf_gain)

    def _set_rx_gain(self, gain, channel=0):
        if gain == "default":
            gain = self.caps.gains["rx"]["RF"].default
        return {"rf": self.set_rx_rf_gain(gain)}
    def _set_rx_rf_gain(self, gain, channel=0):
        self.device.set_rx_rf_gain(float(gain))

    def set_agc(self, enable):
        """Let the tuner choose its gain, or (False) return to the gain last set"""
        self._ensure_open("set_agc")
        self.device.set_agc(bool(enable))

    def _set_bias_t(self, bias):
        """Bias tee on the antenna port (RTL-SDR Blog V3 and V4 only; no effect on other dongles)"""
        self.device.set_bias_t(bool(bias))

    def set_freq_correction(self, ppm, channel=0):
        """Correct for the crystal's frequency error, in parts per million: whole ppm in the hardware (tuner and
        resampler), the fraction left over in what it is asked for"""
        self._ensure_open("set_freq_correction")
        whole = int(round(ppm))
        self.device.set_freq_correction(whole)
        self._ppm_hw = whole
        super().set_freq_correction(ppm - whole, channel)
        return ppm
    def get_freq_correction(self, channel=0):
        return self._ppm_hw + self._ppm

    # --- generic controls --- #
    def _capabilities(self, caps):
        low, high = self.SAMPLE_RATE_GAP
        lo, hi = caps.rates[0].lo, caps.rates[-1].hi
        caps.rates = [Range(lo, low), Range(high, hi)]
        caps.gain_modes["rx"] = ["manual", "auto"]
        try:
            tuner = self.device.tuner
            if isinstance(tuner, str):
                caps.info["tuner"] = tuner
            steps = tuple(self.device.gains())
            if steps:   # the tuner's own table
                stage = caps.gains["rx"]["RF"]
                caps.gains["rx"]["RF"] = dataclasses.replace(stage, min=min(steps), max=max(steps), values=steps)
        except Exception:
            pass
        try:
            caps.info["manufacturer"], caps.info["product"], caps.info["serial"] = self.device.usb_strings()
        except Exception:
            pass
        return caps

    def _set_gain_mode(self, direction, mode, channel=0):
        self.device.set_agc(mode == "auto")
