import numpy as np

from ..base.native import NativeDevice
from ..base import convert
from ..base.caps import Capabilities, GainStage, Range, Setting
from .. import err

from . import images, lib

TIME_SOURCES = ("none", "internal", "external", "gpsdo")

class USRP(NativeDevice):
    """What every USRP has in common through UHD: opening one by serial or address, the per-channel controls, clock and
    time sources, sensors. A family is a subclass that says what its radios can do (`CAPS`) and how they tune
    (`B210`, `X310`, `X410`)."""
    DRIVER = "uhd"          # the driver name a unit of this class is listed under (DeviceInfo.driver)
    MODEL = "USRP"
    CAN_TRANSMIT = True
    DUPLEX = True
    SAMPLE_FORMAT = convert.INT16   # UHD delivers sc16 (interleaved signed 16-bit I/Q)
    LOADS_IMAGES = False            # opening loads an FPGA image (B2xx): look for UHD's images first

    SETTINGS = {
        "time_source": Setting(str, "Source of the device's PPS time reference", "_set_time_source",
                               choices=TIME_SOURCES, getter="_get_time_source", default="none"),
    }

    device: lib.uhd.device   # the extension's device (None until `open`)

    def __init__(self):
        super().__init__()
        lib.uhd.init()

    @classmethod
    def find(cls, addresses=None):
        """[DeviceInfo] of this family's units: `pysdrlib.uhd.find`, for this driver"""
        from . import find
        return [info for info in find(addresses) if info.driver == cls.DRIVER]

    def _open(self, serial_number=None, args="", addr=None):
        """serial_number: the unit with this serial. addr: its network address (a radio on a network). args: UHD device
        address string (e.g. "type=b200,master_clock_rate=16e6")"""
        if type(self).LOADS_IMAGES and images.configure() is None:
            self.log.warning(images.HELP)
        parts = [f"serial={serial_number}" if serial_number is not None else "",
                 f"addr={addr}" if addr else "", args]
        args = ",".join(x for x in parts if x)
        try:
            return lib.uhd.open(args)
        except lib.err.NOT_FOUND as exc:
            raise err.NoDevice(f"No {type(self).MODEL} found" + (f" for {args!r}" if args else "") + "!") from exc
        except lib.err.UHDError as exc:
            if "image" in str(exc).lower():
                raise err.NoDevice(f"{exc}. {images.HELP}") from exc
            raise

    def _channels(self, direction):
        return range(self.device.num_rx_channels() if direction == "rx" else self.device.num_tx_channels())

    # --- tuning: what a family without a shared synthesizer or a master clock to manage does --- #
    def _set_rx_sample_rate(self, Fs, channel=0):
        """Every receive channel at `Fs` (a stream's channels run at one rate). Returns the rate set"""
        for ch in self._channels("rx"):
            self.device.set_rx_rate(float(Fs), ch)
        self._Fs = self.device.get_rx_rate(0)
        self._rate_changed()
        return self._Fs
    def _set_tx_sample_rate(self, Fs, channel=0):
        for ch in self._channels("tx"):
            self.device.set_tx_rate(float(Fs), ch)
        return self.device.get_tx_rate(0)
    def _set_rx_freq(self, freq, channel=0):
        self.device.set_rx_freq(float(freq), channel)
    def _set_tx_freq(self, freq, channel=0):
        self.device.set_tx_freq(float(freq), channel)

    def _set_rx_rf_gain(self, gain, channel=0):
        self.device.set_rx_gain(float(gain), channel)
    def _set_tx_rf_gain(self, gain, channel=0):
        self.device.set_tx_gain(float(gain), channel)

    def _set_bias_t(self, bias):
        raise err.NotSupported(f"{type(self).NAME} has no bias tee")

    # --- generic controls --- #
    def _capabilities(self, caps):
        d = self.device
        caps.settings.pop("bias_t", None)
        caps.channels = {"rx": d.num_rx_channels(), "tx": d.num_tx_channels()}
        caps.bandwidths = {"rx": [Range(*d.rx_bandwidth_range(0))], "tx": [Range(*d.tx_bandwidth_range(0))]}
        caps.antennas = {"rx": list(d.rx_antennas(0)), "tx": list(d.tx_antennas(0))}
        caps.clock_sources = list(d.clock_sources())
        caps.info.update({k: v for k, v in d.info().items() if v})
        return caps

    def _set_gain_mode(self, direction, mode, channel=0):
        raise err.NotSupported(f"{type(self).NAME} has no gain modes")
    def _set_bandwidth(self, direction, bandwidth, channel=0):
        if direction == "rx":
            return self.device.set_rx_bandwidth(float(bandwidth), channel)
        return self.device.set_tx_bandwidth(float(bandwidth), channel)
    def _set_antenna(self, direction, name, channel=0):
        if direction == "rx":
            self.device.set_rx_antenna(name, channel)
        else:
            self.device.set_tx_antenna(name, channel)

    # --- which channels stream --- #
    def _set_rx_channels(self, channels):
        self.device.set_rx_channels(list(channels))
    def _set_tx_channels(self, channels):
        self.device.set_tx_channels(list(channels))
    def _set_clock_source(self, name):
        self.device.set_clock_source(name)

    # --- settings --- #
    def _set_time_source(self, name):
        self.device.set_time_source(name)
    def _get_time_source(self):
        return self.device.get_time_source()

    # --- sensors and time --- #
    def sensors(self, channel=0):
        """{name: value} of the motherboard's and a channel's frontend sensors (ref_locked, lo_locked, temp, rssi...),
        keyed "mboard.", "rx." or "tx." + name"""
        self._ensure_open("sensors")
        self._check_channel("rx", channel)
        out = {f"mboard.{k}": v for k, v in self.device.mboard_sensors().items()}
        out.update({f"rx.{k}": v for k, v in self.device.rx_sensors(channel).items()})
        out.update({f"tx.{k}": v for k, v in self.device.tx_sensors(channel).items()})
        return out
    def time(self):
        """The device's time [s]"""
        self._ensure_open("time")
        return self.device.time_now()
    def set_time(self, seconds=0.0, at_pps=False):
        """Set the device's time; at_pps: on the next PPS edge (to align several devices)"""
        self._ensure_open("set_time")
        (self.device.set_time_next_pps if at_pps else self.device.set_time_now)(float(seconds))


