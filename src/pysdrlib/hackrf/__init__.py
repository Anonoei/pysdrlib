from .._ext import hackrf as ext, utils

HackRF = utils.import_device(__package__, ".hackrf", "HackRF", ext.LIB)
Device = HackRF

_BOARDS = {0x604B: "HackRF Jawbreaker", 0x6089: "HackRF One", 0xCC15: "rad1o"}

def find():
    """[DeviceInfo] of the HackRFs attached"""
    from ..devices import DeviceInfo
    from . import lib
    found = []
    devices = lib.hackrf.device_list()
    try:
        for i, (serial, board) in enumerate(zip(devices.serial_numbers, devices.usb_board_ids)):
            found.append(DeviceInfo("hackrf", _BOARDS.get(board, f"HackRF (USB id {board:#06x})"), serial, i,
                                    {"serial": serial}, {"usb_board_id": board}))
    finally:
        devices.close()
    return found
