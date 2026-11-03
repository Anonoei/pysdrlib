#include "rtl_sdr_device.hpp"

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <limits>

void rtlsdr_check(int result, const char* what) {
    if (result >= 0) return;
    throw HardwareError(RTLSDR_ERROR_FAILED, std::string(what) + " failed (" + std::to_string(result) + ")");
}

uint32_t rtlsdr_device_count() { return rtlsdr_get_device_count(); }

std::tuple<std::string, std::string, std::string, std::string> rtlsdr_device_info(uint32_t index) {
    if (index >= rtlsdr_get_device_count())
        throw HardwareError(RTLSDR_ERROR_NOT_FOUND, "No RTL-SDR with index " + std::to_string(index));
    char manufacturer[256] = {0}, product[256] = {0}, serial[256] = {0};
    rtlsdr_get_device_usb_strings(index, manufacturer, product, serial);   // fails for unreadable dongles: left empty
    const char* name = rtlsdr_get_device_name(index);
    return {name ? name : "", manufacturer, product, serial};
}

// --- RTLSDRDevice ---------------------------------------------------------- //

RTLSDRDevice::~RTLSDRDevice() {
    try { close(); } catch (...) {}
}

HardwareError RTLSDRDevice::not_open_error() const {
    return HardwareError(RTLSDR_ERROR_NOT_OPEN, "RTL-SDR device is not open");
}

void RTLSDRDevice::open(uint32_t index) {
    close();
    if (index >= rtlsdr_get_device_count())
        throw HardwareError(RTLSDR_ERROR_NOT_FOUND, "No RTL-SDR with index " + std::to_string(index));
    rtlsdr_dev_t* dev = nullptr;
    if (rtlsdr_open(&dev, index) < 0 || !dev)
        throw HardwareError(RTLSDR_ERROR_OPEN_FAILED,
                            "rtlsdr_open failed: no permission to use the USB device (check your udev rules), "
                            "or something else is using it");
    handle_ = dev;
}

void RTLSDRDevice::open_by_serial(const std::string& serial) {
    int index = rtlsdr_get_index_by_serial(serial.c_str());
    if (index < 0)
        throw HardwareError(RTLSDR_ERROR_NOT_FOUND, "No RTL-SDR with serial number '" + serial + "'");
    open(static_cast<uint32_t>(index));
}

void RTLSDRDevice::close() {
    if (!handle_) return;
    stop_stream();                  // joins the streaming thread
    rtlsdr_dev_t* h = handle_;
    handle_ = nullptr;
    rx_callback_ = nullptr;         // only safe to drop once no thread can call it
    rtlsdr_check(rtlsdr_close(h), "rtlsdr_close");
}

void RTLSDRDevice::rx_trampoline(unsigned char* buf, uint32_t len, void* ctx) {
    auto* self = static_cast<RTLSDRDevice*>(ctx);
    if (self->dispatch_rx(buf, len, len) != 0 && self->handle_)
        rtlsdr_cancel_async(self->handle_);   // the callback asked to stop
}

void RTLSDRDevice::start_rx() {
    rtlsdr_dev_t* dev = require();
    std::lock_guard<std::mutex> lock(stream_mutex_);
    if (running_)
        throw HardwareError(RTLSDR_ERROR_BUSY, "RTL-SDR is already streaming");
    if (reader_.joinable()) reader_.join();   // a stream that ended by itself
    rtlsdr_check(rtlsdr_reset_buffer(dev), "rtlsdr_reset_buffer");
    running_ = true;
    reader_ = std::thread([this, dev] {
        rtlsdr_read_async(dev, &RTLSDRDevice::rx_trampoline, this, 0, 0);   // blocks until cancelled
        running_ = false;
    });
}

void RTLSDRDevice::stop_rx() {
    require();
    stop_stream();
}