class B210(USRP):
    """Ettus USRP B200/B210 (and compatible) through UHD. Two channels each way on a B210 (frontends A and B), one on a
    B200: every control takes `channel`, and `start_rx(channels=(0, 1))` / `start_tx(channels=(0, 1))` stream both
    together (coherent: one clock, one LO per direction).

    The AD9361 has one receive and one transmit synthesizer, so a frequency is shared by a direction's channels, and
    the streams need one rate per direction (caps.shared). Gain, filter and antenna are per channel.

    The gain is the AD9361's single programmable gain (PGA): `set_rx_pga_gain`/`set_tx_pga_gain`, or the generic
    `set_rx_gain_db`/`set_tx_gain_db` (decibels) or `set_rx_gain`/`set_tx_gain` (a fraction of the range).
    """
    NAME = "UHD B210"
    # 61.44 MS/s with one channel, 30.72 MS/s with two (USB 3 carries sc16 at that rate). The gain is the AD9361 PGA,
    # the only gain the B210 exposes through UHD
    CAPS = Capabilities(
        driver="",
        freq=[Range(50_000_000, 6_000_000_000)], default_freq=915_000_000,
        rates=[Range(200_000, 61_440_000)], default_rate=10_000_000,
        gains={"rx": {"PGA": GainStage(1, 0, 76, 40)}, "tx": {"PGA": GainStage(0.25, 0, 89.75, 40)}},
    )
    SAMPLE_RATE_MAX_DUAL = 30_720_000
    MASTER_CLOCK_MIN = 5_000_000
    MASTER_CLOCK_MAX = 61_440_000
    MASTER_CLOCK_MAX_DUAL = 30_720_000   # with two channels in a stream (UHD refuses more)
    MASTER_CLOCK_GOOD_MIN = 25_000_000   # below this the AD9361's converters are outside their best range
    MODEL = "USRP B2xx"
    LOADS_IMAGES = True
    # In-flight USB packets (UHD's default frame sizes) and the AD9361's PLL lock after a retune
    RETUNE_SAMPLES = 2 * 8192
    RETUNE_SETTLE = 0.002

    SETTINGS = {
        **USRP.SETTINGS,
        "dc_offset_auto": Setting(bool, "Automatic DC offset correction of the receiver", "_set_dc_offset_auto", default=True,
                                  per_channel=True),
        "iq_balance_auto": Setting(bool, "Automatic IQ imbalance correction of the receiver", "_set_iq_balance_auto", default=True,
                                   per_channel=True),
        "master_clock_rate": Setting(float, "Master clock rate [Hz] (0: automatic, chosen from the sample rate)",
                                     "_set_master_clock_rate", getter="_get_master_clock_rate", default=0.0),
    }

    def __init__(self):
        super().__init__()
        self._mcr_pinned = False   # the user chose a master clock rate: don't pick one per sample rate

    # --- tuning --- #
    def _master_clock_for(self, Fs, channels=None):
        """The master clock to run at for sample rate `Fs`: the current one if it already gives Fs with an even
        (or no) decimation, else the smallest power-of-two multiple of Fs the AD9361 runs well at (25-61.44 MHz).
        With two channels in a stream (`channels`, default the streams' own) the clock may be at most 30.72 MHz: then
        the largest power-of-two multiple under that, even below 25 MHz.
        UHD's own choice fails when RX and TX rates are set one after the other, and picks odd decimations."""
        if channels is None:
            channels = max(len(self._rx_channels), len(self._tx_channels))
        top = self.MASTER_CLOCK_MAX_DUAL if channels > 1 else self.MASTER_CLOCK_MAX
        current = self.device.get_master_clock_rate()
        ratio = current / Fs
        if current <= top + 1 and ratio >= 1 - 1e-6 and abs(ratio - round(ratio)) < 1e-6 and (round(ratio) == 1 or round(ratio) % 2 == 0):
            return current
        mcr = float(Fs)
        while mcr < self.MASTER_CLOCK_GOOD_MIN and mcr * 2 <= top + 1:
            mcr *= 2
        return mcr if self.MASTER_CLOCK_MIN - 1 <= mcr <= top + 1 else None   # 1 Hz: rates read back are not exact

    def _fit_clock(self, channels):
        """Before streaming `channels` channels: a master clock they allow at the current rates (rates re-applied)"""
        if self._mcr_pinned:
            return
        mcr = self._master_clock_for(self._Fs, channels)
        if mcr is None:
            self._invalid(f"{channels} channels at {self._Fs:,.0f} S/s: no master clock fits "
                          f"(at most {self.MASTER_CLOCK_MAX_DUAL:,} Hz with two channels)")
            return
        if mcr != self.device.get_master_clock_rate():
            self._change_master_clock(mcr)
            for d in ("rx", "tx"):
                rate = self._Fs if d == "rx" else self._tuning.get(("rate", "tx", 0))
                if rate:
                    for ch in self._channels(d):
                        (self.device.set_rx_rate if d == "rx" else self.device.set_tx_rate)(float(rate), ch)
            self._Fs = self.device.get_rx_rate(0) or self._Fs

    def _change_master_clock(self, mcr):
        """Streams cannot run across a master clock change (the radio stops answering): pause them around it"""
        rx, tx = self.device.is_streaming(), self.device.is_transmitting()
        if tx:
            self.device.stop_tx()
        if rx:
            self.device.stop_rx()
        try:
            self.device.set_master_clock_rate(float(mcr))
        finally:
            if rx:
                self.device.start_rx()
            if tx:
                self.device.start_tx()

    def _fit_rate_clock(self, Fs, direction):
        """Before setting `direction`'s rate to `Fs`: a master clock that gives it (unless the user pinned one). The
        clock moved under the other direction, so its rate is put back, if the new clock can still give it (when not,
        e.g. both directions are being set to a new rate, the caller sets it next)"""
        if self._mcr_pinned:
            return
        mcr = self._master_clock_for(Fs)
        if mcr is None or mcr == self.device.get_master_clock_rate():
            return
        self._change_master_clock(mcr)
        other = "tx" if direction == "rx" else "rx"
        rate = self._tuning.get(("rate", other, 0))
        ratio = mcr / rate if rate else 0
        if rate and abs(ratio - round(ratio)) < 1e-6 and (round(ratio) == 1 or round(ratio) % 2 == 0):
            for ch in self._channels(other):
                (self.device.set_rx_rate if other == "rx" else self.device.set_tx_rate)(float(rate), ch)

    def _set_rx_sample_rate(self, Fs, channel=0):
        """Every receive channel at `Fs` (a stream's channels run at one rate). Returns the rate set"""
        self._fit_rate_clock(Fs, "rx")
        for ch in self._channels("rx"):
            self.device.set_rx_rate(float(Fs), ch)
        self._Fs = self.device.get_rx_rate(0)
        if len(self._rx_channels) > 1 and self._Fs > self.SAMPLE_RATE_MAX_DUAL:
            self._invalid(f"{self._Fs:,.0f} S/s on {len(self._rx_channels)} channels is more than USB carries "
                          f"(at most {self.SAMPLE_RATE_MAX_DUAL:,})")
        self._rate_changed()
        return self._Fs
    def _set_tx_sample_rate(self, Fs, channel=0):
        self._fit_rate_clock(Fs, "tx")
        for ch in self._channels("tx"):
            self.device.set_tx_rate(float(Fs), ch)
        return self.device.get_tx_rate(0)

    # Every channel of the direction: they share the synthesizer, but each has its own DSP tuning, and two channels
    # tuned apart by a fraction of a hertz drift in phase (not coherent)
    def _set_rx_freq(self, freq, channel=0):
        for ch in self._channels("rx"):
            self.device.set_rx_freq(float(freq), ch)
    def _set_tx_freq(self, freq, channel=0):
        for ch in self._channels("tx"):
            self.device.set_tx_freq(float(freq), ch)

    # --- gain: one stage per direction --- #

    def _set_rx_pga_gain(self, gain, channel=0):
        self.device.set_rx_gain(float(gain), channel)
    def _set_tx_pga_gain(self, gain, channel=0):
        self.device.set_tx_gain(float(gain), channel)

    # --- generic controls --- #
    def _capabilities(self, caps):
        caps = super()._capabilities(caps)
        # one synthesizer per direction; a stream's channels share its rate; the rest is per channel
        caps.shared = {"freq": "direction", "rate": "direction", "gain": "channel", "bandwidth": "channel", "antenna": "channel"}
        # Frequency and rates stay CAPS': UHD's own ranges include the DSP's reach beyond the frontend, and the
        # rates it lists are only those the current master clock gives
        caps.gain_modes = {"rx": ["manual", "auto"], "tx": ["manual"]}
        return caps

    def _set_gain_mode(self, direction, mode, channel=0):
        if direction == "tx":
            raise err.NotSupported(f"{type(self).NAME} has no TX gain modes")
        self.device.set_rx_agc(mode == "auto", channel)
    # --- which channels stream --- #
    def _set_rx_channels(self, channels):
        if len(channels) > 1 and self._Fs > self.SAMPLE_RATE_MAX_DUAL:
            self._invalid(f"{len(channels)} channels at {self._Fs:,.0f} S/s is more than USB carries "
                          f"(at most {self.SAMPLE_RATE_MAX_DUAL:,} each)")
        self._fit_clock(max(len(channels), len(self._tx_channels)))
        super()._set_rx_channels(channels)
    def _set_tx_channels(self, channels):
        self._fit_clock(max(len(channels), len(self._rx_channels)))
        super()._set_tx_channels(channels)

    # --- settings --- #
    def _set_dc_offset_auto(self, enable, channel=0):
        self.device.set_rx_dc_offset(bool(enable), channel)
    def _set_iq_balance_auto(self, enable, channel=0):
        self.device.set_rx_iq_balance(bool(enable), channel)
    def _set_master_clock_rate(self, rate):
        """0: automatic (chosen from the sample rate). Changing it changes the rates the device can run; the rate is re-applied"""
        self._mcr_pinned = bool(rate)
        if rate:
            self._change_master_clock(rate)
        if self.state["open"]:
            self.set_rx_sample_rate(self._Fs)
            if ('rate', 'tx', 0) in self._tuning:
                self.set_tx_sample_rate(self._tuning[('rate', 'tx', 0)])
    def _get_master_clock_rate(self):
        return self.device.get_master_clock_rate()


