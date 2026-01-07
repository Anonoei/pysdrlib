from enum import Enum
import numpy as np

cimport cython
from libc.stdint cimport uint8_t, uint16_t, uint32_t, uintptr_t
from libc.stdlib cimport malloc, free
from libc.string cimport memcpy

from . cimport chackrf
from . import err

cdef dict callbacks = {}

class ERR(Enum):
    SUCCESS = chackrf.hackrf_error.HACKRF_SUCCESS
    TRUE = chackrf.hackrf_error.HACKRF_TRUE
    INVALID_PARAM = chackrf.hackrf_error.HACKRF_ERROR_INVALID_PARAM
    NOT_FOUND = chackrf.hackrf_error.HACKRF_ERROR_NOT_FOUND
    BUSY = chackrf.hackrf_error.HACKRF_ERROR_BUSY
    NO_MEM = chackrf.hackrf_error.HACKRF_ERROR_NO_MEM
    LIBUSB = chackrf.hackrf_error.HACKRF_ERROR_LIBUSB
    THREAD = chackrf.hackrf_error.HACKRF_ERROR_THREAD
    STREAMING_THREAD_ERR = chackrf.hackrf_error.HACKRF_ERROR_STREAMING_THREAD_ERR
    STREAMING_STOPPED = chackrf.hackrf_error.HACKRF_ERROR_STREAMING_STOPPED
    STREAMING_EXIT_CALLED = chackrf.hackrf_error.HACKRF_ERROR_STREAMING_EXIT_CALLED
    USB_API_VERSION = chackrf.hackrf_error.HACKRF_ERROR_USB_API_VERSION
    NOT_LAST_DEVICE = chackrf.hackrf_error.HACKRF_ERROR_NOT_LAST_DEVICE
    OTHER = chackrf.hackrf_error.HACKRF_ERROR_OTHER

class RF_PATH_FILTER(Enum):
    BYPASS = chackrf.rf_path_filter.RF_PATH_FILTER_BYPASS
    LOW_PASS = chackrf.rf_path_filter.RF_PATH_FILTER_LOW_PASS
    HIGH_PASS = chackrf.rf_path_filter.RF_PATH_FILTER_HIGH_PASS

cdef _check_err(result):
    if result < 0:
        if result == ERR.INVALID_PARAM.value: raise err.INVALID_PARAM()
        if result == ERR.NOT_FOUND.value: raise err.NOT_FOUND()
        if result == ERR.BUSY.value: raise err.BUSY()
        if result == ERR.NO_MEM.value: raise err.NO_MEM()
        if result == ERR.LIBUSB.value: raise err.LIBUSB()
        if result == ERR.THREAD.value: raise err.THREAD()
        if result == ERR.STREAMING_THREAD_ERR.value: raise err.STREAMING_THREAD_ERR()
        if result == ERR.STREAMING_STOPPED.value: raise err.STREAMING_STOPPED()
        if result == ERR.STREAMING_EXIT_CALLED.value: raise err.STREAMING_EXIT_CALLED()
        if result == ERR.USB_API_VERSION.value: raise err.USB_API_VERSION()
        if result == ERR.NOT_LAST_DEVICE.value: raise err.NOT_LAST_DEVICE()
        if result == ERR.OTHER.value: raise err.OTHER()

@cython.boundscheck(False)
@cython.wraparound(False)
cdef int _rx_callback(chackrf.hackrf_transfer* transfer) noexcept nogil:
    cdef uint8_t* buffer_ptr = transfer.buffer
    cdef uint8_t* np_buffer_ptr
    cdef int result = -1

    with gil:
        np_buffer = np.empty(transfer.buffer_length, dtype=np.int8)
        np_buffer_ptr = <uint8_t*> <uintptr_t> np_buffer.ctypes.data

        memcpy(
            np_buffer_ptr,
            buffer_ptr,
            transfer.valid_length
        )

        if callbacks[<size_t> transfer.device]["rx_callback"] is not None:
            result = callbacks[<size_t> transfer.device]["rx_callback"](np_buffer, transfer.valid_length, transfer.buffer_length)
    return result

cdef class device_list:
    cdef chackrf.hackrf_device_list_t* _hackrf_device_list

    def __cinit__(self):
        self._hackrf_device_list = chackrf.hackrf_device_list()
    def __dealloc__(self):
        self.close()

    def close(self):
        if self._hackrf_device_list is not NULL:
            chackrf.hackrf_device_list_free(self._hackrf_device_list)
            self._hackrf_device_list = <chackrf.hackrf_device_list_t*>NULL
    cdef chackrf.hackrf_device_list_t* ptr(self):
        return self._hackrf_device_list

    def __len__(self):
        return self.device_count

    @property
    def serial_numbers(self):
        return [self._hackrf_device_list[0].serial_numbers[i].decode("utf8") for i in range(self.device_count)]

    @property
    def usb_board_ids(self):
        return [self._hackrf_device_list[0].usb_board_ids[i] for i in range(self.device_count)]

    @property
    def device_count(self):
        return self._hackrf_device_list[0].devicecount

