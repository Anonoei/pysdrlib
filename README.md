# pysdrlib
 Python wrappers for hardware SDRs

 - [Documentation](https://anonoei.github.io/pysdrlib/)
 - [PyPI](https://pypi.org/project/pysdrlib/)

## Notes
- No affiliation to the fantastic [pysdr](https://pysdr.org) page
- For a more robust C implementation, check [SoapySDR](https://github.com/pothosware/SoapySDR)

This serves as a lightweight backend for my [pyspecan](https://github.com/Anonoei/pyspecan) and [RPPS](https://github.com/Anonoei/RPPS) projects,
with minimal user intervention.
Other projects, like SoapySDR, are likely what you're looking for.

# Roadmap
- [ ] [RTL-SDR](https://github.com/Anonoei/pysdrlib/tree/main/src/pysdrlib/rtl_sdr)
- [X] [Hack RF](https://github.com/Anonoei/pysdrlib/tree/main/src/pysdrlib/hackrf)
- [ ] [Blade RF](https://github.com/Anonoei/pysdrlib/tree/main/src/pysdrlib/bladerf)
- [ ] [B210 (UHD)](https://github.com/Anonoei/pysdrlib/tree/main/src/pysdrlib/UHD)
- [ ] [RX-888 (extio)](https://github.com/Anonoei/pysdrlib/tree/main/src/pysdrlib/extio)

## Usage
Dynamic imports
```
import pysdrlib
print(pysdrlib.devices.ls()) # Print available device interfaces
sdr = pysdrlib.devices.get("rtl_sdr").Device() # Initialize sdr
sdr.open()
```
Static imports
```
import pysdrlib.rtl_sdr
sdr = pysdrlib.rtl_sdr.Device() # Initialize sdr
```

# Contributing
1. `git clone https://github.com/Anonoei/pysdrlib`
2. `cd pysdrlib`
3. `git branch -c feature/<your feature>`
4. `python3 builder.py -b -l` build and install locally
