import ctypes
import time
import threading

from . import config
from . import func
from . import err
from .obj import TRANSCEIVER_MODE, RF_PATH_FILTER
from .obj import hackrf_transfer_t, SAMPLE_CB_FN

def rx_callback(transfer: hackrf_transfer_t):
    print("Starting rx_callback!")
    log = ctypes.cast(transfer.contents.rx_ctx, ctypes.py_object).value
    log.info("Hello world!")
    bytes_to_write = transfer.valid_length
    print(bytes_to_write)
    print(transfer.buffer)
    return ctypes.c_int(0)

def hackrf_transfer(serial_number=None):
    mode = TRANSCEIVER_MODE["RX"]
    crystal_correct_ppm = 0
    image_reject_selection = RF_PATH_FILTER["BYPASS"]
    hw_sync = True

    amp = False
    antenna = False
    lna_gain = 8
    vga_gain = 20
    txvga_gain = 0

    CF = config.DEFAULT_FREQ
    Fs = config.DEFAULT_SAMPLE_RATE

    baseband_filter_bw = int(Fs * 0.75)
    baseband_filter_bw = func.compute_baseband_filter_bw(baseband_filter_bw)

    print(f"Using CF: {CF:,}")
    print(f"Using Fs: {Fs:,}")
    print(f"Using BB filter BW: {baseband_filter_bw:,}")

    if crystal_correct_ppm:
        Fs = (Fs * (1_000_000 - crystal_correct_ppm) / 1_000_000 + 0.5)
        CF = CF * (1_000_000 - crystal_correct_ppm) / 1_000_000

    func.init()
    if serial_number is None:
        try:
            device = func.open()
        except err.NOT_FOUND:
            print("Failed to find a HackRF!")
            exit()
    else:
        # list = func.device_list()
        # if list["devicecount"] > 0:
        #     serial_number = list['serial_numbers'][0].decode()
        # print(f"Using device {serial_number}")
        device = func.open_by_serial(serial_number)


    func.set_sample_rate(device, Fs)
    func.set_baseband_filter(device, baseband_filter_bw)
    func.set_hw_sync(device, hw_sync)

    func.set_freq(device, CF)

    if amp:
        func.set_amp_enable(device, amp)
    if antenna:
        func.set_antenna_enable(device, antenna)

    if mode is TRANSCEIVER_MODE.RX:
        func.set_vga_gain(device, vga_gain)
        func.set_lna_gain(device, lna_gain)
        cb = func.start_rx(device, rx_callback, None)
    # else:
    #     func.set_tx_gain(device, txvga_gain)
    #     func.enable_tx_flush(device, flush_callback, None)
    #     func.set_tx_block_complete_callback(device, tx_complete_callback)
    #     func.start_tx(device, tx_callback, None)

    print("Sleeping...")
    time.sleep(2)
    # print(device.contents.json())
    print(f"is_streaming: {func.is_streaming(device)}")
    print(f"Threads: {threading.enumerate()}")
    print(f"Callback is {cb}")
    time.sleep(1)
    if mode is TRANSCEIVER_MODE.RX:
        func.stop_rx(device)

    time.sleep(0.1)

    func.close(device)
    func.exit()
