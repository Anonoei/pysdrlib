from .._ext import uhd as ext, utils

B210 = utils.import_device(__name__, ".uhd", "B210", ext.LIB)
Device = B210

__pdoc__ = {"vendor": False}  # vendored third-party sources are not API

def find():
    """[DeviceInfo] of the USRPs attached (USB B2xx)"""
    from ..devices import DeviceInfo
    from . import images, lib
    images.configure()
    found = []
    for i, d in enumerate(lib.uhd.find()):
        product = d.get("product") or d.get("type") or "USRP"
        serial = d.get("serial") or None
        label = " ".join(x for x in (f"UHD {d.get('name') or product}".strip(), serial) if x)
        found.append(DeviceInfo("uhd", label, serial, i, {"serial": serial} if serial else {}, dict(d)))
    return found
