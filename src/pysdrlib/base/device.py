"""The device-agnostic interface every SDR presents

Lifecycle and tuning (`open`, `initialize`, `set_freq`, `set_sample_rate`, `start_rx`...), reading (`read`, `stream`,
`get_samples`), and the controls a device may have, discovered through `caps` (see `pysdrlib.base.caps`):

    sdr = pysdrlib.device("rtl_sdr"); sdr.open()
    sdr.caps.gains["rx"]                     # the device's own gain stages
    sdr.set_gain(30)                         # total gain, split by the device; sdr.set_gain(12, stage="IF"): one stage
    sdr.set_bandwidth(1.5e6)                 # analog filter, when the device has one
    sdr.set_freq_correction(14.8)            # ppm error of the device's reference
    sdr.set_setting("dither", True)          # anything else the device has (caps.settings)
    with sdr.stream(65536, meta=True) as s:
        for x, m in s: ...                   # m: RxMeta (sample index, gaps, retunes inside the block)

Every new control takes `channel=0` (the channel count is in `caps.channels`). A device that lacks a control
raises `err.NotSupported`. Values out of range are clamped with a warning, or raise `err.InvalidValue` with
`strict = True`.
"""
import itertools
import time

import numpy as np

from .. import logger
from .. import warn
from .. import err
from . import caps as _caps
from .config import ConfigDevice
from .stream import Event, RxMeta, RxStream, sample_time

READ_SLICE = 0.1   # seconds a read waits at a time, between checks on the stream (stalls, stop_rx)