void RTLSDRDevice::stop_stream() {
    std::lock_guard<std::mutex> lock(stream_mutex_);
    if (!reader_.joinable()) return;
    if (reader_.get_id() == std::this_thread::get_id())
        throw HardwareError(RTLSDR_ERROR_BUSY, "stop_rx cannot be called from the receive callback; return non-zero instead");
    // The cancel is ignored until the reader has entered rtlsdr_read_async, so repeat it until it ends
    while (running_) {
        rtlsdr_cancel_async(handle_);
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    reader_.join();
}

void RTLSDRDevice::set_freq(uint64_t freq_hz) {
    rtlsdr_dev_t* dev = require();
    if (freq_hz > std::numeric_limits<uint32_t>::max())
        throw HardwareError(RTLSDR_ERROR_INVALID_PARAM, "frequency is out of range for RTL-SDR");
    rtlsdr_check(rtlsdr_set_center_freq(dev, static_cast<uint32_t>(freq_hz)), "rtlsdr_set_center_freq");
}

double RTLSDRDevice::set_sample_rate(double rate_hz) {
    rtlsdr_dev_t* dev = require();
    int result = rtlsdr_set_sample_rate(dev, static_cast<uint32_t>(std::llround(rate_hz)));
    if (result == -EINVAL)
        throw HardwareError(RTLSDR_ERROR_INVALID_PARAM,
                            "RTL-SDR sample rate must be 225,001-300,000 or 900,001-3,200,000 Hz");
    rtlsdr_check(result, "rtlsdr_set_sample_rate");
    return static_cast<double>(rtlsdr_get_sample_rate(dev));
}

void RTLSDRDevice::set_freq_correction(int ppm) {
    int result = rtlsdr_set_freq_correction(require(), ppm);
    if (result == -2) return;   // already set to this
    rtlsdr_check(result, "rtlsdr_set_freq_correction");
}

std::vector<double> RTLSDRDevice::gains() const {
    rtlsdr_dev_t* dev = require();
    int count = rtlsdr_get_tuner_gains(dev, nullptr);
    rtlsdr_check(count, "rtlsdr_get_tuner_gains");
    std::vector<int> tenths(count);
    if (count) rtlsdr_check(rtlsdr_get_tuner_gains(dev, tenths.data()), "rtlsdr_get_tuner_gains");
    std::vector<double> out;
    for (int t : tenths) out.push_back(t / 10.0);
    return out;
}

double RTLSDRDevice::set_rx_rf_gain(double gain_db) {
    rtlsdr_dev_t* dev = require();
    std::vector<double> steps = gains();
    if (steps.empty())
        throw HardwareError(RTLSDR_ERROR_FAILED, "this tuner has no adjustable gain");
    double nearest = *std::min_element(steps.begin(), steps.end(), [&](double a, double b) {
        return std::fabs(a - gain_db) < std::fabs(b - gain_db);
    });
    rtlsdr_check(rtlsdr_set_tuner_gain_mode(dev, 1), "rtlsdr_set_tuner_gain_mode");
    rtlsdr_check(rtlsdr_set_tuner_gain(dev, static_cast<int>(std::lround(nearest * 10))), "rtlsdr_set_tuner_gain");
    return nearest;
}

void RTLSDRDevice::set_agc(bool enable) {
    rtlsdr_check(rtlsdr_set_tuner_gain_mode(require(), enable ? 0 : 1), "rtlsdr_set_tuner_gain_mode");
}

void RTLSDRDevice::set_bias_t(bool enable) {
    rtlsdr_check(rtlsdr_set_bias_tee(require(), enable ? 1 : 0), "rtlsdr_set_bias_tee");
}

std::string RTLSDRDevice::tuner() const {
    switch (rtlsdr_get_tuner_type(require())) {
        case RTLSDR_TUNER_E4000: return "E4000";
        case RTLSDR_TUNER_FC0012: return "FC0012";
        case RTLSDR_TUNER_FC0013: return "FC0013";
        case RTLSDR_TUNER_FC2580: return "FC2580";
        case RTLSDR_TUNER_R820T: return "R820T";
        case RTLSDR_TUNER_R828D: return "R828D";
        default: return "unknown";
    }
}

std::tuple<std::string, std::string, std::string> RTLSDRDevice::usb_strings() const {
    char manufacturer[256] = {0}, product[256] = {0}, serial[256] = {0};
    rtlsdr_check(rtlsdr_get_usb_strings(require(), manufacturer, product, serial), "rtlsdr_get_usb_strings");
    return {manufacturer, product, serial};
}
