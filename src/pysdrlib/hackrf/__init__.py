from .._ext import hackrf as ext, utils

HackRF = utils.import_device(__package__, ".hackrf", "HackRF", ext.LIB)
Device = HackRF