cdef class device:
    cdef chackrf.hackrf_device* _hackrf_device

    def __cinit__(self):
        self._hackrf_device = NULL
    def __dealloc__(self):
        self.close()

    def close(self):
        if self._hackrf_device is not NULL:
            if <size_t> self._hackrf_device in callbacks.keys():
                callbacks.pop(<size_t> self._hackrf_device)
            _check_err(chackrf.hackrf_close(self._hackrf_device))
            self._hackrf_device = <chackrf.hackrf_device*>NULL

    def set_rx_callback(self, cb_fn):
        callbacks[<size_t> self._hackrf_device]["rx_callback"] = cb_fn

    def start_rx(self):
        _check_err(chackrf.hackrf_start_rx(self.ptr(), _rx_callback, NULL))
    def stop_rx(self):
        _check_err(chackrf.hackrf_stop_rx(self.ptr()))

    def set_freq(self, freq):
        _check_err(chackrf.hackrf_set_freq(self.ptr(), freq))
    def set_sample_rate(self, Fs):
        _check_err(chackrf.hackrf_set_sample_rate(self.ptr(), Fs))
    def set_baseband_filter_bandwidth(self, width):
        cdef uint32_t _width = width
        _check_err(chackrf.hackrf_set_baseband_filter_bandwidth(self.ptr(), _width))
    def set_hw_sync_mode(self, enable):
        cdef uint8_t _enable = enable
        _check_err(chackrf.hackrf_set_hw_sync_mode(self.ptr(), _enable))
    def set_lna_gain(self, gain):
        cdef uint32_t _gain = gain
        _check_err(chackrf.hackrf_set_lna_gain(self.ptr(), _gain))
    def set_vga_gain(self, gain):
        cdef uint32_t _gain = gain
        _check_err(chackrf.hackrf_set_vga_gain(self.ptr(), _gain))
    def set_txvga_gain(self, gain):
        cdef uint32_t _gain = gain
        _check_err(chackrf.hackrf_set_txvga_gain(self.ptr(), _gain))
    def set_amp_enable(self, enable):
        cdef uint8_t _enable = enable
        _check_err(chackrf.hackrf_set_amp_enable(self.ptr(), _enable))
    def set_antenna_enable(self, enable):
        cdef uint8_t _enable = enable
        _check_err(chackrf.hackrf_set_antenna_enable(self.ptr(), _enable))
    def is_streaming(self):
        result = chackrf.hackrf_is_streaming(self.ptr())
        if result == 0:
            return False
        elif result == 1:
            return True
        _check_err(result)

    cdef chackrf.hackrf_device* ptr(self):
        return self._hackrf_device
    cdef chackrf.hackrf_device** dptr(self):
        return &self._hackrf_device
    cdef void _setup(self):
        callbacks[<size_t> self._hackrf_device] = {
            "rx_callback": None,
            "tx_callback": None,
            "sweep_callback": None,
            "tx_complete_callback": None,
            "tx_flush_callback": None,
            "device": self
        }

def init():
    _check_err(chackrf.hackrf_init())
def exit():
    _check_err(chackrf.hackrf_exit())

def open():
    dev = device()
    _check_err(chackrf.hackrf_open(dev.dptr()))
    dev._setup()
    return dev
def open_by_serial(serial_number):
    dev = device()
    _check_err(chackrf.hackrf_open_by_serial(serial_number, dev.dptr()))
    dev._setup()
    return dev
def device_list_open(devs: device_list, idx: int):
    dev = device()
    _check_err(chackrf.hackrf_device_list_open(devs.ptr(), idx, dev.dptr()))
    dev._setup()
    return dev

## ----- Misc ----- ##
def library_version():
    return chackrf.hackrf_library_version().decode("utf-8")
def library_release():
    return chackrf.hackrf_library_release().decode("utf-8")
def board_id_read(dev: device):
    cdef uint8_t board_id
    _check_err(chackrf.hackrf_board_id_read(dev.ptr(), &board_id))
    return board_id
def version_string_read(dev: device):
    cdef char[255] version
    _check_err(chackrf.hackrf_version_string_read(dev.ptr(), version, 255))
    return version.decode("utf-8")
def usb_api_version_read(dev: device):
    cdef uint16_t usb_version
    _check_err(chackrf.hackrf_usb_api_version_read(dev.ptr(), &usb_version))
    return f"{(usb_version >> 8) & 0xFF:x}.{usb_version & 0xFF:02x}"
def board_partid_serialno_read(dev: device):
    cdef chackrf.read_partid_serialno_t pid_sno
    _check_err(chackrf.hackrf_board_partid_serialno_read(dev.ptr(), &pid_sno))
    return (
        f"0x{pid_sno.part_id[0]:08x} 0x{pid_sno.part_id[1]:08x}",
        f"{pid_sno.serial_no[0]:08x}{pid_sno.serial_no[1]:08x}{pid_sno.serial_no[2]:08x}{pid_sno.serial_no[3]:08x}"
    )
