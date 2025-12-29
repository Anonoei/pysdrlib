import ctypes
from enum import Enum
import time
import os

from . import config
from .config import log
from .obj import hackrf_device, hackrf_transfer_t, hackrf_device_list, hackrf_usb_board_id
from .obj import SAMPLE_CB_FN

# import sys
# sys.settrace(print)

def lib_load(lib_path):
    log.debug("Initializing libhackrf with %s", lib_path)
    config.LIB = ctypes.CDLL(lib_path)

    config.LIB.hackrf_init.argtypes = []
    config.LIB.hackrf_init.restype = ctypes.c_int
    config.LIB.hackrf_exit.argtypes = []
    config.LIB.hackrf_exit.restype = ctypes.c_int
    config.LIB.hackrf_library_release.argtypes = []
    config.LIB.hackrf_library_release.restype = ctypes.c_char_p
    config.LIB.hackrf_library_version.argtypes = []
    config.LIB.hackrf_library_version.restype = ctypes.c_char_p
    config.LIB.hackrf_device_list.argtypes = []
    config.LIB.hackrf_device_list.restype = ctypes.POINTER(hackrf_device_list)
    config.LIB.hackrf_device_list_open.argtypes = [ctypes.POINTER(hackrf_device_list), ctypes.c_int, ctypes.POINTER(ctypes.POINTER(hackrf_device))]
    config.LIB.hackrf_device_list_open.restype = ctypes.c_int
    config.LIB.hackrf_open.argtypes = [ctypes.POINTER(ctypes.POINTER(hackrf_device))]
    config.LIB.hackrf_open.restype = ctypes.c_int
    config.LIB.hackrf_close.argtypes = [ctypes.POINTER(hackrf_device)]
    config.LIB.hackrf_close.restype = ctypes.c_int
    config.LIB.hackrf_board_id_read.argtypes = [ctypes.POINTER(hackrf_device), ctypes.POINTER(ctypes.c_int)]
    config.LIB.hackrf_board_id_read.restype = ctypes.c_int
    # self.lib.hackrf_version_string_read.argtypes = [ctypes.POINTER(hackrf_device), ctypes.POINTER(ctypes.c_char_p)]
    config.LIB.hackrf_version_string_read.restype = ctypes.c_int
    config.LIB.hackrf_supported_platform_read.argtypes = [ctypes.POINTER(hackrf_device), ctypes.POINTER(ctypes.c_uint32)]
    config.LIB.hackrf_supported_platform_read.restype = ctypes.c_int
    config.LIB.hackrf_usb_board_id_name.argtypes = [hackrf_usb_board_id]
    config.LIB.hackrf_usb_board_id_name.restype = ctypes.c_char_p
    config.LIB.hackrf_set_sample_rate.argtypes = [ctypes.POINTER(hackrf_device), ctypes.c_double]
    config.LIB.hackrf_set_sample_rate.restype = ctypes.c_int
    config.LIB.hackrf_set_baseband_filter_bandwidth.argtypes = [ctypes.POINTER(hackrf_device), ctypes.c_uint32]
    config.LIB.hackrf_set_baseband_filter_bandwidth.restype = ctypes.c_int
    config.LIB.hackrf_set_hw_sync_mode.argtypes = [ctypes.POINTER(hackrf_device), ctypes.c_uint8]
    config.LIB.hackrf_set_hw_sync_mode.restype = ctypes.c_int

    # self.lib.hackrf_stop_cmd.argtypes = (ctypes.POINTER(hackrf_device),)
    config.LIB.hackrf_start_rx.argtypes = [ctypes.POINTER(hackrf_device), ctypes.POINTER(SAMPLE_CB_FN), ctypes.c_void_p]
    config.LIB.hackrf_start_rx.restype = ctypes.c_int
    config.LIB.hackrf_stop_rx.argtypes = (ctypes.POINTER(hackrf_device),)

    # self.lib.hackrf_init.side_effect = self._check_error
    # self.lib.hackrf_close.side_effect = self._check_error
    # self.lib.hackrf_board_id_read.side_effect = self._check_error
    # self.lib.hackrf_version_String_read.side_effect = self._check_error
    # self.lib.hackrf_supported_platform_read.side_effect = self._check_error
