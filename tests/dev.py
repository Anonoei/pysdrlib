import pysdrlib

def dynamic_import():
    print(pysdrlib.devices.ls())
    sdr = pysdrlib.devices.get("rtl_sdr").Device()
    print(sdr.NAME)

def static_import():
    from pysdrlib import rtl_sdr
    sdr = rtl_sdr.Device()
    print(sdr.NAME)

def setup_hackrf():
    from pysdrlib import hackrf
    hackrf.lib_init()

if __name__ == "__main__":
    setup_hackrf()
