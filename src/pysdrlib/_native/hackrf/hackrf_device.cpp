#include "hackrf_device.hpp"

#include "hardware_convert.hpp"

#include <algorithm>
#include <cstdio>
#include <cstring>
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

HardwareError HackRFDevice::not_open_error() const {
    return HardwareError(HACKRF_ERROR_INVALID_PARAM, "HackRF device is not open");
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
    tx_[0].close();                    // wake blocked writers; the callback then sends silence
    tx_active_ = false;
    int result = hackrf_close(h);   // joins the streaming thread
    rx_callback_ = nullptr;         // only safe to drop once no thread can call it
    hackrf_check(result, "hackrf_close");
}

int HackRFDevice::rx_trampoline(hackrf_transfer* transfer) {
    auto* self = static_cast<HackRFDevice*>(transfer->rx_ctx);
    if (!self) return -1;
    return self->dispatch_rx(transfer->buffer, static_cast<size_t>(transfer->valid_length),
                             static_cast<size_t>(transfer->buffer_length));
}

void HackRFDevice::start_rx() {
    if (tx_active_.load()) throw HardwareError(HACKRF_ERROR_BUSY, "HackRF is transmitting (half-duplex): stop_tx() first");
    hackrf_check(hackrf_start_rx(require(), &HackRFDevice::rx_trampoline, this), "hackrf_start_rx");
}

void HackRFDevice::stop_rx() {
    hackrf_check(hackrf_stop_rx(require()), "hackrf_stop_rx");
}

int HackRFDevice::tx_trampoline(hackrf_transfer* transfer) {
    auto* self = static_cast<HackRFDevice*>(transfer->tx_ctx);
    if (!self) return -1;
    const size_t capacity = static_cast<size_t>(transfer->buffer_length) / 2;   // complex samples that fit
    const size_t want = std::min(capacity, self->tx_scratch_.size());
    const size_t got = want ? self->tx_[0].read(self->tx_scratch_.data(), want, 0.0) : 0;
    if (got) hw::encode_samples({SampleKind::Int, 8, 8, false}, self->tx_scratch_.data(), got, transfer->buffer);
    if (got < capacity) {   // nothing (or not enough) queued: fill the rest with silence rather than stall
        std::memset(transfer->buffer + 2 * got, 0, 2 * (capacity - got));
        self->tx_[0].note_underrun(capacity - got);
    }
    transfer->valid_length = transfer->buffer_length;
    return 0;
}

void HackRFDevice::start_tx() {
    hackrf_device* h = require();
    if (tx_active_.load()) return;
    if (tx_[0].capacity() == 0) throw HardwareError(HACKRF_ERROR_INVALID_PARAM, "set_tx_buffer() before start_tx()");
    if (is_streaming()) throw HardwareError(HACKRF_ERROR_BUSY, "HackRF is receiving (half-duplex): stop_rx() first");
    tx_scratch_.assign(hackrf_get_transfer_buffer_size(h) / 2, hw::cfloat(0.f, 0.f));
    tx_[0].open();
    tx_active_ = true;
    int r = hackrf_start_tx(h, &HackRFDevice::tx_trampoline, this);
    if (r < 0) {
        tx_active_ = false;
        tx_[0].close();
        hackrf_check(r, "hackrf_start_tx");
    }
}

void HackRFDevice::stop_tx() {
    hackrf_device* h = require();
    if (!tx_active_.load()) return;
    tx_[0].close();   // wakes writers blocked on a full queue
    tx_active_ = false;
    hackrf_check(hackrf_stop_tx(h), "hackrf_stop_tx");
}

std::string HackRFDevice::tx_error() const {
    if (!tx_active_.load() || !handle_) return "";
    const int r = hackrf_is_streaming(handle_);
    if (r == HACKRF_TRUE) return "";
    const char* name = hackrf_error_name(static_cast<enum hackrf_error>(r));
    return std::string("transmit stream ended: ") + (name ? name : "unknown error");
}

bool HackRFDevice::is_streaming() const {
    if (!handle_) return false;
    int r = hackrf_is_streaming(handle_);
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
