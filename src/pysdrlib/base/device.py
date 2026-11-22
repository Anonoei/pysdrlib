"""The device-agnostic interface every SDR presents

Lifecycle and tuning (`open`, `initialize`, `set_rx_freq`, `set_rx_sample_rate`, `start_rx`...), reading (`read`, `stream`,
`get_samples`), and the controls a device may have, discovered through `caps` (see `pysdrlib.base.caps`):

    sdr = pysdrlib.device("rtl_sdr"); sdr.open()
    sdr.caps.gains["rx"]                     # the device's own gain stages
    sdr.set_rx_gain(0.5)                     # gain as a fraction of this device's range: the same call on every radio
    sdr.set_rx_gain_db(30)                   # ... or in dB: a total (split by the device) or one stage, set_rx_gain_db(12, stage="IF")
    sdr.set_rx_bandwidth(1.5e6)              # analog filter, when the device has one
    sdr.set_freq_correction(14.8)            # ppm error of the device's reference
    sdr.set_setting("dither", True)          # anything else the device has (caps.settings)
    with sdr.stream(65536, meta=True) as s:
        for x, m in s: ...                   # m: RxMeta (sample index, gaps, retunes inside the block)

Every control takes `channel=0`, and tuning takes `direction` ("rx", "tx", or None for both). `caps.channels` has the
channel counts and `caps.shared` what one setting changes (a frequency may be one channel's, shared by a direction's
channels, or the whole device's). A multi-channel device streams several channels together:

    sdr.start_rx(channels=(0, 1)); x = sdr.read(65536, channel=(0, 1))     # (2, 65536), the same instants
    sdr.start_tx(channels=(0, 1)); sdr.write(np.stack((a, b)))              # sent sample for sample together

A device that lacks a control raises `err.NotSupported`. Values out of range are clamped with a warning, or raise
`err.InvalidValue` with `strict = True`.
"""
import atexit
import dataclasses
import functools
import itertools
import time
import weakref
from typing import Any

import numpy as np

from .. import logger
from .. import warn
from .. import err
from . import caps as _caps
from .stream import Event, RxMeta, RxStream, sample_time

_OPEN = weakref.WeakSet()   # the devices that are open (see `_close_open_devices`)

def _close_open_devices():
    """At exit: stop and close the devices still open. A script that dies mid-stream (an exception, Ctrl+C) otherwise
    leaves a radio streaming, and some (a USRP B210) then refuse to start again until they are reset"""
    for dev in list(_OPEN):
        try:
            if dev.state["rx"]:
                dev.stop_rx()
            if dev.state["tx"]:
                dev.stop_tx()
            if dev.state["open"]:
                dev.close()
        except Exception:
            pass

atexit.register(_close_open_devices)

READ_SLICE = 0.1   # seconds a read waits at a time, between checks on the stream (stalls, stop_rx)
TX_CHUNK = 16384   # samples a multi-channel write queues per channel at a time (so no channel's queue starves another)

def _stage_setter(cls, direction, stage, spec, name):
    hook = f"_set_{direction.lower()}_{stage.lower()}_gain"

    def setter(self, gain=None, channel=0):
        apply = getattr(self, hook, None)
        if apply is None:
            raise NotImplementedError(f"{type(self).__name__} declares a {direction} {stage} gain stage but has no {hook}")
        self._ensure_open(name)
        self._check_channel(direction.lower(), channel)
        return self._set_gain_stage(direction, stage, gain, apply, channel)

    steps = f" in {spec.step} dB steps" if spec.step else ""
    default = f" (default {spec.default})" if spec.default is not None else ""
    setter.__name__ = name
    setter.__qualname__ = f"{cls.__qualname__}.{name}"
    setter.__doc__ = f"{direction} {stage} gain [dB], {spec.min}-{spec.max}{steps}{default}. None: the default. `channel`: which channel's. Returns the gain set"
    setter._generated = True   # type: ignore[attr-defined]
    return setter

