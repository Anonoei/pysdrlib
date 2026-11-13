from .._ext import uhd as ext, utils

__doc__ = utils.readme(__file__)

B210 = utils.import_device(__name__, ".uhd", "B210", ext.LIB)
X310 = utils.import_device(__name__, ".uhd", "X310", ext.LIB)
X410 = utils.import_device(__name__, ".uhd", "X410", ext.LIB)
Device = B210

__pdoc__ = {"vendor": False}  # vendored third-party sources are not API

def _driver_of(address: dict):
    """The driver a UHD device address (its type, product...) is listed under, or None for a radio pysdrlib has no class for"""
    kind = (address.get("type") or "").lower()
    product = (address.get("product") or "").lower()
    if kind == "b200" or product.startswith("b2"):
        return "uhd"
    if kind == "x300" or product.startswith(("x300", "x310")):
        return "x310"
    if kind == "x4xx" or product.startswith("x4"):
        return "x410"
    return None

def find(addresses=None):
    """[DeviceInfo] of the USRPs attached: B2xx on USB, always; X3xx and X4xx on a network only at `addresses` (a host,
    or a list of them), as UHD finds them by asking that address"""
    from ..devices import DeviceInfo
    from . import images, lib
    images.configure()
    if isinstance(addresses, str):
        addresses = [addresses]
    queries = ["type=b200"] + [f"addr={a}" for a in addresses or ()]   # not UHD's own search: it broadcasts on every network
    found = []
    for query in queries:
        for d in lib.uhd.find(query):
            driver = _driver_of(d)
            if driver is None:
                continue
            product = d.get("product") or d.get("type") or "USRP"
            serial = d.get("serial") or None
            open_args = {k: v for k, v in (("serial", serial), ("addr", d.get("addr"))) if v}
            label = " ".join(x for x in (f"UHD {d.get('name') or product}".strip(), serial) if x)
            info = DeviceInfo(driver, label, serial, len(found), open_args, dict(d))
            if info not in found:
                found.append(info)
    return found
