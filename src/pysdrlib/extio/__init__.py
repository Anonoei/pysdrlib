from .._ext import extio as ext, utils

RX888 = utils.import_device(__package__, ".rx888", "RX888", ext.LIB)
Device = RX888