class Device:
    """The interface every driver implements. Use `pysdrlib.open` or `pysdrlib.device` to get one; see the module
    docs (`pysdrlib.base.device`) for an overview.

    Class attributes a driver sets: `NAME`, `CAPS` (its limits and gain stages, `pysdrlib.base.caps`), `CAN_TRANSMIT`, `DUPLEX`
    (receive and transmit at once) and `SAMPLE_FORMAT` (the layout of the samples the hardware delivers).
    """
    NAME = "pysdrlib Device"
    CAPS: _caps.Capabilities = None # type: ignore
    CAN_TRANSMIT = False
    DUPLEX = False
    SAMPLE_FORMAT = None # convert.SampleFormat of the raw stream the driver delivers (None: driver converts itself)
    RETUNE_SETTLE = 0.0  # seconds after a retune the hardware may still be settling
    RETUNE_SAMPLES = 0   # samples from before a retune that may still arrive after it (in-flight transfers)
    SETTINGS: dict = {}  # {name: caps.Setting}: the device's own controls, see `set_setting`
    GAIN_RESERVE: dict = {}  # {"rx"/"tx": (stage, ...)}: stages a total gain uses only when the others can't reach it
    GAIN_BALANCE = "range"   # how a total is shared: "range" (each stage the same fraction of its range) or "db" (equal dB)

    # Set by a remote device (pysdrlib.remote.RemoteDevice); these defaults let a caller read them from any device
    id: str | None = None        # the device's id on its server ("<driver>-0"); None for a local device
    granted: dict | None = None  # what the radio a server chose can do, nearest to what was asked; None when not chosen

    @property
    def is_controller(self) -> bool:
        """Whether this handle may change settings: always for a local device; a remote one may be a viewer"""
        return True

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls._make_stage_setters()

    @classmethod
    def _make_stage_setters(cls):
        """Give the class a `set_<rx|tx>_<stage>_gain(gain=None)` for each gain stage its CAPS declares (TX only if it
        can transmit), unless it defines one itself (to say more than the config can). Each applies the stage through the
        driver's `_set_<rx|tx>_<stage>_gain(gain, channel=0)` hook, so a driver writes the hardware call and nothing else"""
        if cls.CAPS is None:
            return
        for direction in ("RX", "TX"):
            if direction == "TX" and not cls.CAN_TRANSMIT:
                continue
            for stage, spec in cls.CAPS.gains[direction.lower()].items():
                name = f"set_{direction.lower()}_{stage.lower()}_gain"
                if name in dir(cls) and not getattr(getattr(cls, name), "_generated", False):
                    continue
                setattr(cls, name, _stage_setter(cls, direction, stage, spec, name))

    def __init__(self):
        self.log = logger.new(f"device.{type(self).__name__}")
        self.device: Any = None   # the driver's handle (an extension's device), None until `open`
        self.state = {
            "open": False,
            "rx": False,
            "tx": False
        }
        self._cf: float = type(self).CAPS.default_freq # type: ignore
        self._Fs: float = type(self).CAPS.default_rate # type: ignore
        self._init_generic()

    def _init_generic(self):
        """State of the generic controls (also for subclasses that don't call Device.__init__)"""
        self.strict = False         # out-of-range values raise err.InvalidValue instead of being clamped
        self.dc_correction = False  # remove the DC offset (a slow running mean) from what `read` returns
        self._ppm = 0.0
        self._gain_values = {}      # (DIRECTION, channel) -> {stage: gain}
        self._gain_mode = {}        # (direction, channel) -> mode
        self._bandwidth = {}        # (direction, channel) -> Hz
        self._antenna = {}          # (direction, channel) -> name
        self._tuning = {}           # (kind "freq"/"rate", direction, channel) -> value set (nominal)
        self._clock_source = None
        self._settings = {}         # (name, channel) -> value
        self._caps = None
        self._caps_open = False
        self._rx_channels = (0,)    # what the receive stream carries (slot k: the k-th of them)
        self._tx_channels = (0,)
        self._rx_reset()

    def __str__(self):
        return f"{type(self).__name__}: {self.state}"

    @classmethod
    def find(cls, **kwargs) -> list:
        """[DeviceInfo] of the units of this driver that are attached, for `pysdrlib.find`. A driver that can list them
        overrides it (it must not open a radio, and takes `addresses` and/or `timeout` if it can look at a network:
        see `pysdrlib.find`); by default there are none to list, and `pysdrlib.open(name, ...)` is how it is opened"""
        return []

    def open(self, *args, **kwargs):
        """Connect to device. serial=...: the unit with this serial number (where the driver can choose)"""
        if self.state["open"]:
            self.log.warning("Device is already open!")
            return
        if "serial" in kwargs:
            kwargs["serial_number"] = kwargs.pop("serial")
        self.log.trace("open(args:%s, kwargs:%s)", args, kwargs)
        self.device = self._open(*args, **kwargs)
        self.state["open"] = True
        self._caps = None
        _OPEN.add(self)
    def initialize(self, cf=None, Fs=None, rx_gain=None, tx_gain=None, rx_gain_db=None, tx_gain_db=None):
        """Tune the receiver (and the transmitter, where it has its own synthesizer) to `cf` and `Fs`, and set the gains.
        `rx_gain` and `tx_gain` are fractions of the device's range, as `set_rx_gain` takes; `rx_gain_db` and `tx_gain_db`
        are decibels, as `set_rx_gain_db` takes. Each is a number for the total, None (the defaults), or a dict of
        stages, `{"IF": 16, "BB": 20}` (see `rx_gain_stages()`; a stage not named keeps the device's). Not both for one direction"""
        self.log.trace("initialize(cf=%s, Fs=%s, rx_gain=%s, tx_gain=%s, rx_gain_db=%s, tx_gain_db=%s)",
                       cf, Fs, rx_gain, tx_gain, rx_gain_db, tx_gain_db)
        self._ensure_open("initialize")
        cf = self.set_rx_freq(cf)
        Fs = self.set_rx_sample_rate(Fs)
        can_tx = type(self).CAN_TRANSMIT and bool(self.caps.channels.get("tx"))   # (this unit, as opened, may not)
        if can_tx and self.caps.shared.get("freq") != "device":   # a transmitter of its own
            self.set_tx_freq(cf)
        if can_tx and self.caps.shared.get("rate") != "device":
            self.set_tx_sample_rate(Fs)
        rx_gain = self._initial_gain("rx", rx_gain, rx_gain_db)
        tx_gain = self._initial_gain("tx", tx_gain, tx_gain_db) if can_tx else None
        self._initialize(cf, Fs, rx_gain, tx_gain)   # (the hook gets the total gains in dB)

    def _initial_gain(self, d, fraction, db):
        """Set a direction's gain from initialize's arguments; returns the total in dB"""
        if fraction is not None and db is not None:
            raise ValueError(f"give {d}_gain (a fraction) or {d}_gain_db, not both")
        if isinstance(db, dict):
            for stage, value in db.items():
                self._set_gain_db(d, value, stage, 0, False)
        elif isinstance(fraction, dict):
            for stage, value in fraction.items():
                self._set_gain(value, d, stage, 0, False)
        elif fraction is not None:
            self._set_gain(fraction, d, None, 0, False)
        else:
            self._set_gain_db(d, db, None, 0, False)
        return self._get_gain_db(d, None, 0)

    def close(self):
        """Disconnect from device"""
        self.log.trace("close()")
        self._ensure_open("close")
        self._close()
        self.state["open"] = False
        self._caps = None
        _OPEN.discard(self)

    def __enter__(self):
        if not self.state["open"]:   # `with pysdrlib.open(...) as sdr` is open already
            self.open()
        return self
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def start_rx(self, channels=None):
        """Start receiving. channels: which to stream together (default: the last ones, at first channel 0);
        `read(..., channel=...)` reads them"""
        if self.state["rx"]:
            self.log.warning("Device is already receiving!")
            return
        self.log.trace("start_rx(%s)", channels)
        self._ensure_open("start_rx")
        if channels is not None:
            chs = self._channel_list("rx", channels)
            if chs != (0,) or self._rx_channels != (0,):
                self._set_rx_channels(chs)
            self._rx_channels = chs
        self._rx_reset()
        for ch in self._rx_channels:   # what the stream starts at: a block from before a later change keeps saying so
            d = self._direction("rx", ch)
            self._rx_now[("freq", ch)] = self._tuning.get(("freq", d, ch), self._cf)
            self._rx_now[("rate", ch)] = self._tuning.get(("rate", d, ch), self._Fs)
        self._start_rx()
        self.state["rx"] = True
    @property
    def rx_channels(self):
        """The channels the receive stream carries"""
        return self._rx_channels
    @property
    def tx_channels(self):
        """The channels the transmit stream carries"""
        return self._tx_channels
    def stop_rx(self):
        """Stop receiving"""
        if not self.state["rx"]:
            self.log.warning("Device isn't receiving!")
            return
        self.log.trace("stop_rx()")
        self._ensure_open("stop_rx")
        self._stop_rx()
        self.state["rx"] = False
    def get_samples(self) -> np.ndarray:
        """The low-level call: whatever has arrived since the last call (any number, possibly none, without waiting). Most
        code wants `read` (an exact count, waiting for it, with the stream's metadata) or `stream` (blocks until you stop)"""
        # self.log.trace("get_samples()")
        self._ensure_open("get_samples")
        return self._get_samples()

    def start_tx(self, channels=None):
        """Start transmitting (then `write`). channels: which to transmit together (default: the last ones, at first 0)"""
        if self.state["tx"]:
            self.log.warning("Device is already transmitting!")
            return
        self.log.trace("start_tx(%s)", channels)
        self._ensure_open("start_tx")
        if not self.caps.channels.get("tx"):
            raise err.NotSupported(f"{type(self).NAME} can't transmit")
        if channels is not None:
            chs = self._channel_list("tx", channels)
            if chs != (0,) or self._tx_channels != (0,):
                self._set_tx_channels(chs)
            self._tx_channels = chs
        self._start_tx()
        self.state["tx"] = True
    def stop_tx(self):
        """Stop transmitting"""
        if not self.state["tx"]:
            self.log.warning("Device isn't transmitting!")
            return
        self.log.trace("stop_tx()")
        self._ensure_open("stop_tx")
        self._stop_tx()
        self.state["tx"] = False

    # ------------------------------------------------------------------ tuning
    def set_rx_sample_rate(self, Fs=None, channel=0, flush=False):
        """Set the receive sample rate (the nominal one: a frequency correction is applied to what the hardware is asked
        for). What else changes with it: `caps.shared["rate"]` (a device with one clock for both directions sets both).
        flush: while receiving, discard the samples from before the change. Returns the rate in effect"""
        return self._tune_rate(Fs, "rx", channel, flush)
    def set_tx_sample_rate(self, Fs=None, channel=0):
        """Set the transmit sample rate (see `set_rx_sample_rate`). Raises `err.NotSupported` on a receiver"""
        return self._tune_rate(Fs, "tx", channel, False)
    def get_rx_sample_rate(self, channel=0):
        """Receive sample rate [Hz] last set for this channel"""
        return self._tuning.get(("rate", self._direction("rx", channel), channel), self._Fs)
    def get_tx_sample_rate(self, channel=0):
        """Transmit sample rate [Hz] last set for this channel (None: never set)"""
        return self._tuning.get(("rate", self._direction("tx", channel), channel))

    def set_rx_freq(self, freq=None, channel=0, flush=False):
        """Set the receive center frequency (corrected by `set_freq_correction`). What else moves with it:
        `caps.shared["freq"]` (channels sharing a synthesizer, or both directions on a device with one).
        flush: while receiving, discard what was received before the change and the device's settling time
        after it (`caps.retune_settle`), so the next `read` is all at the new frequency. Returns the frequency"""
        return self._tune_freq(freq, "rx", channel, flush)
    def set_tx_freq(self, freq=None, channel=0):
        """Set the transmit center frequency (see `set_rx_freq`). Raises `err.NotSupported` on a receiver"""
        return self._tune_freq(freq, "tx", channel, False)
    def get_rx_inverted(self, channel=0):
        """Whether the receive spectrum comes mirrored, from a converter whose LO is above the signal (see `caps.Chain`):
        flip it (`samples[::-1]` of the spectrum, or conjugate the samples) to read it the right way round"""
        chain = self._chain("rx", channel)
        return bool(chain and chain.invert)
    def get_rx_freq(self, channel=0):
        """Receive center frequency [Hz] last set for this channel (over the air, whatever converter is on the radio)"""
        return self._tuning.get(("freq", self._direction("rx", channel), channel), self._cf)
    def get_tx_freq(self, channel=0):
        """Transmit center frequency [Hz] last set for this channel (None: never set)"""
        return self._tuning.get(("freq", self._direction("tx", channel), channel))

    def _tune_rate(self, Fs, direction, channel, flush):
        """The work of `set_*_sample_rate`; `direction` None: both (what a correction re-applies)"""
        self.log.trace("set_sample_rate(%s, %s)", Fs, direction)
        self._ensure_open(f"set_{direction or 'rx'}_sample_rate")
        self._need_tx(direction)
        dirs = self._tuned_directions(direction, channel, "rate")
        if Fs is None:
            Fs = self.caps.default_rate
        Fs = self._within(Fs, self.caps.rates, "Sample rate")
        scale = self._ppm_scale()
        hw = Fs / scale if self._ppm else Fs
        if hw != Fs and not _caps.contains(self.caps.rates, hw):
            hw, scale = Fs, 1.0    # discrete rates: the hardware can't be asked for a corrected one
        rx_before = self._Fs
        at = self._rx_mark()
        self._Fs = hw
        done = [(self._set_rx_sample_rate if d == "rx" else self._set_tx_sample_rate)(hw, channel=channel)   # may snap self._Fs
                for d in self._hooked(direction, dirs, "rate")]
        actual = next((a for a in done if a is not None), None)
        if actual is None:   # the driver says nothing: what it snapped self._Fs to (receive), else what was asked
            actual = self._Fs if "rx" in dirs else hw
        rate = Fs if actual == hw else actual * scale
        self._Fs = rx_before if "rx" not in dirs else rate
        self._tuned("rate", rate, dirs, channel, flush, at)
        return rate
    def _tune_freq(self, freq, direction, channel, flush):
        """The work of `set_*_freq`; `direction` None: both (what a correction re-applies)"""
        self.log.trace("set_freq(%s, %s)", freq, direction)
        self._ensure_open(f"set_{direction or 'rx'}_freq")
        self._need_tx(direction)
        dirs = self._tuned_directions(direction, channel, "freq")
        chain = self._chain(direction or "rx", channel)    # hardware between the radio and the air (None: none)
        if freq is None:
            freq = self.caps.default_freq if chain is None else chain.to_rf(self.caps.default_freq)
        radio = freq if chain is None else chain.to_if(freq)    # what the radio itself tunes
        held = self._within(radio, self.caps.freq, "Frequency")
        if held != radio:
            radio, freq = held, held if chain is None else chain.to_rf(held)
        at = self._rx_mark()
        for d in self._hooked(direction, dirs, "freq"):
            link = chain if d == (direction or "rx") else self._chain(d, channel)
            hw = radio if link is chain else (freq if link is None else link.to_if(freq))
            hw = hw / self._ppm_scale() if self._ppm else hw
            (self._set_rx_freq if d == "rx" else self._set_tx_freq)(hw, channel=channel)
        self._tuned("freq", freq, dirs, channel, flush, at)
        return freq

    def _chain(self, direction, channel=0):
        """The chain (`caps.chains`) the signal of this direction goes through: the one of the antenna port selected, else the
        one for any port, else the first. None: nothing between the radio and the air"""
        return self._chain_for_port(direction, self._antenna.get((direction, channel)))

    def _chain_for_port(self, direction, port):
        chains = [c for c in self.caps.chains if c.direction == direction]
        if not chains:
            return None
        return next((c for c in chains if port is not None and c.port == port), None) or \
            next((c for c in chains if c.port is None), chains[0])

    def _need_tx(self, direction):
        if direction == "tx" and not self.caps.channels.get("tx"):
            raise err.NotSupported(f"{type(self).NAME} can't transmit")

    def set_freq_correction(self, ppm, channel=0):
        """Error of the device's frequency reference in parts per million (positive: it runs fast). Tuning and
        sample rate are both corrected, so `set_rx_freq(f)` gives f and `set_rx_sample_rate(r)` gives r (the rate only
        where the device has continuous rates). Re-applies them."""
        self._ensure_open("set_freq_correction")
        self._check_channel("rx", channel)
        self._ppm = float(ppm)
        for kind, setter in (("freq", self._tune_freq), ("rate", self._tune_rate)):
            shared = self.caps.shared.get(kind, "channel")
            both = self._tuning.get((kind, "rx", 0)) == self._tuning.get((kind, "tx", 0))
            applied = False
            for (k, d, ch), value in sorted(self._tuning.items(), key=lambda kv: (kv[0][1], kv[0][2])):
                if k != kind or (shared != "channel" and ch) or ((shared == "device" or both) and d != "rx" and not ch):
                    continue   # one call per synthesizer / clock
                setter(value, None if shared == "device" or (both and not ch) else d, ch, False)
                applied = True
            if not applied:
                setter(self._cf if kind == "freq" else self._Fs, None, channel, False)
        return self._ppm
    def get_freq_correction(self, channel=0):
        """Frequency reference error [ppm] last set with `set_freq_correction`"""
        return self._ppm

    # ------------------------------------------------------------------ gain
    # A gain is a fraction of the device's range (set_rx_gain: the call for code that should run on any radio) or in
    # decibels (set_rx_gain_db). Both take a `stage` (see `rx_gain_stages()`) to set one stage instead of the total, which the device
    # splits across its stages. Receive and transmit are separate calls, as for frequency.
    def set_rx_gain(self, fraction=None, stage=None, channel=0, flush=False):
        """The receive gain, as a fraction of the device's range: 0.0 its least, 1.0 its most (linear in dB), of the total
        or of one `stage`. None: the defaults. The same fraction is the same place in the range on every device, so this is
        the call for code that should run on more than one kind of radio: 40 dB is most of one radio's gain range, and a
        third of another's. It takes a fraction, not decibels (for those, `set_rx_gain_db`): a value outside 0-1 raises
        `err.InvalidValue` rather than being clamped, because it is nearly always decibels given to the wrong call, and
        clamping would set the most gain. flush: while receiving, discard what was received before the change and its
        settling time (see `set_rx_freq`). Returns the fraction in effect"""
        return self._set_gain(fraction, "rx", stage, channel, flush)
    def set_tx_gain(self, fraction=None, stage=None, channel=0):
        """The transmit gain, as a fraction of the device's range (see `set_rx_gain`). Returns the fraction in effect"""
        return self._set_gain(fraction, "tx", stage, channel, False)
    def get_rx_gain(self, stage=None, channel=0):
        """The receive gain last set, as a fraction of the device's range (None before a gain was set); `get_rx_gain_db`
        has decibels"""
        return self._get_gain("rx", stage, channel)
    def get_tx_gain(self, stage=None, channel=0):
        """The transmit gain last set, as a fraction of the device's range (None before a gain was set)"""
        return self._get_gain("tx", stage, channel)

    def set_rx_gain_db(self, gain=None, stage=None, channel=0, flush=False):
        """The receive gain in decibels: a total (the device splits it across its stages) or one `stage`'s. None: the
        defaults. Decibels mean different things on different radios; for code that should run on any of them use
        `set_rx_gain`. Returns the gain in effect (the total, or the stage's)"""
        return self._set_gain_db("rx", gain, stage, channel, flush)
    def set_tx_gain_db(self, gain=None, stage=None, channel=0):
        """The transmit gain in decibels (see `set_rx_gain_db`). Returns the gain in effect"""
        return self._set_gain_db("tx", gain, stage, channel, False)
    def get_rx_gain_db(self, stage=None, channel=0):
        """The receive gain last set in decibels: a stage's, or the total of the stages (None before any was set)"""
        return self._get_gain_db("rx", stage, channel)
    def get_tx_gain_db(self, stage=None, channel=0):
        """The transmit gain last set in decibels: a stage's, or the total of the stages (None before any was set)"""
        return self._get_gain_db("tx", stage, channel)
    def get_rx_gains_db(self, channel=0):
        """{stage name: gain [dB]} of what each receive stage was last set to (stages never set are missing)"""
        return self._get_gains_db("rx", channel)
    def get_tx_gains_db(self, channel=0):
        """{stage name: gain [dB]} of what each transmit stage was last set to"""
        return self._get_gains_db("tx", channel)

    def rx_gain_stages(self, channel=0):
        """{stage name: GainStage} of the receiver, as the device names its stages (`stage=` of the gain calls)"""
        return dict(self.caps.gains[self._direction("rx", channel)])
    def tx_gain_stages(self, channel=0):
        """{stage name: GainStage} of the transmitter"""
        return dict(self.caps.gains[self._direction("tx", channel)])

    def _set_gain(self, fraction, d, stage, channel, flush):
        self._ensure_open(f"set_{d}_gain")
        gain = None
        if fraction is not None:
            lo, hi = self._gain_span(stage, self._direction(d, channel))
            if not 0.0 <= fraction <= 1.0:
                raise err.InvalidValue(f"set_{d}_gain takes a fraction of the gain range, 0 to 1, not {fraction}: "
                                       f"for decibels use set_{d}_gain_db({fraction})")
            gain = lo + fraction * (hi - lo)
        self._set_gain_db(d, gain, stage, channel, flush)
        return self._get_gain(d, stage, channel)

    def _set_gain_db(self, d, gain, stage, channel, flush):
        self.log.trace("set_%s_gain_db(%s, stage=%s)", d, gain, stage)
        self._ensure_open(f"set_{d}_gain_db")
        self._direction(d, channel)
        at = self._rx_mark() if d == "rx" else None
        if stage is None:
            total = "default" if gain is None else self._check_gain(gain, d.upper())
            (self._set_rx_gain if d == "rx" else self._set_tx_gain)(total, channel=channel)
        else:
            name = str(stage).upper()
            setter = getattr(self, f"_set_{d}_{name.lower()}_gain", None)
            if name not in self.caps.gains[d] or setter is None:
                raise err.NotSupported(f"{type(self).NAME} has no {d.upper()} gain stage {stage!r} (it has {list(self.caps.gains[d])})")
            self._set_gain_stage(d.upper(), name, gain, setter, channel)
        value = self._get_gain_db(d, stage, channel)
        if d == "rx":
            self._note("gain", value, channel, flush, at=at)
        return value

    def _get_gain_db(self, d, stage, channel):
        values = self._gain_values.get((self._direction(d, channel).upper(), channel), {})
        if stage is not None:
            return values.get(str(stage).upper())
        return sum(values.values()) if values else None

    def _get_gains_db(self, d, channel):
        return dict(self._gain_values.get((self._direction(d, channel).upper(), channel), {}))

    def _get_gain(self, d, stage, channel):
        gain = self._get_gain_db(d, stage, channel)
        if gain is None:
            return None
        lo, hi = self._gain_span(stage, d)
        return 0.0 if hi == lo else (gain - lo) / (hi - lo)

    def _gain_span(self, stage, d):
        if stage is None:
            r = self.caps.gain_range(d)
            return r.lo, r.hi
        spec = self.caps.gains[d].get(str(stage).upper())
        if spec is None:
            raise err.NotSupported(f"{type(self).NAME} has no {d.upper()} gain stage {stage!r} (it has {list(self.caps.gains[d])})")
        return spec.min, spec.max

    def rx_gain_modes(self, channel=0):
        """["manual"] and, when the receiver can set its own gain, "auto\""""
        return list(self.caps.gain_modes[self._direction("rx", channel)])
    def tx_gain_modes(self, channel=0):
        """The transmitter's gain modes (["manual"])"""
        return list(self.caps.gain_modes[self._direction("tx", channel)])
    def set_rx_gain_mode(self, mode, channel=0):
        """"auto": the receiver sets its own gain (AGC), "manual": the gain last set. Raises `err.NotSupported` for a mode
        the device lacks"""
        return self._set_gain_mode_of("rx", mode, channel)
    def set_tx_gain_mode(self, mode, channel=0):
        """The transmitter's gain mode (see `set_rx_gain_mode`)"""
        return self._set_gain_mode_of("tx", mode, channel)
    def get_rx_gain_mode(self, channel=0):
        """Current receive gain mode, "manual" unless `set_rx_gain_mode` changed it"""
        return self._gain_mode.get((self._direction("rx", channel), channel), "manual")
    def get_tx_gain_mode(self, channel=0):
        """Current transmit gain mode"""
        return self._gain_mode.get((self._direction("tx", channel), channel), "manual")

    def _set_gain_mode_of(self, d, mode, channel):
        self._direction(d, channel)
        self._ensure_open(f"set_{d}_gain_mode")
        if mode not in self.caps.gain_modes[d]:
            raise err.NotSupported(f"{type(self).NAME} has no {d.upper()} gain mode {mode!r} (it has {self.caps.gain_modes[d]})")
        self._set_gain_mode(d, mode, channel=channel)
        self._gain_mode[(d, channel)] = mode
        return mode

    # ------------------------------------------------------------------ filter, antenna, clock
    def set_rx_bandwidth(self, bandwidth=None, channel=0):
        """Receive analog filter bandwidth [Hz]: the narrowest the device has that is at least `bandwidth` (None: the
        sample rate). Returns the bandwidth set"""
        return self._set_bandwidth_of("rx", bandwidth, channel)
    def set_tx_bandwidth(self, bandwidth=None, channel=0):
        """Transmit analog filter bandwidth [Hz] (see `set_rx_bandwidth`). Returns the bandwidth set"""
        return self._set_bandwidth_of("tx", bandwidth, channel)
    def get_rx_bandwidth(self, channel=0):
        """Receive analog filter bandwidth [Hz] last set (None: never set)"""
        return self._bandwidth.get((self._direction("rx", channel), channel))
    def get_tx_bandwidth(self, channel=0):
        """Transmit analog filter bandwidth [Hz] last set (None: never set)"""
        return self._bandwidth.get((self._direction("tx", channel), channel))

    def _set_bandwidth_of(self, d, bandwidth, channel):
        self._direction(d, channel)
        self._ensure_open(f"set_{d}_bandwidth")
        ranges = self.caps.bandwidths[d]
        if not ranges:
            raise err.NotSupported(f"{type(self).NAME} has no adjustable {d.upper()} filter")
        want = self._Fs if bandwidth is None else bandwidth
        fits = [r for r in ranges if r.hi >= want]
        if fits:
            bw = min(max(want, r.lo) for r in fits)
        else:
            bw = max(r.hi for r in ranges)
            self._invalid(f"Bandwidth {want:,.0f} is wider than {type(self).NAME} allows, using {bw:,.0f}")
        at = self._rx_mark() if d == "rx" else None
        bw = self._set_bandwidth(d, bw, channel=channel) or bw
        self._bandwidth[(d, channel)] = bw
        if d == "rx":
            self._note("bandwidth", bw, channel, False, at=at)
        return bw

    def rx_antennas(self, channel=0):
        """The receiver's selectable antenna ports ([]: just the one)"""
        return list(self.caps.antennas[self._direction("rx", channel)])
    def tx_antennas(self, channel=0):
        """The transmitter's selectable antenna ports ([]: just the one)"""
        return list(self.caps.antennas[self._direction("tx", channel)])
    def rx_antenna_info(self, channel=0):
        """Each receive antenna port and what is on it, as `rx_antennas` lists them (one entry, port None, when there is
        just the one): [{"port", "chain", "antenna", "inverted", "selected"}]. chain: the name of the `caps.Chain` the
        port goes through (None: none declared); antenna: `Chain.describe()` of the antenna fitted (None: not known;
        `pysdrlib.base.antennas.format_antenna` makes text of it); inverted: whether the spectrum comes mirrored there
        (see `get_rx_inverted`); selected: whether it is the port in use (None for each of several when none was ever
        selected: the radio's default)"""
        return self._antenna_info("rx", channel)
    def tx_antenna_info(self, channel=0):
        """Each transmit antenna port and what is on it: as `rx_antenna_info`"""
        return self._antenna_info("tx", channel)

    def _antenna_info(self, d, channel):
        self._direction(d, channel)
        ports = list(self.caps.antennas[d]) or [None]
        chosen = self._antenna.get((d, channel))
        out = []
        for port in ports:
            chain = self._chain_for_port(d, port)
            out.append({"port": port, "chain": (chain.name or None) if chain else None,
                        "antenna": chain.describe() if chain else None, "inverted": bool(chain and chain.invert),
                        "selected": True if len(ports) == 1 else (None if chosen is None else port == chosen)})
        return out

    def set_rx_antenna(self, name, channel=0):
        """Select a receive antenna port by name (see `rx_antennas`). Raises `err.NotSupported` for a name the device lacks"""
        return self._set_antenna_of("rx", name, channel)
    def set_tx_antenna(self, name, channel=0):
        """Select a transmit antenna port by name (see `tx_antennas`)"""
        return self._set_antenna_of("tx", name, channel)
    def get_rx_antenna(self, channel=0):
        """The receive antenna port last selected (None: never set)"""
        return self._antenna.get((self._direction("rx", channel), channel))
    def get_tx_antenna(self, channel=0):
        """The transmit antenna port last selected (None: never set)"""
        return self._antenna.get((self._direction("tx", channel), channel))

    def _set_antenna_of(self, d, name, channel):
        self._direction(d, channel)
        self._ensure_open(f"set_{d}_antenna")
        if name not in self.caps.antennas[d]:
            raise err.NotSupported(f"{type(self).NAME} has no {d.upper()} antenna {name!r} (it has {self.caps.antennas[d]})")
        before = self._chain(d, channel)
        self._set_antenna(d, name, channel=channel)
        self._antenna[(d, channel)] = name
        after = self._chain(d, channel)
        tuned = self._tuning.get(("freq", d, channel))
        if after is not before and tuned is not None:    # another chain moves frequencies differently: tune the same one over the air through it
            self._tune_freq(tuned, d, channel, False)
        return name

    def clock_sources(self):
        """Reference clock sources ([]: not selectable)"""
        return list(self.caps.clock_sources)
    def set_clock_source(self, name):
        """Select the reference clock by name (see `clock_sources`). Raises `err.NotSupported` for a name the device lacks"""
        self._ensure_open("set_clock_source")
        if name not in self.caps.clock_sources:
            raise err.NotSupported(f"{type(self).NAME} has no clock source {name!r} (it has {self.caps.clock_sources})")
        self._set_clock_source(name)
        self._clock_source = name
        return name
    def get_clock_source(self):
        """The reference clock last selected (None: never set)"""
        return self._clock_source

    def set_bias_t(self, bias):
        """Set bias-t"""
        self.log.trace("set_bias_t(%s)", bias)
        self._ensure_open("set_bias_t")
        self._set_bias_t(bias)
        self._settings[("bias_t", 0)] = bool(bias)

    # ------------------------------------------------------------------ device-specific settings
    def settings(self):
        """{name: caps.Setting}: everything else this device can be told (dither, AGC, test modes...)"""
        return dict(self.caps.settings)
    def set_setting(self, name, value, channel=0):
        """Apply one of `settings()` (to `channel`, where the setting is per channel). Returns the value set"""
        self._ensure_open("set_setting")
        self._check_channel("rx", channel)
        spec = self.caps.settings.get(name)
        if spec is None:
            raise err.NotSupported(f"{type(self).NAME} has no setting {name!r} (it has {list(self.caps.settings)})")
        value = spec.kind(value)
        if spec.choices is not None and value not in spec.choices:
            raise err.InvalidValue(f"{name} must be one of {spec.choices}, not {value!r}")
        method = getattr(self, spec.method)
        method(value, channel=channel) if spec.per_channel else method(value)
        self._settings[(name, channel)] = value
        return value
    def get_setting(self, name, channel=0):
        """Value of a device specific setting listed in `caps.settings`. Raises `err.NotSupported` for an unknown name"""
        spec = self.caps.settings.get(name)
        if spec is None:
            raise err.NotSupported(f"{type(self).NAME} has no setting {name!r}")
        if spec.getter:
            getter = getattr(self, spec.getter)
            return getter(channel=channel) if spec.per_channel else getter()
        return self._settings.get((name, channel), spec.default)

    # ------------------------------------------------------------------ capabilities
    @property
    def caps(self) -> "_caps.Capabilities":
        """What this device can do (see pysdrlib.base.caps). Refined by the driver once open"""
        is_open = self.state["open"]
        if self._caps is None or self._caps_open != is_open:
            c = _caps.for_class(type(self))
            c.settings.update(type(self).SETTINGS)
            if type(self)._set_bias_t is not Device._set_bias_t and "bias_t" not in c.settings:
                c.settings["bias_t"] = _caps.Setting(bool, "Power on the antenna port (bias tee)", "set_bias_t", default=False)
            if is_open:
                c = self._capabilities(c) or c
            self._caps, self._caps_open = c, is_open
        return self._caps
    def get_caps(self):
        """`caps` as plain types (what a remote client receives)"""
        return self.caps.json()

    # ------------------------------------------------------------------ reading
    def read(self, count, timeout=None, meta=False, channel=0, on_drop=None, latest=False):
        """Exactly `count` samples (complex64), waiting for them. Raises err.Timeout after `timeout` seconds
        (None: wait as long as the device delivers; a stalled device raises err.StreamStalled).
        meta=True returns (samples, RxMeta): the first sample's index, where samples were lost, retunes...
        channel: one of `rx_channels`, or several (a sequence): then a (channels, count) array whose rows start at
        the same sample index (the same instant), and a list of RxMeta.
        on_drop="raise": raise err.Dropped if samples were lost inside this block (`RxMeta.gaps`), for a capture that
        must be contiguous; the exception carries the block (`.samples`, `.meta`, `.lost`). None: return it anyway.
        latest=True: the newest `count` samples at the current settings, for a display: what is queued before them
        is skipped, and so is everything from before the last change of a setting (a retune, a new rate or gain...) and
        its settling (`caps.retune_settle`); if not enough has arrived since, wait for it. The skipped samples are
        counted in `RxMeta.discarded`, not as gaps"""
        _check_on_drop(on_drop)
        if latest:
            self._skip_to_latest(int(count), (channel,) if np.isscalar(channel) else tuple(channel))
        if not np.isscalar(channel):
            out, metas = self._read_aligned(int(count), timeout, True, tuple(channel))
            _gap_check(on_drop, out, metas)
            return (out, metas) if meta else out
        out = np.empty(int(count), dtype=np.complex64)
        m = self._read_into(out, timeout, channel)
        _gap_check(on_drop, out, m)
        return (out, m) if meta else out

    def _read_aligned(self, count, timeout, meta, channels):
        out = np.empty((len(channels), count), dtype=np.complex64)
        metas = [self._read_into(out[k], timeout, ch) for k, ch in enumerate(channels)]
        for _ in range(8):   # a channel that lost samples the others didn't is behind: bring it up to them
            start = max(m.index for m in metas)
            late = [k for k, m in enumerate(metas) if m.index < start]
            if not late:
                break
            for k in late:
                skip = start - metas[k].index
                keep = out[k, skip:].copy() if skip < count else np.empty(0, np.complex64)
                if skip >= count:   # it is a whole block (or more) behind: catch up first
                    self._read_into(np.empty(skip - count, np.complex64), timeout, channels[k])
                more = np.empty(count - len(keep), dtype=np.complex64)
                m = self._read_into(more, timeout, channels[k]) if len(more) else metas[k]
                out[k] = np.concatenate((keep, more))
                metas[k] = RxMeta(index=start, count=count, freq=metas[k].freq, rate=metas[k].rate, channel=channels[k],
                                  gaps=metas[k].gaps + m.gaps, discarded=metas[k].discarded + skip, time=metas[k].time,
                                  events=metas[k].events,
                                  hw_time=None if metas[k].hw_time is None else metas[k].hw_time + skip / metas[k].rate)
        return (out, metas) if meta else out
    def read_into(self, out, timeout=None, meta=False, channel=0, on_drop=None):
        """`read` into a complex64 array you own (filled completely). Returns its length, or (length, RxMeta)"""
        _check_on_drop(on_drop)
        if out.dtype != np.complex64 or not out.flags.c_contiguous:
            raise TypeError("read_into needs a contiguous complex64 array")
        m = self._read_into(out, timeout, channel)
        _gap_check(on_drop, out, m)
        return (len(out), m) if meta else len(out)
    def stream(self, count, timeout=None, meta=False, channel=0, flush=True, on_drop=None, latest=False):
        """Blocks of `count` samples, in a `with` block that starts receiving (and stops it after) when it wasn't:

            with sdr.stream(65536) as s:
                for x in s: ...

        flush: when already receiving, discard what was queued before the stream starts. on_drop: as `read`'s, for
        each block (an err.Dropped ends a `for` loop over the stream; call `s.read()` in a loop to carry on past one).
        latest: each block is the newest (see `read`), for a display that would rather skip than fall behind"""
        _check_on_drop(on_drop)
        return RxStream(self, count, timeout, meta, channel, flush, on_drop, latest)
    def sweep(self, start, stop=None, step=None, count=65536, timeout=None, meta=False, channel=0, repeat=False,
              offset=0.0, skip=0, on_drop=None, retries=3):
        """Step the receiver across frequencies, yielding (frequency, samples): `count` samples at each, all received
        after its retune and settling (`set_rx_freq(..., flush=True)`):

            for freq, x in sdr.sweep(88e6, 108e6, count=65536): ...          # start to stop (inclusive)
            for freq, x in sdr.sweep([162.4e6, 162.55e6], count=4096): ...   # or a list of frequencies

        step: Hz between steps (default: 80% of the sample rate, as a band's edges roll off). offset: tune this far
        above each frequency and shift the samples back, so the device's DC spike (its LO leaking through) lands
        `offset` Hz off the middle of each step's spectrum, not on it. skip: samples to discard after each retune on top
        of the device's own settling (`caps.retune_settle`). repeat: sweep again and again, until the loop breaks.
        meta: yield (frequency, samples, RxMeta). on_drop: "raise" (as `read`'s), or "retry": capture a step again
        when samples were lost inside it, up to `retries` times, then raise err.Dropped. Starts receiving if it wasn't,
        and stops it again when the sweep ends (or the loop breaks)"""
        if on_drop not in (None, "raise", "retry"):
            raise ValueError(f"on_drop is None, 'raise' or 'retry', not {on_drop!r}")
        if stop is None:
            freqs = np.asarray(list(np.atleast_1d(start)), dtype=float)
        else:
            if step is None:
                step = 0.8 * (self.get_rx_sample_rate(channel) or self.caps.default_rate)
            if step == 0 or (stop - start) * step < 0:
                raise ValueError(f"step {step} doesn't go from {start} to {stop}")
            freqs = start + step * np.arange(int(np.floor((stop - start) / step + 1e-9)) + 1)
        if not len(freqs):
            raise ValueError("no frequencies to sweep")
        started = not self.state["rx"]
        if started:
            self.start_rx()
        shift, shift_rate = None, None
        try:
            while True:
                for f in freqs:
                    tuned = self.set_rx_freq(float(f) + offset, channel=channel, flush=True) - offset
                    if skip:
                        self.read(int(skip), timeout=timeout, channel=channel)
                    for attempt in range(int(retries) + 1 if on_drop == "retry" else 1):
                        try:
                            x, m = self.read(count, timeout=timeout, meta=True, channel=channel,
                                             on_drop="raise" if on_drop else None)
                            break
                        except err.Dropped as e:
                            if on_drop != "retry" or attempt == int(retries):
                                raise
                            self.log.debug("sweep at %g Hz lost %d samples: capturing it again", tuned, e.lost)
                    if offset:
                        if shift is None or len(shift) != count or shift_rate != m.rate:
                            shift_rate = m.rate
                            shift = np.exp(2j * np.pi * offset / m.rate * np.arange(count)).astype(np.complex64)
                        x *= shift   # the signal at `tuned` sat at -offset: bring it to the middle
                        m = dataclasses.replace(m, freq=m.freq - offset)
                    yield (tuned, x, m) if meta else (tuned, x)
                if not repeat:
                    break
        finally:
            if started and self.state["rx"]:
                self.stop_rx()
    def find_gain(self, channel=0, count=65536, max_clip=1e-4, clip_level=0.98, headroom_db=0.0, confirm=None,
                  timeout=None, skip=0):
        """The highest receive gain at which the signal doesn't clip, found by bisecting the gains the device can set
        (clipping only grows with gain), and set. Returns it as a fraction of the gain range:

            sdr.find_gain()                        # -> 0.62: channel 0's gain, now in effect
            sdr.find_gain(channel=[0, 1])          # -> {0: 0.62, 1: 0.71}: each channel's, from aligned reads

        At each step the gain is set (skipping its settling, `flush=True`), `skip` more samples are discarded, and
        `count` are read; a block clips when more than `max_clip` of its samples have I or Q at `clip_level` of full
        scale or beyond. For a bursty signal (TDMA, WiFi, BLE...) `count` must span a burst: a window that misses
        them reads as clean. confirm: then read this many samples (default 4 * count) at the gain found, and step down
        while that clips. headroom_db: back off this much below the gain found.
        Channels whose gain is one setting (`caps.shared["gain"]`) get the gain at which none clips.
        The gain mode is set to "manual" (an AGC would undo it). Starts receiving if it wasn't, and stops it again; when
        already receiving, the channels must be among `rx_channels`. The result holds while the signal stays as it was"""
        multi = not np.isscalar(channel)
        channels = tuple(channel) if multi else (channel,)
        if self.state["rx"] and not set(channels) <= set(self.rx_channels):
            raise err.InvalidValue(f"find_gain: channels {list(channels)} aren't all receiving (rx_channels "
                                   f"{list(self.rx_channels)}): stop_rx() first, or start_rx(channels=...) with them")
        for ch in channels:
            if self.get_rx_gain_mode(ch) != "manual":
                self.set_rx_gain_mode("manual", ch)
        joint = multi and self.caps.shared.get("gain", "channel") != "channel"
        target = channels if multi else channel
        confirm = 4 * int(count) if confirm is None else int(confirm)
        steps = {ch: _gain_settings(self.caps.gains[self._direction("rx", ch)]) for ch in channels}
        measured = {ch: {} for ch in channels}   # gain in effect [dB] -> share of samples clipping

        def clipping(want, n, cache=True):
            """Set each channel's gain (dB), and the share of `n` samples clipping, by channel. With cache, a gain a
            channel already got is not read again (several settings asked for can come out the same)"""
            got = {ch: self.set_rx_gain_db(g, channel=ch, flush=True) for ch, g in want.items()}
            if cache and all(got[ch] in measured[ch] for ch in channels):
                return {ch: measured[ch][got[ch]] for ch in channels}
            if skip:
                self.read(int(skip), timeout=timeout, channel=target)
            x = np.atleast_2d(self.read(int(n), timeout=timeout, channel=target))
            over = ((np.abs(x.real) >= clip_level) | (np.abs(x.imag) >= clip_level)).mean(axis=1).tolist()
            share = dict(zip(channels, [max(over)] * len(over) if joint else over))
            if cache:
                for ch in channels:
                    measured[ch][got[ch]] = share[ch]
            return share

        started = not self.state["rx"]
        if started:
            self.start_rx(channels=channels if channels != (0,) else None)
        self._quiet_gain_rounding = True
        try:
            lo = dict.fromkeys(channels, -1)                          # index of the highest setting known not to clip
            hi = {ch: len(steps[ch]) for ch in channels}              # ... of the lowest known to clip
            share = clipping({ch: steps[ch][-1] for ch in channels}, count)
            for ch in channels:
                if share[ch] <= max_clip:
                    lo[ch] = len(steps[ch]) - 1
                else:
                    hi[ch] = len(steps[ch]) - 1
            while any(hi[ch] - lo[ch] > 1 for ch in channels):
                mid = {ch: (lo[ch] + hi[ch]) // 2 if hi[ch] - lo[ch] > 1 else max(lo[ch], 0) for ch in channels}
                share = clipping({ch: steps[ch][mid[ch]] for ch in channels}, count)
                for ch in channels:
                    if hi[ch] - lo[ch] > 1:
                        if share[ch] <= max_clip:
                            lo[ch] = mid[ch]
                        else:
                            hi[ch] = mid[ch]
            at = {ch: max(lo[ch], 0) for ch in channels}
            while confirm:   # a longer look: a window can miss a burst
                share = clipping({ch: steps[ch][at[ch]] for ch in channels}, confirm, cache=False)
                down = [ch for ch in channels if share[ch] > max_clip and at[ch] > 0]
                if not down:
                    break
                for ch in down:
                    at[ch] -= 1
            found = {}
            for ch in channels:
                if lo[ch] < 0:
                    self.log.warning("find_gain: channel %s clips even at the lowest gain", ch)
                gain = steps[ch][at[ch]]
                if headroom_db:
                    gain = max([g for g in steps[ch] if g <= gain - headroom_db] or [steps[ch][0]])
                self.set_rx_gain_db(gain, channel=ch, flush=True)
                found[ch] = self.get_rx_gain(channel=ch)
        finally:
            self._quiet_gain_rounding = False
            if started and self.state["rx"]:
                self.stop_rx()
        return found if multi else found[channel]

    def read_available(self, channel=0, count=None):
        """What is queued for a channel now (up to `count`), without waiting: (samples, index of the first). Stops at a
        gap, so the samples are contiguous; the next call's index shows the jump. For relaying a stream (the remote
        server); `read` is the way to read one"""
        self._ensure_open("read_available")
        slot = self._slot(channel)
        ready = self._rx_wait(0, 0.0, slot=slot)
        if count is not None:
            ready = min(ready, int(count))
        if not ready:
            total, _ = self._rx_position(slot=slot)
            return np.empty(0, dtype=np.complex64), int(total)
        x, idx = self._rx_read(ready, slot=slot)
        return x, int(idx)

    def wait_available(self, timeout, channel=0, count=1):
        """Wait up to `timeout` seconds until a channel has `count` samples queued; returns how many are queued (fewer
        than `count`: the time ran out). For relaying a stream (the remote server) without polling; `read` is the
        way to read one"""
        self._ensure_open("wait_available")
        return self._rx_wait(count, timeout, slot=self._slot(channel))

    def flush(self, channel=None):
        """Discard the samples waiting to be read (of every streaming channel: they stay aligned); returns how many"""
        self._ensure_open("flush")
        n = 0
        for slot in range(len(self._rx_channels)):
            n += self._rx_flush(slot=slot)
        total, _ = self._rx_position()
        self._rx_discard = max(self._rx_discard, total)
        return n

    def _skip_to_latest(self, count, channels):
        """`read(latest=True)`: move the discard point up to the newest `count` samples, and past the last change of a
        setting (and its settling) of any of `channels`"""
        self._ensure_open("read")
        if not self.state["rx"]:
            return   # (the read says why)
        slot = self._slot(channels[0])
        self._rx_wait(0, 0.0, slot=slot)   # (a remote device takes what has arrived, changes too)
        total, _ = self._rx_position(slot=slot)
        mark = total - count
        settle = int(self.caps.retune_settle * (self._Fs or 0)) + int(self.caps.retune_samples)
        for index, kind, _, ch in self._rx_events:
            if kind != "hw_time" and ch in channels:
                mark = max(mark, index + settle)
        self._rx_discard = max(self._rx_discard, mark)

    def _rx_idle(self):
        """`read` got nothing for a moment: raise err.Lost / err.StreamStalled if the device is gone or stopped
        delivering (a device without its own watchdog, as a remote one, checks here)"""

    def _slot(self, channel):
        try:
            return self._rx_channels.index(channel)
        except ValueError:
            raise err.InvalidValue(f"channel {channel} isn't streaming (start_rx(channels=...) has {self._rx_channels})") from None

    def _rx_not_started(self):
        """`read` found the device not receiving: raise err.NotStreaming, or return when it turns out to be (a remote
        viewer's state can be older than the server's)"""
        raise err.NotStreaming(f"{type(self).NAME} isn't receiving (call start_rx(), or use stream())")

    def _read_into(self, out, timeout, channel):
        self._ensure_open("read")
        self._check_channel("rx", channel)
        slot = self._slot(channel)
        count = len(out)
        deadline = None if timeout is None else time.monotonic() + timeout
        got, first, gaps, discarded = 0, None, [], 0
        while got < count:
            if not self.state["rx"]:
                self._rx_not_started()
            left = None if deadline is None else deadline - time.monotonic()
            try:
                n, idx = self._rx_read_into(out[got:], READ_SLICE if left is None else max(0.0, min(READ_SLICE, left)), slot)
                if n == 0:
                    self._rx_idle()
            except err.Lost as exc:   # the end of the stream: what this read had goes with the error
                exc.samples = out[:got].copy()
                exc.meta = self._meta(first, got, channel, gaps, discarded) if got else None
                raise
            if n == 0:
                if deadline is not None and time.monotonic() >= deadline:
                    raise err.Timeout(f"{type(self).NAME}: got {got} of {count} samples in {timeout}s")
                continue
            idx, expect = int(idx), self._rx_next.get(channel, 0)
            if self._rx_discard > expect:          # samples a flush skipped on purpose are not a gap
                skipped = max(0, min(idx, self._rx_discard) - expect)
                discarded += skipped
                expect += skipped
            if idx < self._rx_discard:             # ... nor are the ones still settling
                cut = min(n, self._rx_discard - idx)
                discarded += cut
                if cut < n:
                    out[got:got + n - cut] = out[got + cut:got + n]
                idx, n = idx + cut, n - cut
                expect = max(expect, idx)
                if n == 0:
                    self._rx_next[channel] = idx
                    continue
            if idx > expect:
                gaps.append((got, idx - expect))
            if first is None:
                first = idx
            got += n
            self._rx_next[channel] = idx + n
        if self.dc_correction and count:
            self._remove_dc(out, channel)
        return self._meta(first if first is not None else self._rx_next.get(channel, 0), count, channel, gaps, discarded)

    def _meta(self, first, count, channel, gaps, discarded):
        events, later, stamp = [], [], None
        before = self._rx_ends.get(channel, 0)          # where the last block returned ended: changes before it were reported
        for change in self._rx_events:
            index, kind, value, ch = change
            if ch != channel:                           # another channel's: its own reads report it
                later.append(change)
                continue
            if index <= first:
                self._rx_now[(kind, ch)] = value        # in effect at the block's start
                # at its first sample (or in a gap before it), not yet reported. A change a flush skipped past isn't
                # one: the whole block is at the new setting, as RxMeta says
                if index >= before and kind != "hw_time" and count and not discarded:
                    events.append(Event(0, kind, value, ch))
                continue
            if index < first + count:
                if kind == "hw_time":                   # (index, seconds): how the radio's clock reads, not a change
                    stamp = stamp or value
                else:
                    events.append(Event(index - first, kind, value, ch))
            later.append(change)
        self._rx_events = later
        if count:
            self._rx_ends[channel] = first + count + sum(n for at, n in gaps if at > 0)
        total, age = self._rx_position(slot=self._slot(channel))
        when = self._stream_time(channel, self._Fs, sample_time(self._Fs, total, age, first), first)
        rate = self._rx_now.get(("rate", channel), self._Fs)
        stamp = self._rx_now.get(("hw_time", channel)) or stamp
        hw_time = stamp[1] + (first - stamp[0]) / rate if stamp and rate else None
        return RxMeta(index=first, count=count, channel=channel,
                      freq=self._rx_now.get(("freq", channel), self._cf), rate=rate,
                      gaps=tuple(gaps), discarded=discarded, time=when,
                      events=tuple(events), hw_time=hw_time)

    def _stream_time(self, channel, rate, estimate, index):
        """Time of sample `index`. Each delivery estimate is jittery (by a block or more, worse on coarse clocks), so
        it only nudges the stream's start time (t0), and the time is t0 + index / rate: consecutive blocks are
        spaced by their samples, never out of order. t0 follows the clock slowly, so a rate that is not exactly
        nominal still can't drift far from the wall clock"""
        if estimate is None or not rate:
            return estimate
        t0 = estimate - index / rate
        clock = self._rx_clock.get(channel)
        if clock is None or clock[0] != rate or abs(clock[1] - t0) > 1.0:   # first block, a retune, or a jump
            clock = self._rx_clock[channel] = [rate, t0, -float("inf") if clock is None else clock[2]]
        else:
            clock[1] += 0.05 * (t0 - clock[1])
        clock[2] = max(clock[2], clock[1] + index / rate)
        return clock[2]

    def _remove_dc(self, out, channel):
        mean = complex(out.mean())
        prev = self._rx_dc.get(channel)
        tau = 0.1 * (self._Fs or 1.0)   # ~100 ms time constant
        a = min(1.0, len(out) / tau)
        dc = mean if prev is None else prev + a * (mean - prev)
        self._rx_dc[channel] = dc
        out -= np.complex64(dc)

    def _rx_reset(self):
        """Start of a receive stream: indices from 0, no pending changes"""
        self._rx_next = {}          # channel -> index the next sample returned should have (0 at first)
        self._rx_discard = 0        # samples before this index are skipped (flush=True)
        self._rx_events = []        # (index, kind, value, channel) changes not yet past
        self._rx_now = {}           # (kind, channel) -> value at the last block's start
        self._rx_ends = {}          # channel -> stream index after the last block returned (changes before it are reported)
        self._rx_dc = {}
        self._rx_clock = {}         # channel -> [rate, t0, last time]: the stream's time of sample 0, smoothed
        self._rx_buf = np.empty(0, dtype=np.complex64)
        self._rx_buf_index = 0

    def _rx_mark(self):
        """Where the receive stream is before a setting is sent to the hardware (None: not receiving). The change is
        marked there, not where the driver's call returns: a call can block for a long time (~130 ms for a bladeRF's
        cross-band retune) while samples keep arriving, and those are already at, or settling to, the new setting"""
        return self._rx_position()[0] if self.state.get("rx") else None

    def _note(self, kind, value, channel, flush, channels=None, at=None):
        """A receive setting changed (for `channels`, default `channel`): mark where in the stream (`at`: the position
        `_rx_mark` took before the hardware call; default now), and skip the settling with flush, counted from now"""
        if not self.state.get("rx"):
            return
        total, _ = self._rx_position()
        mark = total if at is None else min(at, total)
        for ch in (channels if channels is not None else (channel,)):
            if ch in self._rx_channels:
                self._rx_events.append((mark, kind, value, ch))
        if flush:
            for slot in range(len(self._rx_channels)):
                self._rx_flush(slot=slot)
            settle = int(self.caps.retune_settle * (self._Fs or 0)) + int(self.caps.retune_samples)
            self._rx_discard = max(self._rx_discard, total + settle)

    def _tuned(self, kind, value, dirs, channel, flush, at=None):
        """Record a frequency or rate for every (direction, channel) it changed, per caps.shared[kind]: "channel" (just
        that one), "direction" (all channels of the direction), "device" (everything); mark the receive stream"""
        shared = self.caps.shared.get(kind, "channel")
        hit = {}
        for d in dirs:
            chans = range(self.caps.channels.get(d, 1) or 1) if shared in ("direction", "device") else (channel,)
            hit[d] = tuple(chans)
            for ch in chans:
                self._tuning[(kind, d, ch)] = value
        if kind == "freq":
            self._cf = self._tuning.get(("freq", "rx", 0), self._cf)
        if "rx" in hit:
            self._note(kind, value, channel, flush, hit["rx"], at)

    def _hooked(self, direction, dirs, kind):
        """The directions whose hook a tuning call runs: the one asked for. With none asked for (both) and one synthesizer
        or clock for both, just "rx": its hook tunes the device (tx's would only call it again)"""
        if direction is not None:
            return (str(direction).lower(),)
        return ("rx",) if len(dirs) > 1 and self.caps.shared.get(kind) == "device" else dirs

    def _tuned_directions(self, direction, channel, kind):
        """The directions a tuning call acts on (and checks its channel): both for None, or when `kind` is shared
        by the whole device"""
        if direction is None or self.caps.shared.get(kind) == "device":
            dirs = tuple(d for d in _caps.DIRECTIONS if self.caps.channels.get(d)) or ("rx",)
        else:
            dirs = (self._direction(direction, 0),)
        for d in dirs:
            self._check_channel(d, channel)
        return dirs

    # ------------------------------------------------------------------ transmitting
    def write(self, samples, timeout=None, channel=None):
        """Queue samples (complex, full scale +-1) for transmission, after `start_tx`. Blocks while the queue is full,
        up to `timeout` seconds (None: until all are queued). Returns how many (per channel) were queued.
        channel: one of `tx_channels` (default: the stream's only one), or several with a (channels, n) array: those
        rows go out sample for sample together"""
        self._ensure_open("write")
        if not self.caps.channels.get("tx"):
            raise err.NotSupported(f"{type(self).NAME} can't transmit")
        if not self.state["tx"]:
            raise err.NotStreaming("write() needs start_tx() first")
        chans = self._tx_channels if channel is None else ((channel,) if np.isscalar(channel) else tuple(channel))
        x = np.asarray(samples, dtype=np.complex64)
        if x.ndim == 1:
            x = x[None]
        if x.ndim != 2 or len(x) != len(chans):
            raise ValueError(f"write {len(chans)} channel(s) {chans}: give a ({len(chans)}, n) array (or 1-D for one)")
        try:
            slots = [self._tx_channels.index(c) for c in chans]
        except ValueError:
            raise err.InvalidValue(f"channels {chans}: start_tx(channels=...) has {self._tx_channels}") from None
        limit = -1.0 if timeout is None else float(timeout)
        n, done = x.shape[1], 0
        step = n if len(slots) == 1 else TX_CHUNK
        while done < n:
            want = min(step, n - done)
            got = min(self._tx_write(np.ascontiguousarray(x[k, done:done + want]), limit, slot=s)
                      for k, s in enumerate(slots))
            done += got
            self._tx_check()
            if got < want and timeout is not None:   # timed out
                break
        return done
    def get_tx_stats(self, channel=None):
        """The transmit stream's counts for a channel (default: its first): written, sent, underruns, pending..."""
        slot = 0 if channel is None else self._tx_channels.index(channel)
        return self._tx_stats(slot=slot)

    def _channel_list(self, direction, channels):
        chs = (int(channels),) if np.isscalar(channels) else tuple(int(c) for c in channels)
        if not chs or len(set(chs)) != len(chs):
            raise err.InvalidValue(f"{direction} channels {channels}: one or more, each once")
        for ch in chs:
            self._check_channel(direction, ch)
        return chs

    # --- receive hooks: a driver with its own queue overrides these (see NativeDevice); the defaults use get_samples.
    # `slot` is the k-th of rx_channels; the defaults serve channel 0 only
    def _rx_read_into(self, out, timeout, slot=0):
        """Wait up to `timeout` seconds for len(out) samples, then move up to that many into `out`:
        (how many, index of the first). A driver with a queue of its own does this in one call (see NativeDevice)"""
        self._rx_wait(len(out), timeout, slot=slot)
        x, idx = self._rx_read(len(out), slot=slot)
        out[:len(x)] = x
        return len(x), idx
    def _rx_wait(self, count, timeout, slot=0):
        """Wait up to `timeout` seconds for `count` samples; returns how many are ready"""
        deadline = time.monotonic() + timeout
        while True:
            x = self._get_samples()
            if len(x):
                self._rx_buf = np.concatenate((self._rx_buf, x)) if len(self._rx_buf) else x
            if len(self._rx_buf) >= count or time.monotonic() >= deadline:
                return len(self._rx_buf)
            time.sleep(0.001)
    def _rx_read(self, count, slot=0):
        """(up to `count` samples, index of the first)"""
        x, idx = self._rx_buf[:count], self._rx_buf_index
        self._rx_buf = self._rx_buf[count:]
        self._rx_buf_index += len(x)
        return x, idx
    def _rx_position(self, slot=0):
        """(index the next sample to arrive gets, seconds since the device last delivered or None)"""
        return self._rx_buf_index + len(self._rx_buf), None
    def _rx_flush(self, slot=0):
        """Discard what is queued; returns how many"""
        x = self._get_samples()
        n = len(self._rx_buf) + len(x)
        self._rx_buf_index += n
        self._rx_buf = np.empty(0, dtype=np.complex64)
        return n

    # --- Helpers/Utils --- #
    def _invalid(self, msg):
        """An out-of-range value: a warning (it is clamped), or with `strict` an error"""
        if self.strict:
            raise err.InvalidValue(msg)
        warn.InvalidValue(msg)

    def _within(self, value, ranges, label):
        """`value` held to the outer limits of `ranges` (a warning, or with `strict` an error, when it is moved)"""
        lo, hi = min(r.lo for r in ranges), max(r.hi for r in ranges)
        if value < lo:
            self._invalid(f"{label} must be greater than {lo:,}")
            return lo
        if value > hi:
            self._invalid(f"{label} must be less than {hi:,}")
            return hi
        return value

    def _ppm_scale(self):
        return 1.0 + self._ppm * 1e-6

    def _direction(self, direction, channel):
        d = str(direction).lower()
        if d not in _caps.DIRECTIONS:
            raise ValueError(f"direction is 'rx' or 'tx', not {direction!r}")
        self._check_channel(d, channel)
        return d

    def _check_channel(self, direction, channel):
        if channel == 0:
            return
        n = self.caps.channels.get(direction, 0)
        if not 0 <= channel < n:
            raise err.InvalidValue(f"{type(self).NAME} has {n} {direction.upper()} channel(s); there is no channel {channel}")

    def _clamp_gain(self, gain, label, step, lo, hi):
        """Round `gain` down to a multiple of `step` and clamp it to [lo, hi], warning if changed"""
        if step:
            if gain % step:
                if not getattr(self, "_quiet_gain_rounding", False):   # (find_gain sets totals a split may round)
                    self._invalid(f"{label} gain must be a multiple of {step}")
                gain -= gain % step # default to lowering gain to next valid value
        if gain < lo:
            self._invalid(f"{label} gain must greater than {lo}")
            gain = lo
        elif gain > hi:
            self._invalid(f"{label} gain must be less than {hi}")
            gain = hi
        return gain

    def _check_gain(self, gain, direction):
        """Validate a total ("RX"/"TX") gain against the device's overall limits"""
        span = self.caps.gain_range(direction.lower())
        return self._clamp_gain(gain, direction, None, span.lo, span.hi)

    def _set_gain_stage(self, direction, stage, gain, setter, channel=0):
        """For devices to build their own per-stage gain setters on.

        A device exposes whatever stages its hardware has, under its own names, e.g.

            def set_rx_mixer_gain(self, gain=None):
                return self._set_gain_stage("RX", "MIXER", gain, self._set_rx_mixer_gain)

        Handles the open check, the device's default, validation against the stage's limits declared
        in its caps, and then calls `setter(validated_gain)`. Returns the gain that was set.
        A stage the driver lists `values` for (uneven steps) snaps to the nearest of them.
        """
        label = f"{direction} {stage}".replace("_", " ")
        self.log.trace("set_%s_%s_gain(%s)", direction.lower(), stage.lower(), gain)
        self._ensure_open(f"set_{direction.lower()}_{stage.lower()}_gain")
        spec = self.caps.stage(direction, stage)
        if spec is None:
            raise NotImplementedError(f"{type(self).NAME} has no {label} gain stage")
        if gain is None:
            gain = spec.default
        gain = self._clamp_gain(gain, label, spec.step, spec.min, spec.max)
        if spec.values:
            gain = min(spec.values, key=lambda v: abs(v - gain))
        setter(gain, channel=channel)
        self._gain_values.setdefault((direction, channel), {})[stage] = gain
        return gain

    def _ensure_open(self, name):
        if not self.state["open"]:
            raise err.NotOpen(f"Cannot call `{name}` when device isn't open")

    # --- Defined in child classes --- #
    def _open(self, *args, **kwargs):
        raise NotImplementedError()
    def _close(self):
        raise NotImplementedError()

    def _start_rx(self):
        raise NotImplementedError()
    def _stop_rx(self):
        raise NotImplementedError()
    def _get_samples(self) -> np.ndarray:
        raise NotImplementedError()

    def _start_tx(self):
        pass
    def _stop_tx(self):
        pass

    def _initialize(self, cf, Fs, rx_gain, tx_gain):
        pass
    def _set_rx_sample_rate(self, Fs, channel=0) -> "float | None":
        """Set the receive sample rate; return the rate the hardware chose (None: the one asked for, or what the hook
        left in `self._Fs`). Where rx and tx share one clock it sets both"""
        ...
    def _set_tx_sample_rate(self, Fs, channel=0) -> "float | None":
        """Set the transmit sample rate. By default, where rx and tx share one clock, the receive hook's"""
        if self.caps.shared.get("rate") == "device":
            return self._set_rx_sample_rate(Fs, channel)
        raise err.NotSupported(f"{type(self).NAME} can't transmit")
    def _set_rx_freq(self, freq, channel=0):
        """Tune the receiver (and the transmitter too, where they share one synthesizer)"""
        ...
    def _set_tx_freq(self, freq, channel=0):
        """Tune the transmitter. By default, where rx and tx share one synthesizer, the receive hook's"""
        if self.caps.shared.get("freq") == "device":
            return self._set_rx_freq(freq, channel)
        raise err.NotSupported(f"{type(self).NAME} can't transmit")

    def _set_rx_gain(self, gain, channel=0):
        return self._set_total_gain("rx", gain, channel)

    def _set_tx_gain(self, gain, channel=0):
        return self._set_total_gain("tx", gain, channel)

    def _set_total_gain(self, direction, gain, channel=0):
        """Set every stage of a direction: their defaults ("default"), or `_split_gain`'s share of a total.
        Returns {stage (lower case): gain}"""
        d = direction.lower()
        stages = self.caps.gains[d]
        if not stages:
            raise NotImplementedError(f"{type(self).NAME} has no {d.upper()} gain stages")
        shares = {n: s.default for n, s in stages.items()} if gain == "default" else self._split_gain(gain, d)
        out = {}
        for name, value in shares.items():
            setter = getattr(self, f"_set_{d}_{name.lower()}_gain")
            out[name.lower()] = self._set_gain_stage(d.upper(), name, value, setter, channel)
        return out

    def split_rx_gain_db(self, total):
        """How a total receive gain [dB] is shared between the stages: `_split_gain`'s answer, {stage: gain}"""
        return self._split_gain(total, "rx")
    def split_tx_gain_db(self, total):
        """How a total transmit gain [dB] is shared between the stages: {stage: gain}"""
        return self._split_gain(total, "tx")

    def _split_gain(self, total, direction):
        """How a total gain is shared between a direction's stages: {stage: gain}, chosen from every combination the
        stages allow. Stages in GAIN_RESERVE stay at their least unless the others can't come within their finest
        step of `total`. Then: the sum nearest `total` (not above it when there is a choice), and the stages as evenly
        loaded as possible: each the same fraction of its range, or (GAIN_BALANCE = "db") the same number of dB.
        A tie goes to the stage nearer the antenna (the first listed), which keeps the noise figure down"""
        d = direction.lower()
        stages = self.caps.gains[d]
        reserve = frozenset(n for n in type(self).GAIN_RESERVE.get(d, ()) if n in stages)
        return dict(_split(tuple(stages.items()), reserve, type(self).GAIN_BALANCE == "db", total, type(self).NAME))

    def _set_bias_t(self, bias):
        raise NotImplementedError()

    def _capabilities(self, caps):
        """Refine `caps` (from CAPS) for the attached unit: return it, or a new one. Called once open"""
        return caps
    def _set_gain_mode(self, direction, mode, channel=0):
        raise err.NotSupported(f"{type(self).NAME} has no gain modes")
    def _set_bandwidth(self, direction, bandwidth, channel=0):
        """Set the analog filter; return the bandwidth the hardware chose (None: the one asked for)"""
        raise err.NotSupported(f"{type(self).NAME} has no adjustable filter")
    def _set_antenna(self, direction, name, channel=0):
        raise err.NotSupported(f"{type(self).NAME} has no antenna selection")
    def _set_clock_source(self, name):
        raise err.NotSupported(f"{type(self).NAME} has no clock source selection")
    def _set_rx_channels(self, channels):
        """Choose the channels the receive stream carries (a driver with more than one)"""
        raise err.NotSupported(f"{type(self).NAME} streams only channel 0")
    def _set_tx_channels(self, channels):
        raise err.NotSupported(f"{type(self).NAME} transmits only channel 0")
    def _tx_write(self, samples, timeout, slot=0):
        """Queue complex64 samples for the slot-th transmit channel; return how many were queued"""
        raise err.NotSupported(f"{type(self).NAME} has no transmit path")
    def _tx_check(self):
        """Raise if transmitting has failed"""
    def _tx_stats(self, slot=0):
        raise err.NotSupported(f"{type(self).NAME} has no transmit statistics")


# --- the gain split (Device._split_gain), cached: a device's stages are fixed, and a GUI's gain slider asks again and again
@functools.lru_cache(maxsize=1024)
def _check_on_drop(on_drop):
    if on_drop not in (None, "raise"):
        raise ValueError(f"on_drop is None or 'raise', not {on_drop!r}")

def _gap_check(on_drop, samples, meta):
    """err.Dropped (carrying the block) when on_drop is "raise" and samples were lost inside it"""
    if on_drop != "raise":
        return
    metas = meta if isinstance(meta, list) else [meta]
    lost = sum(n for m in metas for at, n in m.gaps if at > 0)   # (one before the first sample isn't inside it)
    if lost:
        e = err.Dropped(f"{lost:,} samples lost inside a block of {metas[0].count:,}"
                        + (f" (channels {[m.channel for m in metas if any(at for at, _ in m.gaps)]})" if len(metas) > 1 else ""))
        e.samples, e.meta, e.lost = samples, meta, lost
        raise e

def _gain_settings(stages):
    """The total gains (dB, ascending) a direction's stages ({name: GainStage}) can make"""
    options = [_stage_values(spec) for spec in stages.values()]
    if not options:
        return [0.0]
    if np.prod([len(o) for o in options], dtype=float) > 1e5:   # too many to list: whole dB across the range
        lo, hi = sum(min(o) for o in options), sum(max(o) for o in options)
        return sorted(set(np.arange(lo, hi, 1.0).tolist()) | {hi})
    return sorted({round(sum(combo), 6) for combo in itertools.product(*options)})

def _split(stages, reserve, by_db, total, name):
    """{stage: gain} sharing `total` between `stages` ((name, GainStage) pairs, in signal order); see `_split_gain`"""
    names = [n for n, _ in stages]
    spec = dict(stages)
    options = [_stage_values(spec[n]) for n in names]
    if np.prod([len(o) for o in options], dtype=float) > 1e6:
        raise NotImplementedError(f"{name}: too many gain combinations to search; split it in the driver")

    def load(n, v):
        span = spec[n].max - spec[n].min
        return v - spec[n].min if by_db else ((v - spec[n].min) / span if span else 0.0)

    def best_of(combos):
        best, best_key = None, None
        for combo in combos:
            total_db = sum(combo)
            used = [load(n, v) for n, v in zip(names, combo) if n not in reserve]
            key = (round(abs(total_db - total), 9), total_db > total,
                   sum(v - spec[n].min for n, v in zip(names, combo) if n in reserve),
                   (max(used) - min(used)) if used else 0.0,
                   tuple(-v for v in combo))   # a tie: gain nearer the antenna (stages are listed in signal order)
            if best_key is None or key < best_key:
                best, best_key = combo, key
        return best, best_key

    if reserve:   # first without the reserve stages
        held = [o if n not in reserve else (spec[n].min,) for n, o in zip(names, options)]
        combo, key = best_of(itertools.product(*held))
        steps = [_resolution(spec[n]) for n in names if n not in reserve]
        if combo is not None and key[0] <= (min(steps) if steps else 0):
            return tuple(zip(names, combo))
    combo, _ = best_of(itertools.product(*options))
    return tuple(zip(names, combo))

def _resolution(spec):
    """The finest change a stage can make"""
    values = sorted(set(_stage_values(spec)))
    gaps = [b - a for a, b in zip(values, values[1:])]
    return min(gaps) if gaps else 0

def _stage_values(spec):
    """The gains a stage can be set to: its table, its steps, or (continuous) whole dB"""
    if spec.values:
        return tuple(spec.values)
    step = spec.step or 1
    if all(isinstance(v, int) for v in (spec.min, spec.max, step)):
        return tuple(range(spec.min, spec.max + 1, step))
    return tuple(np.arange(spec.min, spec.max + step / 2, step).tolist())
