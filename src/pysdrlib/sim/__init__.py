from .sim import Sim
Device = Sim

def find():
    """The simulated device (listed by pysdrlib.find(virtual=True))"""
    from ..devices import DeviceInfo
    return [DeviceInfo("sim", "Simulated", None, 0, {}, {"virtual": True})]
