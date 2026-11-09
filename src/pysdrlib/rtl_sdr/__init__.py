from .._ext import rtl_sdr as ext, utils

RTLSDR = utils.import_device(__package__, ".rtlsdr", "RTLSDR", ext.LIB)
Device = RTLSDR

def find():
    """[DeviceInfo] of the RTL-SDR dongles attached"""
    from ..devices import DeviceInfo
    from . import lib
    found = []
    units = [lib.rtl_sdr.device_info(i) for i in range(lib.rtl_sdr.device_count())]
    serials = [u[3] for u in units]
    for i, (name, manufacturer, product, serial) in enumerate(units):
        # Many dongles share a serial (00000001): those are opened by their position instead
        args = {"serial": serial} if serial and serials.count(serial) == 1 else {"index": i}
        found.append(DeviceInfo("rtl_sdr", name or f"{manufacturer} {product}".strip(), serial or None, i, args,
                                {"manufacturer": manufacturer, "product": product}))
    return found
