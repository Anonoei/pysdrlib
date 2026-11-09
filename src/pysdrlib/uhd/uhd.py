import numpy as np

from ..base.native import NativeDevice
from ..base import convert
from ..base.caps import Range, Setting
from .. import err

from .config import ConfigB210 as config
from . import images, lib

class B210(NativeDevice):
    """Ettus USRP B200/B210 (and compatible) through UHD. Controls act on channel 0 (RX/TX of frontend A); `device`
    methods take a `channel` for the B210's second frontend.

    The gain is the AD9361's single programmable gain (PGA): `set_rx_pga_gain`/`set_tx_pga_gain`, or the generic
    `set_rx_gain`/`set_tx_gain`.
    """
    NAME = "UHD B210"
    CONFIG = config
    CAN_TRANSMIT = True
    DUPLEX = True
    SAMPLE_FORMAT = convert.INT16   # UHD delivers sc16 (interleaved signed 16-bit I/Q)
    # In-flight USB packets (UHD's default frame sizes) and the AD9361's PLL lock after a retune
    RETUNE_SAMPLES = 2 * 8192
    RETUNE_SETTLE = 0.002

    SETTINGS = {
        "dc_offset_auto": Setting(bool, "Automatic DC offset correction of the receiver", "_set_dc_offset_auto", default=True),
        "iq_balance_auto": Setting(bool, "Automatic IQ imbalance correction of the receiver", "_set_iq_balance_auto", default=True),
        "time_source": Setting(str, "Source of the device's PPS time reference", "_set_time_source",
                               choices=config.TIME_SOURCES, getter="_get_time_source", default="none"),
        "master_clock_rate": Setting(float, "Master clock rate [Hz] (0: automatic, chosen from the sample rate)",
                                     "_set_master_clock_rate", getter="_get_master_clock_rate", default=0.0),
    }

    def __init__(self):
        super().__init__()
        lib.uhd.init() # pyright: ignore[reportAttributeAccessIssue]
        self.device: lib.uhd.device = None # pyright: ignore[reportAttributeAccessIssue]
        self._mcr_pinned = False   # the user chose a master clock rate: don't pick one per sample rate

    def _open(self, serial_number=None, args=""):
        """serial_number: the unit with this serial. args: UHD device address string (e.g. "type=b200,master_clock_rate=16e6")"""
        if images.configure() is None:
            self.log.warning(images.HELP)
        if serial_number is not None:
            args = ",".join(x for x in (f"serial={serial_number}", args) if x)
        try:
            return lib.uhd.open(args) # pyright: ignore[reportAttributeAccessIssue]
        except lib.err.NOT_FOUND as exc:
            raise err.NoDevice("No USRP devices found!") from exc
        except lib.err.UHDError as exc:
            if "image" in str(exc).lower():
                raise err.NoDevice(f"{exc}. {images.HELP}") from exc
            raise

    # --- tuning --- #
    def _master_clock_for(self, Fs):
        """The master clock to run at for sample rate `Fs`: the current one if it already gives Fs with an even
        (or no) decimation, else the smallest power-of-two multiple of Fs the AD9361 runs well at (25-61.44 MHz).
        UHD's own choice fails when RX and TX rates are set one after the other, and picks odd decimations."""
        cfg = type(self).CONFIG
        current = self.device.get_master_clock_rate()
        ratio = current / Fs
        if ratio >= 1 and abs(ratio - round(ratio)) < 1e-6 and (round(ratio) == 1 or round(ratio) % 2 == 0):
            return current
        mcr = float(Fs)
        while mcr < cfg.MASTER_CLOCK_GOOD_MIN:
            mcr *= 2
        return mcr if mcr <= cfg.MASTER_CLOCK_MAX else None

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

    def _set_sample_rate(self, Fs):
        if not self._mcr_pinned:
            mcr = self._master_clock_for(Fs)
            if mcr is not None and mcr != self.device.get_master_clock_rate():
                self._change_master_clock(mcr)
        self.device.set_sample_rate(float(Fs))   # RX and TX: the AD9361 runs both directions from one clock
        self._Fs = self.device.get_rx_rate(0)
        self._rate_changed()
    def _set_freq(self, freq):
        self.device.set_freq(int(freq))   # RX and TX of channel 0

    # --- gain: one stage per direction --- #
    def set_rx_pga_gain(self, gain=None):
        """Receive PGA, 0-76 dB in 1 dB steps"""
        return self._set_gain_stage("RX", "PGA", gain, self._set_rx_pga_gain)
    def set_tx_pga_gain(self, gain=None):
        """Transmit PGA, 0-89.75 dB in 0.25 dB steps"""
        return self._set_gain_stage("TX", "PGA", gain, self._set_tx_pga_gain)

    def _set_rx_gain(self, gain):
        if gain == "default":
            gain = type(self).CONFIG.DEFAULT_GAIN_RX_PGA
        return {"pga": self.set_rx_pga_gain(gain)}
    def _set_tx_gain(self, gain):
        if gain == "default":
            gain = type(self).CONFIG.DEFAULT_GAIN_TX_PGA
        return {"pga": self.set_tx_pga_gain(gain)}
    def _set_rx_pga_gain(self, gain):
        self.device.set_rx_gain(float(gain), 0)
    def _set_tx_pga_gain(self, gain):
        self.device.set_tx_gain(float(gain), 0)

    def _set_bias_t(self, bias):
        raise err.NotSupported(f"{type(self).NAME} has no bias tee")

    # --- generic controls --- #
    def _capabilities(self, caps):
        d = self.device
        caps.settings.pop("bias_t", None)
        # caps.channels stays 1/1: the generic API drives channel 0. The second frontend is reachable through `device`
        # (its methods take a `channel`) until the base Device grows channels
        # Frequency and rates stay CONFIG's: UHD's own ranges include the DSP's reach beyond the frontend, and the
        # rates it lists are only those the current master clock gives
        caps.bandwidths = {"rx": [Range(*d.rx_bandwidth_range(0))], "tx": [Range(*d.tx_bandwidth_range(0))]}
        caps.gain_modes = {"rx": ["manual", "auto"], "tx": ["manual"]}
        caps.antennas = {"rx": list(d.rx_antennas(0)), "tx": list(d.tx_antennas(0))}
        caps.clock_sources = list(d.clock_sources())
        caps.info.update({k: v for k, v in d.info().items() if v})
        return caps

    def _set_gain_mode(self, direction, mode):
        if direction == "tx":
            raise err.NotSupported(f"{type(self).NAME} has no TX gain modes")
        self.device.set_rx_agc(mode == "auto", 0)
    def _set_bandwidth(self, direction, bandwidth):
        if direction == "rx":
            return self.device.set_rx_bandwidth(float(bandwidth), 0)
        return self.device.set_tx_bandwidth(float(bandwidth), 0)
    def _set_antenna(self, direction, name):
        if direction == "rx":
            self.device.set_rx_antenna(name, 0)
        else:
            self.device.set_tx_antenna(name, 0)
    def _set_clock_source(self, name):
        self.device.set_clock_source(name)

    # --- settings --- #
    def _set_dc_offset_auto(self, enable):
        self.device.set_rx_dc_offset(bool(enable), 0)
    def _set_iq_balance_auto(self, enable):
        self.device.set_rx_iq_balance(bool(enable), 0)
    def _set_time_source(self, name):
        self.device.set_time_source(name)
    def _get_time_source(self):
        return self.device.get_time_source()
    def _set_master_clock_rate(self, rate):
        """0: automatic (chosen from the sample rate). Changing it changes the rates the device can run; the rate is re-applied"""
        self._mcr_pinned = bool(rate)
        if rate:
            self._change_master_clock(rate)
        if self.state["open"]:
            self.set_sample_rate(self._Fs)
    def _get_master_clock_rate(self):
        return self.device.get_master_clock_rate()

    # --- transmit --- #
    TX_BUFFER_SECONDS = 0.25   # how much stream `write` may queue ahead of the radio before it blocks

    def _start_tx(self):
        self.device.set_tx_buffer(int(type(self).TX_BUFFER_SECONDS * self._Fs))
        self.device.start_tx()
    def _stop_tx(self):
        self.device.stop_tx()

    def write(self, samples, timeout=None):
        """Queue complex samples (full scale is +-1) for transmission; blocks while the queue is full, up to `timeout`
        seconds (None: until all are queued). Transmitting starts with `start_tx()`. Returns how many were queued"""
        self._ensure_open("write")
        if not self.state["tx"]:
            raise err.NotSupported("write() needs start_tx() first")
        x = np.ascontiguousarray(samples, dtype=np.complex64).ravel()
        limit = -1.0 if timeout is None else float(timeout)
        done = 0
        while done < len(x):
            n = self.device.write_tx(x[done:], limit)
            done += n
            if n == 0 and limit >= 0:
                break
            tx_error = self.device.tx_error()
            if tx_error:
                raise err.StreamStalled(f"{type(self).NAME} stopped transmitting: {tx_error}")
        return done

    @property
    def tx_stats(self):
        """{"written", "sent", "underruns", "pending", "async_errors"} samples counted since start_tx (underruns: samples of
        silence sent because `write` was not keeping up)"""
        return self.device.tx_stats()

    # --- sensors and time --- #
    def sensors(self):
        """{name: value} of the motherboard's and the frontends' sensors (ref_locked, lo_locked, temp, rssi...), keyed "mboard.", "rx." or "tx." + name"""
        self._ensure_open("sensors")
        out = {f"mboard.{k}": v for k, v in self.device.mboard_sensors().items()}
        out.update({f"rx.{k}": v for k, v in self.device.rx_sensors(0).items()})
        out.update({f"tx.{k}": v for k, v in self.device.tx_sensors(0).items()})
        return out
    def time(self):
        """The device's time [s]"""
        self._ensure_open("time")
        return self.device.time_now()
    def set_time(self, seconds=0.0, at_pps=False):
        """Set the device's time; at_pps: on the next PPS edge (to align several devices)"""
        self._ensure_open("set_time")
        (self.device.set_time_next_pps if at_pps else self.device.set_time_now)(float(seconds))
