import ctypes
import weakref

from . import config
from .config import log
from . import err
from .obj import hackrf_device, read_partid_serialno
from .obj import SAMPLE_CB_FN

def _check_error(result):
    if result != 0:
        if result == -2: raise err.INVALID_PARAM()
        if result == -5: raise err.NOT_FOUND()
        if result == -6: raise err.BUSY()
        if result == -11: raise err.NO_MEM()
        if result == -1000: raise err.LIBUSB()
        if result == -1001: raise err.THREAD()
        if result == -1002: raise err.STREAMING_THREAD_ERR()
        if result == -1003: raise err.STREAMING_STOPPED()
        if result == -1004: raise err.STREAMING_EXIT_CALLED()
        if result == -1005: raise err.USB_API_VERSION()
        if result == -2000: raise err.NOT_LAST_DEVICE()
        if result == -9999: raise err.OTHER()
    return result

def init():
    log.trace("Calling hackrf_init()")
    _check_error(config.LIB.hackrf_init())

def exit():
    log.trace("Calling hackrf_exit()")
    _check_error(config.LIB.hackrf_exit())

def close(device):
    log.trace("Calling hackrf_close() on device")
    _check_error(config.LIB.hackrf_close(device))


def device_list():
    pointer = config.LIB.hackrf_device_list()
    struct = pointer.contents
    board_ids = struct.usb_board_ids.contents
    board_ids = config.LIB.hackrf_usb_board_id_name(board_ids)
    return {
        'ptr': pointer,
        'serial_numbers': struct.serial_numbers,
        'usb_board_ids': board_ids,
        'usb_device_index': struct.usb_device_index.contents,
        'devicecount': struct.devicecount,
        'usb_devices': struct.usb_devices,
        'usb_devicecount': struct.usb_devicecount
    }

def device_list_open(list, index):
    device = ctypes.POINTER(hackrf_device)()
    result = config.LIB.hackrf_device_list_open(list, index, device)
    _check_error(result)
    return device

def open():
    device = ctypes.POINTER(hackrf_device)()
    _check_error(config.LIB.hackrf_open(ctypes.byref(device)))
    return device

def open_by_serial(serial_number):
    serial_number = ctypes.c_char_p(serial_number.encode())
    device = ctypes.POINTER(hackrf_device)()
    _check_error(config.LIB.hackrf_open_by_serial(serial_number, ctypes.byref(device)))
    return device

def board_id_read(device):
    board_id = ctypes.c_int(0)
    result = config.LIB.hackrf_board_id_read(device, ctypes.byref(board_id))
    _check_error(result)
    return board_id.value

def get_version_string(device):
    # version = ctypes.c_char_p()
    version = ctypes.create_string_buffer(255)
    result = config.LIB.hackrf_version_string_read(device, ctypes.byref(version, 0), 255)
    _check_error(result)
    return version.value.decode()

def usb_api_version_read(device):
    usb_version = ctypes.c_uint16()
    result = config.LIB.hackrf_usb_api_version_read(device, ctypes.byref(usb_version))
    _check_error(result)
    return usb_version.value

def board_pardid_serialno_read(device):
    partid_serialno = read_partid_serialno()
    result = config.LIB.hackrf_board_partid_serialno_read(device, ctypes.byref(partid_serialno))
    _check_error(result)
    return partid_serialno

def device_list_free(list):
    result = config.LIB.hackrf_device_list_free(list)
    _check_error(result)

def get_supported_platform(device):
    platform = ctypes.c_uint32(0)
    config.LIB.hackrf_supported_platform_read(device, ctypes.byref(platform))
    return platform.value

def library_release():
    return config.LIB.hackrf_library_release().decode()

def library_version():
    return config.LIB.hackrf_library_version().decode()

def compute_baseband_filter_bw(bw):
    bw = ctypes.c_uint32(bw)
    bw = config.LIB.hackrf_compute_baseband_filter_bw(bw)
    return bw

def set_freq(device, freq):
    if freq < config.FREQ_MIN:
        print(f"frequency must be greater than or equal to {config.FREQ_MIN:,} ({config.FREQ_MIN_ABS:,})!")
    elif freq > config.FREQ_MAX:
        print(f"frequency must be less than or equal to {config.FREQ_MAX:,} ({config.FREQ_MAX_ABS:,})!")
    log.trace(f"Calling hackrf_set_freq({freq:,})")
    freq = ctypes.c_uint64(freq)
    result = config.LIB.hackrf_set_freq(device, freq)
    _check_error(result)

def set_freq_explicit(device, freq_if, freq_lo, filter_path):
    if freq_if < config.IF_MIN:
        print(f"if frequency must be greater than or equal to {config.IF_MIN:,} ({config.IF_MIN_ABS:,})!")
    elif freq_if > config.IF_MAX:
        print(f"if frequency must be less than or equal to {config.IF_MAX:,} ({config.IF_MAX_ABS:,})!")
    if freq_lo < config.LO_MIN:
        print(f"lo frequency must be greater than or equal to {config.LO_MIN:,}!")
    elif freq_lo > config.LO_MAX:
        print(f"lo frequency must be less than or equal to {config.LO_MAX:,}!")
    log.trace(f"Calling hackrf_set_freq_explicit(if: {freq_if:,} lo: {freq_lo:,}, path: {filter_path})")
    freq_if = ctypes.c_uint64(freq_if)
    freq_lo = ctypes.c_uint64(freq_lo)
    filter_path = ctypes.c_uint8(filter_path)
    result = config.LIB.hackrf_set_freq_explicit(device, freq_if, freq_lo, filter_path)
    _check_error(result)

