from libc.stdint cimport uint8_t, uint16_t, uint32_t, uint64_t

cdef extern from "hackrf.h" nogil:
    int SAMPLES_PER_BLOCK
    int BYTES_PER_BLOCK
    int MAX_SWEEP_RANGES

    enum hackrf_error:
        HACKRF_SUCCESS
        HACKRF_TRUE
        HACKRF_ERROR_INVALID_PARAM
        HACKRF_ERROR_NOT_FOUND
        HACKRF_ERROR_BUSY
        HACKRF_ERROR_NO_MEM
        HACKRF_ERROR_LIBUSB
        HACKRF_ERROR_THREAD
        HACKRF_ERROR_STREAMING_THREAD_ERR
        HACKRF_ERROR_STREAMING_STOPPED
        HACKRF_ERROR_STREAMING_EXIT_CALLED
        HACKRF_ERROR_USB_API_VERSION
        HACKRF_ERROR_NOT_LAST_DEVICE
        HACKRF_ERROR_OTHER

    int HACKRF_BOARD_REV_GSG
    int HACKRF_PLATFORM_JAWBREAKER
    int HACKRF_PLATFORM_HACKRF1_OG
    int HACKRF_PLATFORM_RAD1O
    int HACKRF_PLATFORM_HACKRF1_R9

    enum hackrf_board_id:
        BOARD_ID_JELLYBEAN
        BOARD_ID_JAWBREAKER
        BOARD_ID_HACKRF1_OG
        BOARD_ID_RAD1O
        BOARD_ID_HACKRF1_R9
        BOARD_ID_UNRECOGNIZED
        BOARD_ID_UNDETECTED

    int BOARD_ID_HACKRF_ONE
    int BOARD_ID_INVALID

    enum hackrf_usb_board_id:
        USB_BOARD_ID_JAWBREAKER
        USB_BOARD_ID_HACKRF_ONE
        USB_BOARD_ID_RAD1O
        USB_BOARD_ID_INVALID

    enum rf_path_filter:
        RF_PATH_FILTER_BYPASS
        RF_PATH_FILTER_LOW_PASS
        RF_PATH_FILTER_HIGH_PASS

    enum sweep_style:
        LINEAR
        INTERLEAVED

    ctypedef struct hackrf_device:
        pass

    ctypedef struct hackrf_transfer:
        hackrf_device* device
        uint8_t* buffer
        int buffer_length
        int valid_length
        void* rx_ctx
        void* tx_ctx

    ctypedef struct read_partid_serialno_t:
        uint32_t[2] part_id
        uint32_t[4] serial_no

    ctypedef struct hackrf_bool_user_setting:
        int do_update
        int change_on_mode_entry
        int enabled

    ctypedef struct hackrf_bias_t_user_setting_req:
        hackrf_bool_user_setting tx
        hackrf_bool_user_setting rx
        hackrf_bool_user_setting off

    ctypedef struct hackrf_m0_state:
        uint16_t requested_mode
        uint16_t request_flag
        uint32_t active_mode
        uint32_t m0_count
        uint32_t m4_count
        uint32_t num_shortfalls
        uint32_t longest_shortfall
        uint32_t shortfall_limit
        uint32_t threshold
        uint32_t next_mode
        uint32_t error

    ctypedef struct hackrf_device_list_t:
        char** serial_numbers
        hackrf_usb_board_id* usb_board_ids
        int* usb_device_index
        int devicecount
        void** usb_devices
        int usb_devicecount
    hackrf_device_list_t* hackrf_device_list()

    ctypedef int (*hackrf_sample_block_cb_fn)(hackrf_transfer* transfer)

    ctypedef void (*hackrf_tx_block_complete_cb_fn)(hackrf_transfer* transfer, int)

    ctypedef void (*hackrf_flush_cb_fn)(void* flush_ctx, int)

    int hackrf_init()
    int hackrf_exit()
    const char* hackrf_library_version()
    const char* hackrf_library_release()

    int hackrf_device_list_open(
        hackrf_device_list_t* list,
        int idx,
        hackrf_device** device)
    void hackrf_device_list_free(hackrf_device_list_t* list)
    int hackrf_open(hackrf_device** device)
    int hackrf_open_by_serial(
        const char* desired_serial_number,
        hackrf_device** device)
    int hackrf_close(hackrf_device* device)
    int hackrf_start_rx(
        hackrf_device* device,
        hackrf_sample_block_cb_fn callback,
        void* rx_ctx)
    int hackrf_stop_rx(hackrf_device* device)
    int hackrf_start_tx(
        hackrf_device* device,
        hackrf_sample_block_cb_fn callback,
        void* tx_ctx)
    int hackrf_set_tx_block_complete_callback(
        hackrf_device* device,
        hackrf_tx_block_complete_cb_fn callback)
    int hackrf_enable_tx_flush(
        hackrf_device* device,
        hackrf_flush_cb_fn callback,
        void* flush_ctx)
    int hackrf_stop_tx(hackrf_device* device)
    int hackrf_get_m0_state(
        hackrf_device* device,
        hackrf_m0_state* value)
    int hackrf_set_tx_underrun_limit(
        hackrf_device* device,
        uint32_t value)
    int hackrf_set_rx_overrun_limit(
        hackrf_device* device,
        uint32_t value)
    int hackrf_is_streaming(hackrf_device* device)
    int hackrf_set_baseband_filter_bandwidth(
        hackrf_device* device,
        const uint32_t bandwidth_hz)
    int hackrf_board_id_read(hackrf_device* device, uint8_t* value)
    int hackrf_version_string_read(
        hackrf_device* device,
        char* version,
        uint8_t length)
    int hackrf_usb_api_version_read(
        hackrf_device* device,
        uint16_t* version)
    int hackrf_set_freq(hackrf_device* device, const uint64_t freq_hz)
    int hackrf_set_sample_rate(
        hackrf_device* device,
        const double freq_hz)
    int hackrf_set_amp_enable(
        hackrf_device* device,
        const uint8_t value)
    int hackrf_board_partid_serialno_read(
        hackrf_device* device,
        read_partid_serialno_t* partid_serialno)
    int hackrf_set_lna_gain(hackrf_device* device, uint32_t value)
    int hackrf_set_vga_gain(hackrf_device* device, uint32_t value)
    int hackrf_set_txvga_gain(hackrf_device* device, uint32_t value)
    int hackrf_set_antenna_enable(
        hackrf_device* device,
        const uint8_t value)
    const char* hackrf_error_name(hackrf_error errcode)
    uint32_t hackrf_compute_baseband_filter_bw_round_down_lt(
        const uint32_t bandwidth_hz)
    uint32_t hackrf_compute_baseband_filter_bw(
        const uint32_t bandwidth_hz)
    int hackrf_set_hw_sync_mode(
        hackrf_device* device,
        const uint8_t value)
    int hackrf_reset(hackrf_device* device)
