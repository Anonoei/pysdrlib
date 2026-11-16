from ..base.native import NativeDevice
from ..base import convert
from ..base.caps import Capabilities, GainStage, Range, Setting
from .. import err

from . import lib

CLOCK_SOURCES = ("internal", "external")
GAIN_MODE = lib.bladerf.GAIN_MODE   # the extension's enum (bound here: tests replace `lib.bladerf` with a fake)

class BladeRF(NativeDevice):
    """Nuand bladeRF 2.0 micro (xA4, xA9) through libbladeRF. Two channels each way: every control takes `channel`, and
    `start_rx(channels=(0, 1))` / `start_tx(channels=(0, 1))` stream both together (coherent: one clock, one LO per
    direction). The bladeRF 1.0 (x40, x115) is not supported: opening one raises `err.NoDevice`.

    The AD9361 has one receive and one transmit synthesizer, so a frequency is shared by a direction's channels. Gain, filter
    and bias tee are per channel.

    The gain is one stage per direction, under libbladeRF's names: the receiver's `full` gain (`set_rx_full_gain`) and the
    transmitter's `dsa` (`set_tx_dsa_gain`, the digital step attenuator: 0 dB is full power, so its values are zero or
    negative), or the generic `set_rx_gain_db`/`set_tx_gain_db` (decibels) and `set_rx_gain`/`set_tx_gain` (a fraction of
    the range).
    """
    NAME = "bladeRF"
    # The receive gain range depends on the frequency (libbladeRF clamps to it): 1 to 77 dB below 1.3 GHz, -4 to 71 up to
    # 4 GHz, -10 to 62 above. The transmit gain is the attenuator's: -89 (libbladeRF takes whole dB) to 0 dB
    CAPS = Capabilities(
        driver="",
        freq=[Range(70_000_000, 6_000_000_000)], default_freq=915_000_000,
        rates=[Range(520_834, 61_440_000)], default_rate=10_000_000,
        gains={"rx": {"FULL": GainStage(1, -10, 77, 30)}, "tx": {"DSA": GainStage(1, -89, 0, -60)}},
    )
    CAN_TRANSMIT = True
    DUPLEX = True
    SAMPLE_FORMAT = convert.INT12   # SC16 Q11: interleaved signed 16-bit I/Q, 12 significant bits
    # After a retune: bladerf_set_frequency returns only once the AD9361's PLL is locked, ~11 ms for a hop within a band and
    # ~130 ms across bands (100 MHz <-> 2.4/5 GHz, which recalibrates), and the stream mark is taken when it returns. Measured
    # on a bladeRF 2.0 micro xA4 (FPGA 0.16.0, firmware 2.6.0, USB 3) at 2, 10 and 40 MS/s, retuning at random moments
    # between an FM station and a quiet frequency (100 <-> 125 MHz, 100 MHz <-> 5 GHz): at most 2034 samples from before
    # the retune after the mark (2 MS/s), and none at 10 or 40; the level had settled before the mark every time. So one
    # 16384-sample buffer plus 5 ms is margin, checked with flush=True (see the README)
    RETUNE_SAMPLES = 16384
    RETUNE_SETTLE = 0.005

    SETTINGS = {
        "clock_output": Setting(bool, "Output the reference clock on the clock output", "_set_clock_output",
                                getter="_get_clock_output", default=False),
    }

    device: lib.bladerf.device   # the extension's device (None until `open`)

    def __init__(self):
        super().__init__()
        lib.bladerf.init()

    def _open(self, serial_number=None, args=""):
        """serial_number: the unit with this serial. args: a libbladeRF device string (e.g. "*:instance=1")"""
        if serial_number is not None:
            args = f"*:serial={serial_number}"
        try:
            device = lib.bladerf.open(args)
        except lib.err.NOT_FOUND as exc:
            raise err.NoDevice("No bladeRF devices found!") from exc
        except lib.err.UNSUPPORTED_BOARD as exc:
            raise err.NoDevice(f"{exc}") from exc
        except lib.err.PERMISSION as exc:
            raise err.NoDevice(f"{exc}: install libbladeRF's udev rules (88-nuand-bladerf2.rules) or run as root") from exc
        except (lib.err.UPDATE_FW, lib.err.UPDATE_FPGA) as exc:
            raise err.NoDevice(f"{exc}: update it with `bladeRF-cli --flash-firmware` / `--flash-fpga`") from exc
        # libbladeRF opens the receivers in the AD9361's AGC, which overrides any gain set; pysdrlib's gain mode starts at
        # "manual" (get_rx_gain_mode), so make the hardware match it
        for ch in range(device.num_rx_channels()):
            device.set_rx_gain_mode(GAIN_MODE.MGC, ch)
        return device

    def _channels(self, direction):
        return range(self.device.num_rx_channels() if direction == "rx" else self.device.num_tx_channels())

    # --- tuning --- #
    def _set_rx_sample_rate(self, Fs, channel=0):
        self._Fs = self.device.set_rx_rate(float(Fs), channel)
        self._rate_changed()
        return self._Fs
    def _set_tx_sample_rate(self, Fs, channel=0):
        return self.device.set_tx_rate(float(Fs), channel)

    def _set_rx_freq(self, freq, channel=0):
        self.device.set_rx_freq(int(freq), channel)
    def _set_tx_freq(self, freq, channel=0):
        self.device.set_tx_freq(int(freq), channel)

    # --- gain: one stage per direction --- #
    def _set_rx_full_gain(self, gain, channel=0):
        self.device.set_rx_gain_stage("full", int(round(gain)), channel)
    def _set_tx_dsa_gain(self, gain, channel=0):
        self.device.set_tx_gain_stage("dsa", int(round(gain)), channel)

    def _set_bias_t(self, bias):
        for ch in self._channels("rx"):
            self.device.set_rx_bias_tee(bool(bias), ch)

    # --- generic controls --- #
    def _capabilities(self, caps):
        d = self.device
        caps.channels = {"rx": d.num_rx_channels(), "tx": d.num_tx_channels()}
        # one synthesizer per direction; a stream's channels share its rate; the rest is per channel
        caps.shared = {"freq": "direction", "rate": "direction", "gain": "channel", "bandwidth": "channel", "antenna": "channel"}
        caps.bandwidths = {"rx": [Range(*d.rx_bandwidth_range(0)[:2])], "tx": [Range(*d.tx_bandwidth_range(0)[:2])]}
        caps.gain_modes = {"rx": ["manual", "auto"], "tx": ["manual"]}
        caps.clock_sources = list(CLOCK_SOURCES)
        caps.info.update({k: v for k, v in d.info().items() if v})
        return caps

    def _set_gain_mode(self, direction, mode, channel=0):
        if direction == "tx":
            raise err.NotSupported(f"{type(self).NAME} has no TX gain modes")
        # "auto" is libbladeRF's default mode: the AD9361's slow-attack AGC
        self.device.set_rx_gain_mode(GAIN_MODE.DEFAULT if mode == "auto" else GAIN_MODE.MGC, channel)
    def _set_bandwidth(self, direction, bandwidth, channel=0):
        if direction == "rx":
            return self.device.set_rx_bandwidth(float(bandwidth), channel)
        return self.device.set_tx_bandwidth(float(bandwidth), channel)
    def _set_clock_source(self, name):
        self.device.set_clock_select(name)

    # --- which channels stream --- #
    def _set_rx_channels(self, channels):
        self.device.set_rx_channels(list(channels))
    def _set_tx_channels(self, channels):
        self.device.set_tx_channels(list(channels))

    # --- settings --- #
    def _set_clock_output(self, enable):
        self.device.set_clock_output(bool(enable))
    def _get_clock_output(self):
        return self.device.get_clock_output()

    # --- sensors --- #
    def sensors(self):
        """{name: value} of the board's sensors: `rfic_temperature` [deg C] and `pll_locked`"""
        self._ensure_open("sensors")
        out = {}
        for key, value in self.device.sensors().items():
            out[key] = float(value) if key == "rfic_temperature" else value == "true"
        return out
