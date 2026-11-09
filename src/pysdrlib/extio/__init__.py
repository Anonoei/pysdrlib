from .._ext import extio as ext, utils

RX888 = utils.import_device(__package__, ".rx888", "RX888", ext.LIB)
Device = RX888

__pdoc__ = {"vendor": False}  # vendored third-party sources are not API

def find():
    """[DeviceInfo] of the RX-888s attached. The driver has no serial numbers and opens the first one it finds"""
    from ..devices import DeviceInfo
    from . import lib
    return [DeviceInfo("extio", "RX-888", None, i, {}, {"openable": i == 0}) for i in range(lib.extio.device_count())]
