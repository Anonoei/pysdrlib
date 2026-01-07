import time
from enum import Enum

import numpy as np

from ..lib import hackrf

callback_samples = np.zeros([], dtype=np.complex64)
callback_last_idx = 0

class TRANSCEIVER_MODE(Enum):
    OFF = 0
    RX = 1
    TX = 2
    SS = 3

    Rx = RX
    Tx = TX

    RECEIVE = RX
    TRANSMIT = TX
    SIGNALSOURCE = SS

def rx_callback(device, buffer, buffer_length, valid_length):
    global callback_samples
    global callback_last_idx
    accepted = valid_length // 2
    accepted_samples = buffer[:valid_length].astype(np.int8) # -128 to 127
    accepted_samples = accepted_samples[0::2] + 1j * accepted_samples[1::2]  # Convert to complex type (de-interleave the IQ)
    accepted_samples /= 128 # -1 to +1

    callback_samples[callback_last_idx:callback_last_idx+accepted] = accepted_samples
    callback_last_idx += accepted
    return 0

def hackrf_transfer(serial_number=None):
    mode = TRANSCEIVER_MODE.RX
    # image_reject_selection = None
    hw_sync = False

    lna_gain = 8
    vga_gain = 20
    txvga_gain = 0

    amp = False
    antenna = False
    if mode is TRANSCEIVER_MODE.RX:
        lna_gain = 16 # 8
        vga_gain = 16 # 20
    elif mode is TRANSCEIVER_MODE.TX:
        txvga_gain = 0

    CF = 900_000_000
    Fs =  10_000_000

    hackrf.init()
    if serial_number is None:
        try:
            dev = hackrf.open()
        except hackrf.err.NOT_FOUND:
            print("No HackRF devices found!")
            return
    else:
        dev = hackrf.open_by_serial(serial_number)

    dev.set_sample_rate(Fs)
    dev.set_hw_sync_mode(hw_sync)
    dev.set_freq(CF)

    if amp:
        dev.set_amp_enabled(amp)
    if antenna:
        dev.set_antenna_enable(antenna)


    global callback_samples
    callback_samples = np.zeros(1*Fs, dtype=np.complex64)

    if mode is TRANSCEIVER_MODE.RX:
        print(f"Setting rx; vga: {vga_gain}, lna: {lna_gain}")
        dev.set_rx_callback(rx_callback)
        dev.set_vga_gain(vga_gain)
        dev.set_lna_gain(lna_gain)
        dev.start_rx()

    print("Sleeping...")
    time.sleep(0.5)
    print(f"is_streaming: {dev.is_streaming()}: {callback_samples}")
    time.sleep(0.5)

    if mode is TRANSCEIVER_MODE.RX:
        dev.stop_rx()

    dev.close()
    hackrf.exit()
