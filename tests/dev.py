import pysdrlib

def dynamic_import():
    print(pysdrlib.devices.ls())
    sdr = pysdrlib.devices.get("rtl_sdr").Device()
    print(sdr.NAME)

def static_import():
    import pysdrlib.rtl_sdr
    sdr = pysdrlib.rtl_sdr.Device()
    print(sdr.NAME)

if __name__ == "__main__":
    dynamic_import()