def set_sample_rate(device, Fs):
    if Fs < config.SAMPLE_RATE_MIN:
        print(f"Sample rate must be greater than or equal to {config.SAMPLE_RATE_MIN:,}!")
    elif Fs > config.SAMPLE_RATE_MAX:
        print(f"Sample rate must be less than or equal to {config.SAMPLE_RATE_MAX:,}!")
    log.trace(f"Calling hackrf_set_sample_rate({Fs:,})")
    Fs = ctypes.c_double(Fs)
    result = config.LIB.hackrf_set_sample_rate(device, Fs)
    _check_error(result)

def set_baseband_filter(device, width):
    if not width in config.BASEBAND_FILTERS:
        print(f"baseband_filter width must be one of: {config.BASEBAND_FILTERS}")
    log.trace(f"Calling hackrf_set_baseband_filter_bandwidth({width:,})")
    width = ctypes.c_uint32(width)
    _check_error(config.LIB.hackrf_set_baseband_filter_bandwidth(device, width))

def set_hw_sync(device, enable):
    enable = bool(enable)
    log.trace(f"Calling hackrf_set_hw_sync_mode({enable})")
    enable = ctypes.c_uint8(enable)
    _check_error(config.LIB.hackrf_set_hw_sync_mode(device, enable))

def set_lna_gain(device, gain):
    if gain % config.RX_LNA_GAIN_STEP:
        print(f"lna_gain must be a multiple of {config.RX_LNA_GAIN_STEP}!")
    if gain < config.RX_LNA_GAIN_MIN:
        print(f"lna_gain must be greater than or equal to {config.RX_LNA_GAIN_MIN}!")
    elif gain > config.RX_LNA_GAIN_MAX:
        print(f"lna_gain must be less than or equal to {config.RX_LNA_GAIN_MAX}!")
    log.trace(f"Calling hackrf_set_lna_gain({gain})")
    gain = ctypes.c_uint32(gain)
    _check_error(config.LIB.hackrf_set_lna_gain(device, gain))

def set_vga_gain(device, gain):
    if gain % config.RX_VGA_GAIN_STEP:
        print(f"vga_gain must be a multiple of {config.RX_VGA_GAIN_STEP}!")
    if gain < config.RX_VGA_GAIN_MIN:
        print(f"vga_gain must be greater than or equal to {config.RX_VGA_GAIN_MIN}!")
    elif gain > config.RX_VGA_GAIN_MAX:
        print(f"vga_gain must be less than or equal to {config.RX_VGA_GAIN_MAX}!")
    log.trace(f"Calling hackrf_set_vga_gain({gain})")
    gain = ctypes.c_uint32(gain)
    _check_error(config.LIB.hackrf_set_vga_gain(device, gain))

def set_txvga_gain(device, gain):
    if gain % config.TX_VGA_GAIN_STEP:
        print(f"txvga_gain must be a multiple of {config.TX_VGA_GAIN_STEP}!")
    if gain < config.TX_VGA_GAIN_MIN:
        print(f"txvga_gain must be greater than or equal to {config.TX_VGA_GAIN_MIN}!")
    elif gain > config.TX_VGA_GAIN_MAX:
        print(f"txvga_gain must be less than or equal to {config.TX_VGA_GAIN_MAX}!")
    log.trace(f"Calling hackrf_set_txvga_gain({gain})")
    gain = ctypes.c_uint32(gain)
    _check_error(config.LIB.hackrf_set_txvga_gain(device, gain))

def set_amp_enable(device, enable):
    enable = bool(enable)
    log.trace(f"Calling hackrf_set_amp_enable({enable})")
    enable = ctypes.c_uint8(enable)
    _check_error(config.LIB.hackrf_set_amp_enable(device, enable))

def set_antenna_enable(device, enable):
    enable = bool(enable)
    log.trace(f"Calling hckrf_set_antenna_enable({enable})")
    enable = ctypes.c_uint8(enable)
    _check_error(config.LIB.hackrf_set_antenna_enable(device, enable))

def start_rx(device, callback_fn, rx_ctx=None):
    log.trace(f"Calling hackrf_start_rx({callback_fn})")
    cb = SAMPLE_CB_FN(callback_fn)
    log.trace(f"Sending callback: {cb}")
    if not rx_ctx is None:
        rx_ctx = ctypes.c_void_p(rx_ctx)
    _check_error(config.LIB.hackrf_start_rx(device, ctypes.byref(cb), rx_ctx))
    print(f"Callback is {cb} --> {callback_fn}")
    return cb

def stop_rx(device):
    _check_error(config.LIB.hackrf_stop_rx(device))

def start_tx(device, callback, tx_ctx):
    _check_error(config.LIB.hackrf_start_tx(device, callback, tx_ctx))

def stop_tx(device):
    _check_error(config.LIB.hackrf_stop_tx(device))

def stop_cmd(device):
    _check_error(config.LIB.hackrf_stop_cmd(device))

def is_streaming(device):
    result = config.LIB.hackrf_is_streaming(device)
    if result == 0:
        return False
    elif result == 1:
        return True
    _check_error(result)
