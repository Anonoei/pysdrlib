"""A simulated radio that needs no hardware: `pysdrlib.open("sim")`, listed by `pysdrlib.find(virtual=True)`.

It runs pysdrlib's real streaming path (the native queue, the stall watchdog, drop reporting, `read`) over a compiled
device with no hardware behind it, so it stands in for a radio in examples, tests and benchmarks. The signal is a tone by
default, or tones, noise and QPSK bursts built to order; it can have several channels and accept transmit, and it can
drop, stall or disconnect on demand. `Sim`'s arguments (`pysdrlib.open("sim", channels=2, noise=-60, ...)`) are the
reference.
"""
from .sim import Sim
Device = Sim

def find():
    """The simulated device (listed by pysdrlib.find(virtual=True))"""
    from ..devices import DeviceInfo
    return [DeviceInfo("sim", "Simulated", None, 0, {}, {"virtual": True})]