class _XSeries(USRP):
    """The radios of Ettus's X series (X310, X410): on a network (or PCIe), with daughterboards or a wideband frontend,
    and one synthesizer per channel. UHD gives one overall gain per channel (`set_rx_rf_gain`: the sum of the frontend's
    stages) and what the frontends fitted allow (frequency and gain ranges), read from the radio when it is opened.

    EXPERIMENTAL: written from UHD's documentation and tested on a fake driver, not on hardware. Rates are rounded by UHD
    to what the master clock gives; read back what was set. Needs a libuhd that has these radios (a system one: the copy
    pysdrlib builds is B2xx only)."""
    CAN_TRANSMIT = True
    _warned = False

    def _open(self, serial_number=None, args="", addr=None):
        """addr: the radio's network address ("192.168.10.2"); serial_number or args as for any USRP"""
        if not _XSeries._warned:
            _XSeries._warned = True
            self.log.warning("%s support is experimental: not yet tested on hardware", type(self).MODEL)
        return super()._open(serial_number, args, addr)

    def _capabilities(self, caps):
        caps = super()._capabilities(caps)
        d = self.device
        # one synthesizer per channel; a stream's channels share its rate; AGC needs a frontend that has it (these don't)
        caps.shared = {"freq": "channel", "rate": "direction", "gain": "channel", "bandwidth": "channel", "antenna": "channel"}
        caps.gain_modes = {"rx": ["manual"], "tx": ["manual"]}
        # what the frontends fitted allow (a different daughterboard is a different range)
        rx_lo, rx_hi = d.rx_freq_range(0)
        tx_lo, tx_hi = d.tx_freq_range(0)
        caps.freq = [Range(max(rx_lo, tx_lo), min(rx_hi, tx_hi))]
        for direction, ranged in (("rx", d.rx_gain_range(0)), ("tx", d.tx_gain_range(0))):
            lo, hi, step = ranged
            default = caps.gains[direction]["RF"].default
            caps.gains[direction] = {"RF": GainStage(step, lo, hi, min(max(default, lo), hi))}
        return caps


