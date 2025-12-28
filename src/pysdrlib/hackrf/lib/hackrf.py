from enum import Enum
import ctypes

# import sys
# sys.settrace(print)

class libusb_device_handle(ctypes.Structure):
    pass

class libusb_transfer(ctypes.Structure):
    _fields_ = [
        ("handle", ctypes.c_void_p),
        ("flags", ctypes.c_uint8),
        ("endpoint", ctypes.c_ubyte),
        ("type", ctypes.c_ubyte),
        ("timeout", ctypes.c_int),
        ("status", ctypes.c_uint16),
        ("length", ctypes.c_int),
        ("actual_length", ctypes.c_int),
        ("callback", ctypes.c_void_p),
        ("user_data", ctypes.c_void_p),
        ("buffer", ctypes.POINTER(ctypes.c_ubyte)),
        ("num_iso_packets", ctypes.c_int),
        ("iso_packet_desc", ctypes.c_void_p),
    ]

class HackRFError(Exception):
    pass

class hackrf_usb_board_id(ctypes.Structure):
    _fields_ = [
        ("USB_BOARD_ID_JAWBREAKER", ctypes.c_int),
        ("USB_BOARD_ID_HACKRF_ONE", ctypes.c_int),
        ("USB_BOARD_ID_RAD1O", ctypes.c_int),
        ("USB_BOARD_ID_INVALID", ctypes.c_int),
    ]

class hackrf_device_list(ctypes.Structure):
    _fields_ = [
        ("serial_numbers", ctypes.POINTER(ctypes.c_char_p)),
        ("usb_board_ids", ctypes.POINTER(hackrf_usb_board_id)),
        ("usb_device_index", ctypes.POINTER(ctypes.c_int)),
        ("devicecount", ctypes.c_int),
        ("usb_devices", ctypes.c_void_p),
        ("usb_devicecount", ctypes.c_int)
    ]

class hackrf_device(ctypes.Structure):
    _fields_ = [
        ("usb_device", ctypes.POINTER(ctypes.c_void_p)), # libusb_device_handle*
        ("transfers", ctypes.POINTER(ctypes.POINTER(libusb_transfer))), # struct libusb_transfer**
        # ("transfers", ctypes.c_void_p), # struct libusb_transfer**
        ("callback", ctypes.c_void_p), # hackrf_sample_block_cb_fn
        ("transfer_thread_started", ctypes.c_bool), # volatile bool
        ("transfer_thread", ctypes.c_void_p), # pthread_t
        ("streaming", ctypes.c_bool), # volatile bool
        ("rx_ctx", ctypes.c_void_p), # void*
        ("tx_ctx", ctypes.c_void_p), # void*
        ("do_exit", ctypes.c_bool), # volatile bool
        ("buffer", ctypes.c_ubyte * (4 * 262144)), # unsigned char buffer[TRANSFER_COUNT * TRANSFER_BUFFER_SIZE]
        ("transfers_setup", ctypes.c_bool), # bool
        ("transfer_lock", ctypes.c_void_p), # pthread_mutex_t
        ("active_transfers", ctypes.c_int), # volatile int
        ("all_finished_cv", ctypes.c_void_p), # pthread_cond_t
        ("flush", ctypes.c_bool), # bool flush
        ("flush_transfer", ctypes.c_void_p), # struct libusb_transfer*
        ("flush_callback", ctypes.c_void_p), # hackrf_flush_cb_fn
        ("tx_completion_callback", ctypes.c_void_p), # hackrf_tx_block_complete_cb_fn
        ("flush_ctx", ctypes.c_void_p) # void* flush_ctx
    ]

class read_partid_serialno(ctypes.Structure):
    _fields_ = [
        ("part_id", ctypes.c_uint32 * 2),
        ("serial_no", ctypes.c_uint32 * 4)
    ]

