import pysdrlib

def dynamic_import():
    print(pysdrlib.devices.ls())
    sdr = pysdrlib.devices.get("rtl_sdr").Device()
    print(sdr.NAME)

def static_import():
    import pysdrlib.rtl_sdr
    sdr = pysdrlib.rtl_sdr.Device()
    print(sdr.NAME)

def setup_hackrf():
    from pysdrlib import hackrf
    libhackrf = hackrf.lib_init()

    libhackrf.hackrf_info()

if __name__ == "__main__":
    setup_hackrf()