class X310(_XSeries):
    """Ettus USRP X300/X310 through UHD, on a 1/10 GbE or PCIe link. Two channels each way with two daughterboards
    (frontends A and B; UBX, SBX, CBX, WBX... set what the frequency, bandwidth and gain are: see `caps` once open).

        sdr = pysdrlib.open("x310", addr="192.168.10.2")          # or pysdrlib.find(addresses=["192.168.10.2"])[0].open()
        sdr.initialize(cf=2.4e9, Fs=100e6)
        sdr.start_rx(channels=(0, 1))

    One overall gain per channel: `set_rx_rf_gain` / `set_tx_rf_gain`, or the generic `set_rx_gain_db` / `set_tx_gain_db`.
    The link sets the rate: 10 GbE carries up to 200 MS/s of sc16, 1 GbE about 25 MS/s. Converting that to complex64
    on the host is what limits Python at high rates."""
    NAME = "UHD X310"
    DRIVER = "x310"
    MODEL = "USRP X300/X310"
    # Placeholders until open() reads the daughterboards': 10 MHz - 6 GHz is a UBX's range
    CAPS = Capabilities(
        driver="",
        freq=[Range(10_000_000, 6_000_000_000)], default_freq=100_000_000,
        rates=[Range(100_000, 200_000_000)], default_rate=10_000_000,
        gains={"rx": {"RF": GainStage(0.5, 0, 31.5, 15)}, "tx": {"RF": GainStage(0.5, 0, 31.5, 15)}},
    )
    # In-flight frames (UHD's 8000-byte default, 2000 sc16 samples) and the LO's lock after a retune. UNMEASURED
    RETUNE_SAMPLES = 2 * 2000
    RETUNE_SETTLE = 0.005


