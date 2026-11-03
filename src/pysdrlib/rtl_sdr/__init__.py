from .._ext import rtl_sdr as ext, utils

RTLSDR = utils.import_device(__package__, ".rtlsdr", "RTLSDR", ext.LIB)
Device = RTLSDR
