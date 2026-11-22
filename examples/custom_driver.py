"""A driver written outside pysdrlib: a Device subclass registered under a name, after which find(), open() and a
`pysdrlib server` know it like a shipped driver. A package would declare it as an entry point instead (see the
*Drivers of your own* guide); `python -m pysdrlib.scaffold my_radio --out-of-tree` writes one."""
import numpy as np

import pysdrlib
from pysdrlib import Capabilities, Range, testing
from pysdrlib.base.caps import GainStage

# --8<-- [start:driver]
@pysdrlib.register("noise_source")
class NoiseSource(pysdrlib.Device):
    """White noise at any frequency: the smallest receive-only Device"""
    NAME = "Noise source"
    CAPS = Capabilities(driver="", freq=[Range(0, 6e9)], default_freq=100e6, rates=[Range(1e3, 10e6)],
                        default_rate=1e6, gains={"rx": {"LEVEL": GainStage(1, 0, 40, 20)}, "tx": {}})

    def _open(self, level=0.1):          # pysdrlib.open("noise_source", level=...) arguments arrive here
        self.level = level
        self._rng = np.random.default_rng()

    def _close(self):
        pass

    def _set_rx_freq(self, freq, channel=0):
        pass                             # a radio would tune here; these hooks get values already checked against CAPS
    def _set_rx_sample_rate(self, Fs, channel=0):
        pass
    def _set_rx_level_gain(self, gain, channel=0):   # one per stage: set_rx_level_gain is made from CAPS
        self.gain = gain

    def _start_rx(self):
        pass
    def _stop_rx(self):
        pass
    def _get_samples(self):              # whatever has "arrived": read() builds exact counts on it
        n = 4096
        x = self._rng.standard_normal(n) + 1j * self._rng.standard_normal(n)
        return (self.level * 10 ** ((self.gain - 40) / 20) * x).astype(np.complex64)
# --8<-- [end:driver]

# --8<-- [start:use]
print(testing.check_driver(NoiseSource))      # what's missing or wrong, before a radio is involved
sdr = pysdrlib.open("noise_source", level=0.5)
sdr.initialize(cf=433.92e6, Fs=1e6)
sdr.start_rx()
x = sdr.read(10_000)
sdr.close()
# --8<-- [end:use]
print(x.dtype, x.shape)