class Device:
    NAME = "pysdrlib Device"
    CONFIG: ConfigDevice = None # type: ignore
    CAN_TRANSMIT = False
    DUPLEX = False
    SAMPLE_FORMAT = None # convert.SampleFormat of the raw stream the driver delivers (None: driver converts itself)
    RETUNE_SETTLE = 0.0  # seconds after a retune the hardware may still be settling
    RETUNE_SAMPLES = 0   # samples from before a retune that may still arrive after it (in-flight transfers)
    SETTINGS: dict = {}  # {name: caps.Setting}: the device's own controls, see `set_setting`
    GAIN_RESERVE: dict = {}  # {"rx"/"tx": (stage, ...)}: stages a total gain uses only when the others can't reach it
    GAIN_BALANCE = "range"   # how a total is shared: "range" (each stage the same fraction of its range) or "db" (equal dB)

    __slots__ = (
        "log",
        "device", "state",
        "_cf", "_Fs",
        "strict", "dc_correction",
        "_ppm", "_gain_values", "_gain_mode", "_bandwidth", "_antenna", "_clock_source", "_settings",
        "_caps", "_caps_open",
        "_rx_next", "_rx_discard", "_rx_events", "_rx_now", "_rx_dc", "_rx_buf", "_rx_buf_index",
    )
    def __init__(self):
        self.log = logger.new(f"device.{type(self).__name__}")
        self.device = None
        self.state = {
            "open": False,
            "rx": False,
            "tx": False
        }
        self._cf: float = type(self).CONFIG.DEFAULT_FREQ # type: ignore
        self._Fs: float = type(self).CONFIG.DEFAULT_SAMPLE_RATE # type: ignore
        self._init_generic()

    def _init_generic(self):
        """State of the generic controls (also for subclasses that don't call Device.__init__)"""
        self.strict = False         # out-of-range values raise err.InvalidValue instead of being clamped
        self.dc_correction = False  # remove the DC offset (a slow running mean) from what `read` returns
        self._ppm = 0.0
        self._gain_values = {"RX": {}, "TX": {}}
        self._gain_mode = {"rx": "manual", "tx": "manual"}
        self._bandwidth = {"rx": None, "tx": None}
        self._antenna = {"rx": None, "tx": None}
        self._clock_source = None
        self._settings = {}
        self._caps = None
        self._caps_open = False
        self._rx_reset()

    def __str__(self):
        return f"{type(self).__name__}: {self.state}"

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
    def initialize(self, cf=None, Fs=None, rx_gain=None, tx_gain=None):
        """Initialize the device settings"""
        self.log.trace("initialize(cf=%s, Fs=%s, rx_gain=%s, tx_gain=%s)", cf, Fs, rx_gain, tx_gain)
        self._ensure_open("initialize")
        cf = self.set_freq(cf)
        Fs = self.set_sample_rate(Fs)
        rx_gain = self.set_rx_gain(rx_gain)
        tx_gain = self.set_tx_gain(tx_gain) if type(self).CAN_TRANSMIT else None
        self._initialize(cf, Fs, rx_gain, tx_gain)

    def close(self):
        """Disconnect from device"""
        self.log.trace("close()")
        self._ensure_open("close")
        self._close()
        self.state["open"] = False
        self._caps = None

    def __enter__(self):
        self.open()
        return self
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def start_rx(self):
        """Start receiving"""
        if self.state["rx"]:
            self.log.warning("Device is already receiving!")
            return
        self.log.trace("start_rx()")
        self._ensure_open("start_rx")
        self._rx_reset()
        self._start_rx()
        self.state["rx"] = True
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
        """Get samples from buffer: whatever has arrived since the last call (possibly none). See `read`"""
        # self.log.trace("get_samples()")
        self._ensure_open("get_samples")
        return self._get_samples()

    def start_tx(self):
        """Start transmitting"""
        if self.state["tx"]:
            self.log.warning("Device is already transmitting!")
            return
        self.log.trace("start_tx()")
        self._ensure_open("start_rx")
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
    def set_sample_rate(self, Fs=None, channel=0, flush=False):
        """Set sample rate (the nominal one: a frequency correction is applied to what the hardware is asked for).
        flush: while receiving, discard the samples from before the change. Returns the rate in effect"""
        self.log.trace("set_sample_rate(%s)", Fs)
        self._ensure_open("set_sample_rate")
        self._check_channel("rx", channel)
        if Fs is None:
            Fs = type(self).CONFIG.DEFAULT_SAMPLE_RATE
        if Fs < type(self).CONFIG.SAMPLE_RATE_MIN:
            self._invalid(f"Sample rate must be greater than {type(self).CONFIG.SAMPLE_RATE_MIN:,}")
            Fs = type(self).CONFIG.SAMPLE_RATE_MIN
        elif Fs > type(self).CONFIG.SAMPLE_RATE_MAX:
            self._invalid(f"Sample rate must be less than {type(self).CONFIG.SAMPLE_RATE_MAX:,}")
            Fs = type(self).CONFIG.SAMPLE_RATE_MAX
        scale = self._ppm_scale()
        hw = Fs / scale if self._ppm else Fs
        if hw != Fs and not _caps.contains(self.caps.rates, hw):
            hw, scale = Fs, 1.0    # discrete rates: the hardware can't be asked for a corrected one
        self._Fs = hw
        self._set_sample_rate(hw)          # a driver may snap self._Fs to what the hardware does
        self._Fs = Fs if self._Fs == hw else self._Fs * scale
        self._note("rate", self._Fs, channel, flush)
        return self._Fs
    def get_sample_rate(self, channel=0):
        return self._Fs
    def set_freq(self, freq=None, channel=0, flush=False):
        """Set center frequency (corrected by `set_freq_correction`).
        flush: while receiving, discard what was received before the change and the device's settling time
        after it (`caps.retune_settle`), so the next `read` is all at the new frequency. Returns the frequency"""
        self.log.trace("set_freq(%s)", freq)
        self._ensure_open("set_freq")
        self._check_channel("rx", channel)
        if freq is None:
            freq = type(self).CONFIG.DEFAULT_FREQ
        if freq < type(self).CONFIG.FREQ_MIN:
            self._invalid(f"Frequency must be greater than {type(self).CONFIG.FREQ_MIN:,}")
            freq = type(self).CONFIG.FREQ_MIN
        elif freq > type(self).CONFIG.FREQ_MAX:
            self._invalid(f"Frequency must be less than {type(self).CONFIG.FREQ_MAX:,}")
            freq = type(self).CONFIG.FREQ_MAX
        self._cf = freq
        self._set_freq(freq / self._ppm_scale() if self._ppm else freq)
        self._note("freq", self._cf, channel, flush)
        return self._cf
    def get_freq(self, channel=0):
        return self._cf

    def set_freq_correction(self, ppm, channel=0):
        """Error of the device's frequency reference in parts per million (positive: it runs fast). Tuning and
        sample rate are both corrected, so `set_freq(f)` gives f and `set_sample_rate(r)` gives r (the rate only
        where the device has continuous rates). Re-applies them."""
        self._ensure_open("set_freq_correction")
        self._check_channel("rx", channel)
        self._ppm = float(ppm)
        self.set_freq(self._cf, channel)
        self.set_sample_rate(self._Fs, channel)
        return self._ppm
    def get_freq_correction(self, channel=0):
        return self._ppm

    # ------------------------------------------------------------------ gain
    def set_rx_gain(self, gain=None):
        """Set abstract receive gain"""
        self.log.trace("set_rx_gain(%s)", gain)
        self._ensure_open("set_rx_gain")
        if gain is None:
            gain = "default"
        else:
            gain = self._check_gain(gain, "RX")
        return self._set_rx_gain(gain)
    def set_tx_gain(self, gain=None):
        """Set abstract transmit gain"""
        self.log.trace("set_tx_gain(%s)", gain)
        self._ensure_open("set_tx_gain")
        if gain is None:
            gain = "default"
        else:
            gain = self._check_gain(gain, "TX")
        return self._set_tx_gain(gain)

    def gains(self, direction="rx", channel=0):
        """{stage name: GainStage} of a direction, as the device names them"""
        d = self._direction(direction, channel)
        return dict(self.caps.gains[d])
    def set_gain(self, gain=None, stage=None, direction="rx", channel=0, flush=False):
        """The total gain of a direction (the device splits it across its stages), or one `stage`'s (see
        `gains()`). None: the default. Returns the gain in effect (the total, or the stage's)"""
        d = self._direction(direction, channel)
        if stage is None:
            (self.set_rx_gain if d == "rx" else self.set_tx_gain)(gain)
        else:
            name = str(stage).upper()
            setter = getattr(self, f"_set_{d}_{name.lower()}_gain", None)
            if name not in self.caps.gains[d] or setter is None:
                raise err.NotSupported(f"{type(self).NAME} has no {d.upper()} gain stage {stage!r} (it has {list(self.caps.gains[d])})")
            self._set_gain_stage(d.upper(), name, gain, setter)
        value = self.get_gain(stage, d, channel)
        self._note("gain", value, channel, flush)
        return value
    def get_gain(self, stage=None, direction="rx", channel=0):
        """Gain last set: a stage's, or the total of the stages (None before any was set)"""
        d = self._direction(direction, channel)
        values = self._gain_values[d.upper()]
        if stage is not None:
            return values.get(str(stage).upper())
        return sum(values.values()) if values else None

    def set_gain_relative(self, fraction, stage=None, direction="rx", channel=0, flush=False):
        """Gain as a fraction of the device's range: 0.0 its least, 1.0 its most (linear in dB), of the total or of one
        `stage`. The same fraction is the same place in the range on every device. Returns the fraction in effect"""
        d = self._direction(direction, channel)
        lo, hi = self._gain_span(stage, d)
        if not 0.0 <= fraction <= 1.0:
            self._invalid(f"Relative gain must be between 0 and 1, not {fraction}")
            fraction = min(max(fraction, 0.0), 1.0)
        self.set_gain(lo + fraction * (hi - lo), stage, d, channel, flush)
        return self.get_gain_relative(stage, d, channel)
    def get_gain_relative(self, stage=None, direction="rx", channel=0):
        """`get_gain` as a fraction of the range (None before a gain was set)"""
        d = self._direction(direction, channel)
        gain = self.get_gain(stage, d, channel)
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

    def gain_modes(self, direction="rx", channel=0):
        """["manual"] and, when the device can set its own gain, "auto\""""
        return list(self.caps.gain_modes[self._direction(direction, channel)])
    def set_gain_mode(self, mode, direction="rx", channel=0):
        """"auto": the device sets its own gain (AGC), "manual": the gain last set"""
        d = self._direction(direction, channel)
        self._ensure_open("set_gain_mode")
        if mode not in self.caps.gain_modes[d]:
            raise err.NotSupported(f"{type(self).NAME} has no {d.upper()} gain mode {mode!r} (it has {self.caps.gain_modes[d]})")
        self._set_gain_mode(d, mode)
        self._gain_mode[d] = mode
        return mode
    def get_gain_mode(self, direction="rx", channel=0):
        return self._gain_mode[self._direction(direction, channel)]

    # ------------------------------------------------------------------ filter, antenna, clock
    def set_bandwidth(self, bandwidth=None, direction="rx", channel=0):
        """Analog filter bandwidth [Hz]: the narrowest the device has that is at least `bandwidth` (None: the
        sample rate). Returns the bandwidth set"""
        d = self._direction(direction, channel)
        self._ensure_open("set_bandwidth")
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
        bw = self._set_bandwidth(d, bw) or bw
        self._bandwidth[d] = bw
        self._note("bandwidth", bw, channel, False)
        return bw
    def get_bandwidth(self, direction="rx", channel=0):
        return self._bandwidth[self._direction(direction, channel)]

    def antennas(self, direction="rx", channel=0):
        """Selectable antenna ports ([]: just the one)"""
        return list(self.caps.antennas[self._direction(direction, channel)])
    def set_antenna(self, name, direction="rx", channel=0):
        d = self._direction(direction, channel)
        self._ensure_open("set_antenna")
        if name not in self.caps.antennas[d]:
            raise err.NotSupported(f"{type(self).NAME} has no {d.upper()} antenna {name!r} (it has {self.caps.antennas[d]})")
        self._set_antenna(d, name)
        self._antenna[d] = name
        return name
    def get_antenna(self, direction="rx", channel=0):
        return self._antenna[self._direction(direction, channel)]

    def clock_sources(self):
        """Reference clock sources ([]: not selectable)"""
        return list(self.caps.clock_sources)
    def set_clock_source(self, name):
        self._ensure_open("set_clock_source")
        if name not in self.caps.clock_sources:
            raise err.NotSupported(f"{type(self).NAME} has no clock source {name!r} (it has {self.caps.clock_sources})")
        self._set_clock_source(name)
        self._clock_source = name
        return name
    def get_clock_source(self):
        return self._clock_source

    def set_bias_t(self, bias):
        """Set bias-t"""
        self.log.trace("set_bias_t(%s)", bias)
        self._ensure_open("set_bias_t")
        self._set_bias_t(bias)
        self._settings["bias_t"] = bool(bias)

    # ------------------------------------------------------------------ device-specific settings
    def settings(self):
        """{name: caps.Setting}: everything else this device can be told (dither, AGC, test modes...)"""
        return dict(self.caps.settings)
    def set_setting(self, name, value):
        """Apply one of `settings()`. Returns the value set"""
        self._ensure_open("set_setting")
        spec = self.caps.settings.get(name)
        if spec is None:
            raise err.NotSupported(f"{type(self).NAME} has no setting {name!r} (it has {list(self.caps.settings)})")
        value = spec.kind(value)
        if spec.choices is not None and value not in spec.choices:
            raise err.InvalidValue(f"{name} must be one of {spec.choices}, not {value!r}")
        getattr(self, spec.method)(value)
        self._settings[name] = value
        return value
    def get_setting(self, name):
        spec = self.caps.settings.get(name)
        if spec is None:
            raise err.NotSupported(f"{type(self).NAME} has no setting {name!r}")
        if spec.getter:
            return getattr(self, spec.getter)()
        return self._settings.get(name, spec.default)

    # ------------------------------------------------------------------ capabilities
    @property
    def caps(self) -> "_caps.Capabilities":
        """What this device can do (see pysdrlib.base.caps). Refined by the driver once open"""
        is_open = self.state["open"]
        if self._caps is None or self._caps_open != is_open:
            c = _caps.from_config(type(self))
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
    def read(self, count, timeout=None, meta=False, channel=0):
        """Exactly `count` samples (complex64), waiting for them. Raises err.Timeout after `timeout` seconds
        (None: wait as long as the device delivers; a stalled device raises err.StreamStalled).
        meta=True returns (samples, RxMeta): the first sample's index, where samples were lost, retunes..."""
        out = np.empty(int(count), dtype=np.complex64)
        m = self._read_into(out, timeout, channel)
        return (out, m) if meta else out
    def read_into(self, out, timeout=None, meta=False, channel=0):
        """`read` into a complex64 array you own (filled completely). Returns its length, or (length, RxMeta)"""
        if out.dtype != np.complex64 or not out.flags.c_contiguous:
            raise TypeError("read_into needs a contiguous complex64 array")
        m = self._read_into(out, timeout, channel)
        return (len(out), m) if meta else len(out)
    def stream(self, count, timeout=None, meta=False, channel=0, flush=True):
        """Blocks of `count` samples, in a `with` block that starts receiving (and stops it after) when it wasn't:

            with sdr.stream(65536) as s:
                for x in s: ...

        flush: when already receiving, discard what was queued before the stream starts"""
        return RxStream(self, count, timeout, meta, channel, flush)
    def flush(self, channel=0):
        """Discard the samples waiting to be read; returns how many"""
        self._ensure_open("flush")
        self._check_channel("rx", channel)
        n = self._rx_flush()
        total, _ = self._rx_position()
        self._rx_discard = max(self._rx_discard, total)
        return n

    def _read_into(self, out, timeout, channel):
        self._ensure_open("read")
        self._check_channel("rx", channel)
        count = len(out)
        deadline = None if timeout is None else time.monotonic() + timeout
        got, first, gaps, discarded = 0, None, [], 0
        while got < count:
            if not self.state["rx"]:
                raise err.NotStreaming(f"{type(self).NAME} isn't receiving (call start_rx(), or use stream())")
            left = None if deadline is None else deadline - time.monotonic()
            self._rx_wait(count - got, READ_SLICE if left is None else max(0.0, min(READ_SLICE, left)))
            x, idx = self._rx_read(count - got)
            n = len(x)
            if n == 0:
                if deadline is not None and time.monotonic() >= deadline:
                    raise err.Timeout(f"{type(self).NAME}: got {got} of {count} samples in {timeout}s")
                continue
            idx, expect = int(idx), self._rx_next
            if self._rx_discard > expect:          # samples a flush skipped on purpose are not a gap
                skipped = max(0, min(idx, self._rx_discard) - expect)
                discarded += skipped
                expect += skipped
            if idx < self._rx_discard:             # ... nor are the ones still settling
                cut = min(n, self._rx_discard - idx)
                discarded += cut
                x, idx, n = x[cut:], idx + cut, n - cut
                expect = max(expect, idx)
                if n == 0:
                    self._rx_next = idx
                    continue
            if idx > expect:
                gaps.append((got, idx - expect))
            if first is None:
                first = idx
            out[got:got + n] = x
            got += n
            self._rx_next = idx + n
        if self.dc_correction and count:
            self._remove_dc(out, channel)
        return self._meta(first if first is not None else self._rx_next, count, channel, gaps, discarded)

    def _meta(self, first, count, channel, gaps, discarded):
        events, later = [], []
        for change in self._rx_events:
            index, kind, value, ch = change
            if index <= first:
                self._rx_now[(kind, ch)] = value        # in effect at the block's start
                continue
            if index < first + count:
                events.append(Event(index - first, kind, value, ch))
            later.append(change)
        self._rx_events = later
        total, age = self._rx_position()
        return RxMeta(index=first, count=count, channel=channel,
                      freq=self._rx_now.get(("freq", channel), self._cf), rate=self._rx_now.get(("rate", channel), self._Fs),
                      gaps=tuple(gaps), discarded=discarded, time=sample_time(self._Fs, total, age, first),
                      events=tuple(events))

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
        self._rx_next = 0           # index the next sample returned should have
        self._rx_discard = 0        # samples before this index are skipped (flush=True)
        self._rx_events = []        # (index, kind, value, channel) changes not yet past
        self._rx_now = {}           # (kind, channel) -> value at the last block's start
        self._rx_dc = {}
        self._rx_buf = np.empty(0, dtype=np.complex64)
        self._rx_buf_index = 0

    def _note(self, kind, value, channel, flush):
        """A setting changed: mark where in the stream (and skip the settling, with flush)"""
        if not self.state.get("rx"):
            return
        total, _ = self._rx_position()
        self._rx_events.append((total, kind, value, channel))
        if flush:
            self._rx_flush()
            settle = int(self.caps.retune_settle * (self._Fs or 0)) + int(self.caps.retune_samples)
            self._rx_discard = max(self._rx_discard, total + settle)

    # --- receive hooks: a driver with its own queue overrides these (see NativeDevice); the defaults use get_samples
    def _rx_wait(self, count, timeout):
        """Wait up to `timeout` seconds for `count` samples; returns how many are ready"""
        deadline = time.monotonic() + timeout
        while True:
            x = self._get_samples()
            if len(x):
                self._rx_buf = np.concatenate((self._rx_buf, x)) if len(self._rx_buf) else x
            if len(self._rx_buf) >= count or time.monotonic() >= deadline:
                return len(self._rx_buf)
            time.sleep(0.001)
    def _rx_read(self, count):
        """(up to `count` samples, index of the first)"""
        x, idx = self._rx_buf[:count], self._rx_buf_index
        self._rx_buf = self._rx_buf[count:]
        self._rx_buf_index += len(x)
        return x, idx
    def _rx_position(self):
        """(index the next sample to arrive gets, seconds since the device last delivered or None)"""
        return self._rx_buf_index + len(self._rx_buf), None
    def _rx_flush(self):
        """Discard what is queued; returns how many"""
        x = self._get_samples()
        n = len(self._rx_buf) + len(x)
        self._rx_buf_index += n
        self._rx_buf = np.empty(0, dtype=np.complex64)
        return n

    @property
    def Fs(self):
        """get_sample_rate wrapper"""
        return self._Fs
    @Fs.setter
    def Fs(self, Fs):
        """set_sample_rate wrapper"""
        self.set_sample_rate(Fs)

    @property
    def CF(self):
        """get_freq wrapper"""
        return self._cf
    @CF.setter
    def CF(self, cf):
        """set_freq wrapper"""
        self.set_freq(cf)

    # --- Helpers/Utils --- #
    def _invalid(self, msg):
        """An out-of-range value: a warning (it is clamped), or with `strict` an error"""
        if self.strict:
            raise err.InvalidValue(msg)
        warn.InvalidValue(msg)

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
        config = type(self).CONFIG
        return self._clamp_gain(gain, direction, None,
                                getattr(config, f"GAIN_{direction}_MIN"), getattr(config, f"GAIN_{direction}_MAX"))

    def _set_gain_stage(self, direction, stage, gain, setter):
        """For devices to build their own per-stage gain setters on.

        A device exposes whatever stages its hardware has, under its own names, e.g.

            def set_rx_mixer_gain(self, gain=None):
                return self._set_gain_stage("RX", "MIXER", gain, self._set_rx_mixer_gain)

        Handles the open check, the device's default, validation against the stage's limits declared
        in its config, and then calls `setter(validated_gain)`. Returns the gain that was set.
        A stage the driver lists `values` for (uneven steps) snaps to the nearest of them.
        """
        label = f"{direction} {stage}".replace("_", " ")
        self.log.trace("set_%s_%s_gain(%s)", direction.lower(), stage.lower(), gain)
        self._ensure_open(f"set_{direction.lower()}_{stage.lower()}_gain")
        spec = self.caps.gains[direction.lower()].get(stage) or type(self).CONFIG.stage(direction, stage)
        if spec is None:
            raise NotImplementedError(f"{type(self).NAME} has no {label} gain stage")
        if gain is None:
            gain = spec.default
        gain = self._clamp_gain(gain, label, spec.step, spec.min, spec.max)
        if spec.values:
            gain = min(spec.values, key=lambda v: abs(v - gain))
        setter(gain)
        self._gain_values[direction][stage] = gain
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
    def _set_sample_rate(self, Fs):
        ...
    def _set_freq(self, freq):
        ...

    def _set_rx_gain(self, gain):
        return self._set_total_gain("rx", gain)

    def _set_tx_gain(self, gain):
        return self._set_total_gain("tx", gain)

    def _set_total_gain(self, direction, gain):
        """Set every stage of a direction: their defaults ("default"), or `split_gain`'s share of a total.
        Returns {stage (lower case): gain}"""
        d = direction.lower()
        stages = self.caps.gains[d]
        if not stages:
            raise NotImplementedError(f"{type(self).NAME} has no {d.upper()} gain stages")
        shares = {n: s.default for n, s in stages.items()} if gain == "default" else self.split_gain(gain, d)
        out = {}
        for name, value in shares.items():
            setter = getattr(self, f"_set_{d}_{name.lower()}_gain")
            out[name.lower()] = self._set_gain_stage(d.upper(), name, value, setter)
        return out

    def split_gain(self, total, direction="rx"):
        """How a total gain is shared between a direction's stages: {stage: gain}, chosen from every combination the
        stages allow. Stages in GAIN_RESERVE stay at their least unless the others can't come within their finest
        step of `total`. Then: the sum nearest `total` (not above it when there is a choice), and the stages as evenly
        loaded as possible: each the same fraction of its range, or (GAIN_BALANCE = "db") the same number of dB.
        A tie goes to the stage nearer the antenna (the first listed), which keeps the noise figure down"""
        d = direction.lower()
        stages = self.caps.gains[d]
        reserve = {n for n in type(self).GAIN_RESERVE.get(d, ()) if n in stages}
        names = list(stages)
        options = [self._stage_values(stages[n]) for n in names]
        if np.prod([len(o) for o in options], dtype=float) > 1e6:
            raise NotImplementedError(f"{type(self).NAME}: too many gain combinations to search; split it in the driver")
        by_db = type(self).GAIN_BALANCE == "db"

        def load(n, v):
            span = stages[n].max - stages[n].min
            return v - stages[n].min if by_db else ((v - stages[n].min) / span if span else 0.0)

        def best_of(combos):
            best, best_key = None, None
            for combo in combos:
                total_db = sum(combo)
                used = [load(n, v) for n, v in zip(names, combo) if n not in reserve]
                key = (round(abs(total_db - total), 9), total_db > total,
                       sum(v - stages[n].min for n, v in zip(names, combo) if n in reserve),
                       (max(used) - min(used)) if used else 0.0,
                       tuple(-v for v in combo))   # a tie: gain nearer the antenna (stages are listed in signal order)
                if best_key is None or key < best_key:
                    best, best_key = combo, key
            return best, best_key

        if reserve:   # first without the reserve stages
            held = [o if n not in reserve else (stages[n].min,) for n, o in zip(names, options)]
            combo, key = best_of(itertools.product(*held))
            steps = [self._resolution(stages[n]) for n in names if n not in reserve]
            if combo is not None and key[0] <= (min(steps) if steps else 0):
                return dict(zip(names, combo))
        combo, _ = best_of(itertools.product(*options))
        return dict(zip(names, combo))

    @classmethod
    def _resolution(cls, spec):
        """The finest change a stage can make"""
        values = sorted(set(cls._stage_values(spec)))
        gaps = [b - a for a, b in zip(values, values[1:])]
        return min(gaps) if gaps else 0

    @staticmethod
    def _stage_values(spec):
        """The gains a stage can be set to: its table, its steps, or (continuous) whole dB"""
        if spec.values:
            return tuple(spec.values)
        step = spec.step or 1
        if all(isinstance(v, int) for v in (spec.min, spec.max, step)):
            return tuple(range(spec.min, spec.max + 1, step))
        return tuple(np.arange(spec.min, spec.max + step / 2, step).tolist())

    def _set_bias_t(self, bias):
        raise NotImplementedError()

    def _capabilities(self, caps):
        """Refine `caps` (from CONFIG) for the attached unit: return it, or a new one. Called once open"""
        return caps
    def _set_gain_mode(self, direction, mode):
        raise err.NotSupported(f"{type(self).NAME} has no gain modes")
    def _set_bandwidth(self, direction, bandwidth):
        """Set the analog filter; return the bandwidth the hardware chose (None: the one asked for)"""
        raise err.NotSupported(f"{type(self).NAME} has no adjustable filter")
    def _set_antenna(self, direction, name):
        raise err.NotSupported(f"{type(self).NAME} has no antenna selection")
    def _set_clock_source(self, name):
        raise err.NotSupported(f"{type(self).NAME} has no clock source selection")