class libhackrf(ctypes.Structure):
    _fields_ = []
    def __init__(self, lib_path):
        print(f"Initializing {type(self).__name__} with {lib_path}")
        self.lib = ctypes.CDLL(lib_path)

        self.lib.hackrf_init.argtypes = []
        self.lib.hackrf_init.restype = ctypes.c_int
        self.lib.hackrf_close.argtypes = [ctypes.POINTER(hackrf_device)]
        self.lib.hackrf_close.restype = ctypes.c_int

        self.lib.hackrf_device_list.argtypes = []
        self.lib.hackrf_device_list.restype = ctypes.POINTER(hackrf_device_list)

        self.lib.hackrf_device_list_open.argtypes = (ctypes.POINTER(hackrf_device_list), ctypes.c_int, ctypes.POINTER(ctypes.POINTER(hackrf_device)))
        self.lib.hackrf_device_list_open.restype = ctypes.c_int

        self.lib.hackrf_board_id_read.argtypes = [ctypes.POINTER(hackrf_device), ctypes.POINTER(ctypes.c_int)]
        self.lib.hackrf_board_id_read.restype = ctypes.c_int

        self.lib.hackrf_board_id_name.argtypes = []

        # self.lib.hackrf_version_string_read.argtypes = [ctypes.POINTER(hackrf_device), ctypes.POINTER(ctypes.c_char_p)]
        self.lib.hackrf_version_string_read.restype = ctypes.c_int

        self.lib.hackrf_supported_platform_read.argtypes = [ctypes.POINTER(hackrf_device), ctypes.POINTER(ctypes.c_uint32)]
        self.lib.hackrf_supported_platform_read.restype = ctypes.c_int

        self.lib.hackrf_library_release.argtypes = []
        self.lib.hackrf_library_release.restype = ctypes.c_char_p
        self.lib.hackrf_library_version.argtypes = []
        self.lib.hackrf_library_version.restype = ctypes.c_char_p

        self.lib.hackrf_usb_board_id_name.argtypes = [hackrf_usb_board_id]
        self.lib.hackrf_usb_board_id_name.restype = ctypes.c_char_p

        # self.lib.hackrf_init.side_effect = self._check_error
        # self.lib.hackrf_close.side_effect = self._check_error
        # self.lib.hackrf_board_id_read.side_effect = self._check_error
        # self.lib.hackrf_version_String_read.side_effect = self._check_error
        # self.lib.hackrf_supported_platform_read.side_effect = self._check_error

    def _check_error(self, result):
        if result != 0:
            if result == -2: raise HackRFError("INVALID_PARAM")
            if result == -5: raise HackRFError("NOT_FOUND")
            if result == -6: raise HackRFError("BUSY")
            if result == -11: raise HackRFError("NO_MEM")
            if result == -1000: raise HackRFError("LIBUSB")
            if result == -1001: raise HackRFError("THREAD")
            if result == -1002: raise HackRFError("STREAMING_THREAD_ERR")
            if result == -1003: raise HackRFError("STREAMING_STOPPED")
            if result == -1004: raise HackRFError("STREAMING_EXIT_CALLED")
            if result == -1005: raise HackRFError("USB_API_VERSION")
            if result == -2000: raise HackRFError("NOT_LAST_DEVICE")
            if result == -9999: raise HackRFError("OTHER")
        return result


    def init(self):
        result = self.lib.hackrf_init()
        self._check_error(result)

    def exit(self):
        result = self.lib.hackrf_exit()
        self._check_error(result)

    def close(self, device):
        result = self.lib.hackrf_close(device)
        self._check_error(result)

    def device_list(self):
        pointer = self.lib.hackrf_device_list()
        struct = pointer.contents
        board_ids = struct.usb_board_ids.contents
        board_ids = self.lib.hackrf_usb_board_id_name(board_ids)
        return {
            'ptr': pointer,
            'serial_numbers': struct.serial_numbers,
            'usb_board_ids': board_ids,
            'usb_device_index': struct.usb_device_index.contents,
            'devicecount': struct.devicecount,
            'usb_devices': struct.usb_devices,
            'usb_devicecount': struct.usb_devicecount
        }

    def device_list_open(self, list, index):
        device = ctypes.POINTER(hackrf_device)()
        result = self.lib.hackrf_device_list_open(list, index, device)
        self._check_error(result)
        return device

    def board_id_read(self, device):
        board_id = ctypes.c_int(0)
        result = self.lib.hackrf_board_id_read(device, ctypes.byref(board_id))
        self._check_error(result)
        return board_id.value

    def get_version_string(self, device):
        # version = ctypes.c_char_p()
        version = ctypes.create_string_buffer(255)
        result = self.lib.hackrf_version_string_read(device, ctypes.byref(version, 0), 255)
        self._check_error(result)
        return version.value.decode()

    def usb_api_version_read(self, device):
        usb_version = ctypes.c_uint16()
        result = self.lib.hackrf_usb_api_version_read(device, ctypes.byref(usb_version))
        self._check_error(result)
        return usb_version.value

    def board_pardid_serialno_read(self, device):
        partid_serialno = read_partid_serialno()
        result = self.lib.hackrf_board_partid_serialno_read(device, ctypes.byref(partid_serialno))
        self._check_error(result)
        return partid_serialno

    def device_list_free(self, list):
        result = self.lib.hackrf_device_list_free(list)
        self._check_error(result)

    def get_supported_platform(self, device):
        platform = ctypes.c_uint32(0)
        self.lib.hackrf_supported_platform_read(device, ctypes.byref(platform))
        return platform.value

    def library_release(self):
        return self.lib.hackrf_library_release().decode()

    def library_version(self):
        return self.lib.hackrf_library_version().decode()

    def hackrf_info(self):
        self.init()
        print(f"libhackrf version: {self.library_release()} ({self.library_version()})")
        list = self.device_list()

        if list["devicecount"] < 1:
            print("No HackRF boards found")
            return

        for i in range(list["devicecount"]):
            print("Found HackRF")
            print(f"Index: {i}")
            if list["serial_numbers"][i]:
                print(f"Serial number: {list['serial_numbers'][i].decode()}")

            device = self.device_list_open(list["ptr"], i)
            board_id = self.board_id_read(device)
            print(f"Board ID Number: {board_id}")

            version = self.get_version_string(device)
            usb_version = self.usb_api_version_read(device)

            print(f"Firmware version: {version} (API:{(usb_version >> 8) & 0xFF}.{usb_version & 0xFF})")

            partid_serialno = self.board_pardid_serialno_read(device)
            print(f"Part ID Number: 0x{partid_serialno.part_id[0]:08x} 0x{partid_serialno.part_id[1]:08x}")

            self.close(device)

        self.device_list_free(list["ptr"])
        self.exit()
