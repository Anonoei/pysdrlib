import ctypes
from enum import Enum

class TRANSCEIVER_MODE(Enum):
    OFF = 0
    RX = 1
    TX = 2
    SS = 3

    Rx = RX
    Tx = TX

    RECEIEVE = RX
    TRANSMIT = Tx
    SIGNALSOURCE = SS

class RF_PATH_FILTER(Enum):
    BYPASS = 0
    LOW_PASS = 1
    HIGH_PASS = 2

class libusb_device(ctypes.Structure):
    _fields_ = [
        ("bus_number", ctypes.c_uint8),
        ("port_number", ctypes.c_uint8),
        ("device_address", ctypes.c_uint8),
        ("speed", ctypes.c_int),
        ("session_data", ctypes.c_ulong),
    ]

class libusb_device_handle(ctypes.Structure):
    _fields_ = [
        ("lock", ctypes.c_void_p),
        ("claimed_interfaces", ctypes.c_ulong),
        ("list", ctypes.c_void_p),
        ("dev", ctypes.POINTER(libusb_device)),
        ("auto_detach_kernel_driver", ctypes.c_int)
    ]

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
    def json(self):
        return {
            "usb_device": self.usb_device,
            # "transfers": self.transfers,
            "callback": self.callback,
            "transfer_thread_started": self.transfer_thread_started,
            "transfer_thread": self.transfer_thread,
            "streaming": self.streaming,
            "rx_ctx": self.rx_ctx,
            "tx_ctx": self.tx_ctx,
            "do_exit": self.do_exit,
            "buffer": self.buffer,
            "transfers_setup": self.transfers_setup,
            "transfer_lock": self.transfer_lock,
            "active_transfers": self.active_transfers,
            "all_finished_cv": self.all_finished_cv,
            "flush": self.flush,
            "flush_transfer": self.flush_transfer,
            "flush_callback": self.flush_callback,
            "tx_completion_callback": self.tx_completion_callback,
            "flush_ctx": self.flush_ctx
        }

class hackrf_transfer_t(ctypes.Structure):
    _fields_ = [
        ("device", ctypes.POINTER(hackrf_device)),
        ("buffer", ctypes.POINTER(ctypes.c_uint8)),
        ("buffer_length", ctypes.c_int),
        ("valid_length", ctypes.c_int),
        ("rx_ctx", ctypes.c_void_p),
        ("tx_ctx", ctypes.c_void_p)
    ]

class read_partid_serialno(ctypes.Structure):
    _fields_ = [
        ("part_id", ctypes.c_uint32 * 2),
        ("serial_no", ctypes.c_uint32 * 4)
    ]

SAMPLE_CB_FN = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(hackrf_transfer_t))

hackrf_device._fields_ = [ # vendor/hackrf/host/libhackrf/src/hackrf.h: 858
    ("usb_device", ctypes.POINTER(libusb_device_handle)), # libusb_device_handle*
    # ("transfers", ctypes.POINTER(libusb_transfer) * 4),
    ("transfers", ctypes.POINTER(ctypes.POINTER(libusb_transfer))), # struct libusb_transfer**
    # ("transfers", ctypes.c_void_p), # struct libusb_transfer**
    ("callback", SAMPLE_CB_FN), # hackrf_sample_block_cb_fn
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
