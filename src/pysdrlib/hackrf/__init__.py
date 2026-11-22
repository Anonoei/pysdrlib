"""HackRF One through libhackrf: receive and transmit, half-duplex.

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
    - RF [amp] (0 or 11, default=0)
    - IF [txvga] (0-47:1, default=0)

## Transmit
HackRF is half-duplex: it receives or transmits, never both. `start_tx` raises `err.Busy` while receiving (and
`start_rx` while transmitting). Samples are complex64 in [-1, 1]; the transmit callback converts to int8. If `write`
falls behind, silence is sent and counted in `tx_stats["underruns"]`.

```python
sdr.initialize(cf=915e6, Fs=2e6, tx_gain=0)   # raise the TX gain gradually, into an antenna or load you intend
sdr.start_tx()
sdr.write(samples)        # complex64; blocks while the 0.25 s queue is full
sdr.stop_tx()
```
"""
from .._ext import hackrf as ext, utils

HackRF = utils.import_device(__name__, ".hackrf", "HackRF", ext.LIB)
Device = HackRF

_BOARDS = {0x604B: "HackRF Jawbreaker", 0x6089: "HackRF One", 0xCC15: "rad1o"}

def find():
    """[DeviceInfo] of the HackRFs attached"""
    from ..devices import DeviceInfo
    from . import lib
    found = []
    devices = lib.hackrf.device_list()
    try:
        for i, (serial, board) in enumerate(zip(devices.serial_numbers, devices.usb_board_ids)):
            found.append(DeviceInfo("hackrf", _BOARDS.get(board, f"HackRF (USB id {board:#06x})"), serial, i,
                                    {"serial": serial}, {"usb_board_id": board}))
    finally:
        devices.close()
    return found
