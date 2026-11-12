from .._ext import bladerf as ext, utils

__doc__ = utils.readme(__file__)

BladeRF = utils.import_device(__name__, ".bladerf", "BladeRF", ext.LIB)
Device = BladeRF

__pdoc__ = {"vendor": False}  # vendored third-party sources are not API

def find():
    """[DeviceInfo] of the bladeRFs attached"""
    from ..devices import DeviceInfo
    from . import lib
    found = []
    for i, d in enumerate(lib.bladerf.find()):
        serial = d.get("serial") or None
        label = " ".join(x for x in (d.get("product") or "bladeRF", serial) if x)
        found.append(DeviceInfo("bladerf", label, serial, i, {"serial": serial} if serial else {}, dict(d)))
    return found
