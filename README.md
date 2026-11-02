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

### Remote devices
Host SDRs on one machine and use them from another (`pip install pysdrlib[remote]`, uses ZeroMQ).

```
pysdrlib server --bind tcp://0.0.0.0:5555 --port-range 6000-6100
pysdrlib client tcp://host:5555 list
pysdrlib client tcp://host:5555 rx hackrf --freq 100e6 --rate 10e6 -t 5 -o out.cfile
```
```python
sdr = pysdrlib.device("remote", "tcp://host:5555", "hackrf")  # same API as a local device
sdr.open(); sdr.initialize(cf=100e6, Fs=10e6); sdr.start_rx()
samples = sdr.get_samples()
```
One client controls a device; any number can attach as viewers (`pysdrlib.device("remote", addr, id="hackrf-0")`) to receive samples.

# Contributing
1. `git clone https://github.com/Anonoei/pysdrlib`
2. `cd pysdrlib`
3. `git branch -c feature/<your feature>`
4. `python3 builder.py -b -l` build and install locally