class X410(_XSeries):
    """Ettus USRP X410 through UHD, on a 10/25/100 GbE link. Four channels each way, 1 MHz to 7.2 GHz, up to 400 MS/s
    with the CG_400 FPGA image (200 MS/s with X4_200); it needs UHD 4 and an image that matches it.

        sdr = pysdrlib.open("x410", addr="192.168.10.2")
        sdr.initialize(cf=3.5e9, Fs=200e6)
        sdr.start_rx(channels=(0, 1, 2, 3))

    One overall gain per channel: `set_rx_rf_gain` / `set_tx_rf_gain`, or the generic `set_rx_gain_db` / `set_tx_gain_db`.
    Converting 400 MS/s to complex64 on the host is more than Python keeps up with: expect `warn.Dropped` there."""
    NAME = "UHD X410"
    DRIVER = "x410"
    MODEL = "USRP X410"
    CAPS = Capabilities(
        driver="",
        freq=[Range(1_000_000, 7_200_000_000)], default_freq=100_000_000,
        rates=[Range(100_000, 400_000_000)], default_rate=10_000_000,
        gains={"rx": {"RF": GainStage(1, 0, 60, 30)}, "tx": {"RF": GainStage(1, 0, 60, 30)}},
    )
    RETUNE_SAMPLES = 2 * 2000   # UNMEASURED
    RETUNE_SETTLE = 0.005
