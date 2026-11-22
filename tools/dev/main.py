import pysdrlib

def dynamic_import():
    print(pysdrlib.devices.ls())
    sdr = pysdrlib.device("rtl_sdr")
    # sdr = pysdrlib.devices.get("rtl_sdr").Device()
    print(sdr.NAME)

def static_import():
    from pysdrlib import rtl_sdr
    sdr = rtl_sdr.Device()
    print(sdr.NAME)

if __name__ == "__main__":
    static_import()
