#include "hackrf_device.hpp"

#include <cstdio>
#include <mutex>

namespace {
std::mutex g_init_mutex;
bool g_initialized = false;
}

void hackrf_init_checked() {
    std::lock_guard<std::mutex> lock(g_init_mutex);
    if (g_initialized) return;
    hackrf_check(hackrf_init(), "hackrf_init");
    g_initialized = true;
}

void hackrf_ensure_init() { hackrf_init_checked(); }

void hackrf_exit_checked() {
    std::lock_guard<std::mutex> lock(g_init_mutex);
    if (!g_initialized) return;
    g_initialized = false;
    hackrf_check(hackrf_exit(), "hackrf_exit");
}

void hackrf_check(int result, const char* what) {
    if (result >= 0) return;
    const char* name = hackrf_error_name(static_cast<enum hackrf_error>(result));
    throw HardwareError(result, std::string(what) + ": " + (name ? name : "unknown error"));
}

// --- HackRFDevice ---------------------------------------------------------- //

HackRFDevice::~HackRFDevice() {
    try { close(); } catch (...) {}
}

hackrf_device* HackRFDevice::require() const {
    if (!handle_) throw HardwareError(HACKRF_ERROR_INVALID_PARAM, "HackRF device is not open");
    return handle_;
}

void HackRFDevice::open() {
    hackrf_ensure_init();
    close();
    hackrf_check(hackrf_open(&handle_), "hackrf_open");
}

void HackRFDevice::open_by_serial(const std::string& serial) {
    hackrf_ensure_init();
    close();
    hackrf_check(hackrf_open_by_serial(serial.c_str(), &handle_), "hackrf_open_by_serial");
}

void HackRFDevice::open_from_list(hackrf_device_list_t* list, int index) {
    close();
    hackrf_check(hackrf_device_list_open(list, index, &handle_), "hackrf_device_list_open");
}

void HackRFDevice::close() {
    if (!handle_) return;
    hackrf_device* h = handle_;
    handle_ = nullptr;
    int result = hackrf_close(h);   // joins the streaming thread
    rx_callback_ = nullptr;         // only safe to drop once no thread can call it
    hackrf_check(result, "hackrf_close");
}

void HackRFDevice::set_rx_callback(RxCallbackFn callback) {
    rx_callback_ = std::move(callback);
}

int HackRFDevice::rx_trampoline(hackrf_transfer* transfer) {
    auto* self = static_cast<HackRFDevice*>(transfer->rx_ctx);
    if (!self || !self->rx_callback_) return -1;
    return self->rx_callback_(transfer->buffer, static_cast<size_t>(transfer->valid_length),
                              static_cast<size_t>(transfer->buffer_length));
}

int HackRFDevice::simulate_rx(const uint8_t* data, int valid_length, int buffer_length) {
    hackrf_transfer t{};
    t.device = handle_;
    t.buffer = const_cast<uint8_t*>(data);
    t.buffer_length = buffer_length;
    t.valid_length = valid_length;
    t.rx_ctx = this;
    return rx_trampoline(&t);
}

void HackRFDevice::start_rx() {
    hackrf_check(hackrf_start_rx(require(), &HackRFDevice::rx_trampoline, this), "hackrf_start_rx");
}

void HackRFDevice::stop_rx() {
    hackrf_check(hackrf_stop_rx(require()), "hackrf_stop_rx");
}

bool HackRFDevice::is_streaming() const {
    int r = hackrf_is_streaming(require());
    if (r == HACKRF_TRUE) return true;
    // A stream that ended (stop_rx, or the callback returned non-zero) is "not streaming", not a failure.
    if (r == HACKRF_ERROR_STREAMING_STOPPED || r == HACKRF_ERROR_STREAMING_EXIT_CALLED) return false;
    hackrf_check(r, "hackrf_is_streaming");
    return false;
}

