# Hack RF

- How-to [pysdr HackRF](https://pysdr.org/content/hackrf.html)
- Stand-alone implementation: [python_hackrf](https://github.com/GvozdevLeonid/python_hackrf)
- Specs [hackrf.readthedocs](https://hackrf.readthedocs.io/en/latest/hackrf_one.html)
- Gain control [hackrf.readthedocs](https://hackrf.readthedocs.io/en/latest/setting_gain.html)
- Minimum sample rate [hackrf.readthedocs](https://hackrf.readthedocs.io/en/latest/sampling_rate.html)

## Specs
- Frequency (1Mhz - 6 GHz)
- Sample rate (2 MHz - 20 Mhz)
- Samples (complex int 8)
- Amplifiers
  - Receive
    - RF [amp] (0 or 11, default=0)
    - IF [lna] (0-40:8, default=30)
    - baseband [vga] (0-62:2, default=50)
  - Transmit
    - RF (0 or 11, default=11)
    - IF (0-47:1, adjust)