void HackRFDevice::set_freq(uint64_t freq_hz) {
    hackrf_check(hackrf_set_freq(require(), freq_hz), "hackrf_set_freq");
}
void HackRFDevice::set_sample_rate(double sample_rate_hz) {
    hackrf_check(hackrf_set_sample_rate(require(), sample_rate_hz), "hackrf_set_sample_rate");
}
void HackRFDevice::set_baseband_filter_bandwidth(uint32_t bandwidth_hz) {
    hackrf_check(hackrf_set_baseband_filter_bandwidth(require(), bandwidth_hz), "hackrf_set_baseband_filter_bandwidth");
}
void HackRFDevice::set_hw_sync_mode(bool enable) {
    hackrf_check(hackrf_set_hw_sync_mode(require(), enable ? 1 : 0), "hackrf_set_hw_sync_mode");
}

void HackRFDevice::set_rx_rf_gain(bool amp_enable) {
    hackrf_check(hackrf_set_amp_enable(require(), amp_enable ? 1 : 0), "hackrf_set_amp_enable");
}
void HackRFDevice::set_rx_if_gain(uint32_t gain) {
    hackrf_check(hackrf_set_lna_gain(require(), gain), "hackrf_set_lna_gain");
}
void HackRFDevice::set_rx_bb_gain(uint32_t gain) {
    hackrf_check(hackrf_set_vga_gain(require(), gain), "hackrf_set_vga_gain");
}
void HackRFDevice::set_tx_rf_gain(bool amp_enable) {
    hackrf_check(hackrf_set_amp_enable(require(), amp_enable ? 1 : 0), "hackrf_set_amp_enable");
}
void HackRFDevice::set_tx_if_gain(uint32_t gain) {
    hackrf_check(hackrf_set_txvga_gain(require(), gain), "hackrf_set_txvga_gain");
}
void HackRFDevice::set_antenna_enable(bool enable) {
    hackrf_check(hackrf_set_antenna_enable(require(), enable ? 1 : 0), "hackrf_set_antenna_enable");
}

uint8_t HackRFDevice::board_id_read() const {
    uint8_t id = 0;
    hackrf_check(hackrf_board_id_read(require(), &id), "hackrf_board_id_read");
    return id;
}

std::string HackRFDevice::version_string_read() const {
    char version[256] = {0};
    hackrf_check(hackrf_version_string_read(require(), version, 255), "hackrf_version_string_read");
    return version;
}

std::string HackRFDevice::usb_api_version_read() const {
    uint16_t v = 0;
    hackrf_check(hackrf_usb_api_version_read(require(), &v), "hackrf_usb_api_version_read");
    char buf[16];
    std::snprintf(buf, sizeof buf, "%x.%02x", (v >> 8) & 0xFF, v & 0xFF);
    return buf;
}

std::pair<std::string, std::string> HackRFDevice::board_partid_serialno_read() const {
    read_partid_serialno_t p{};
    hackrf_check(hackrf_board_partid_serialno_read(require(), &p), "hackrf_board_partid_serialno_read");
    char part[32], serial[40];
    std::snprintf(part, sizeof part, "0x%08x 0x%08x", p.part_id[0], p.part_id[1]);
    std::snprintf(serial, sizeof serial, "%08x%08x%08x%08x",
                  p.serial_no[0], p.serial_no[1], p.serial_no[2], p.serial_no[3]);
    return {part, serial};
}

// --- HackRFDeviceList ------------------------------------------------------ //

HackRFDeviceList::HackRFDeviceList() {
    hackrf_ensure_init();
    list_ = hackrf_device_list();
}
HackRFDeviceList::~HackRFDeviceList() { close(); }

void HackRFDeviceList::close() {
    if (list_) {
        hackrf_device_list_free(list_);
        list_ = nullptr;
    }
}

int HackRFDeviceList::device_count() const { return list_ ? list_->devicecount : 0; }

std::vector<std::string> HackRFDeviceList::serial_numbers() const {
    std::vector<std::string> out;
    for (int i = 0; i < device_count(); ++i)
        out.emplace_back(list_->serial_numbers[i] ? list_->serial_numbers[i] : "");
    return out;
}

std::vector<int> HackRFDeviceList::usb_board_ids() const {
    std::vector<int> out;
    for (int i = 0; i < device_count(); ++i)
        out.push_back(static_cast<int>(list_->usb_board_ids[i]));
    return out;
}
