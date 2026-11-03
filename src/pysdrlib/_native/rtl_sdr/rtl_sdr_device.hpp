#pragma once

#include <atomic>
#include <cstdint>
#include <mutex>
#include <string>
#include <thread>
#include <tuple>
#include <vector>

#include "rtl-sdr.h"
#include "hardware_wrapper.hpp"

// librtlsdr's own errors are bare negative ints (libusb's, or -1), so these are pysdrlib's.
enum RTLSDRErrorCode {
    RTLSDR_ERROR_FAILED = -1001,        // a librtlsdr call failed; the message says which
    RTLSDR_ERROR_INVALID_PARAM = -1002,
    RTLSDR_ERROR_NOT_FOUND = -1003,
    RTLSDR_ERROR_NOT_OPEN = -1004,
    RTLSDR_ERROR_OPEN_FAILED = -1005,
    RTLSDR_ERROR_BUSY = -1006,
};

// Throws HardwareError(RTLSDR_ERROR_FAILED, "<what>: <result>") when `result` is a librtlsdr error.
void rtlsdr_check(int result, const char* what);

// An RTL2832U dongle. librtlsdr's streaming call blocks until cancelled, so the device runs it on a
// thread of its own and the callback is invoked from there.
class RTLSDRDevice : public HardwareDevice<rtlsdr_dev_t*> {
public:
    RTLSDRDevice() = default;
    ~RTLSDRDevice() override;

    // Opening: by position in the USB device list, or by serial number
    void open(uint32_t index);
    void open_by_serial(const std::string& serial);
    void close() override;

    // Unsigned 8-bit, offset binary: mid-scale (127.5) is zero
    SampleFormat rx_sample_format() const override { return {SampleKind::UInt, 8, 8, false}; }

    // Streaming
    void start_rx() override;
    void stop_rx() override;
    bool is_streaming() const override { return running_; }

    // Tuning
    void set_freq(uint64_t freq_hz);
    // librtlsdr accepts 225,001-300,000 and 900,001-3,200,000 Hz. Returns the rate the hardware settled on.
    double set_sample_rate(double rate_hz);
    void set_freq_correction(int ppm);

    // Tuner gain in dB, snapped to the nearest step the tuner has (see gains()) and returned. Switches
    // the tuner to manual gain.
    double set_rx_rf_gain(double gain_db);
    // true: the tuner picks its own gain
    void set_agc(bool enable);
    // Supplies power on the antenna port; only the RTL-SDR Blog dongles have the hardware for it
    void set_bias_t(bool enable);

    // Queries
    std::vector<double> gains() const;          // the tuner's gain steps, in dB
    std::string tuner() const;                  // "R820T", "E4000"...
    std::tuple<std::string, std::string, std::string> usb_strings() const;   // manufacturer, product, serial

protected:
    HardwareError not_open_error() const override;

private:
    static void rx_trampoline(unsigned char* buf, uint32_t len, void* ctx);
    void stop_stream();

    std::mutex stream_mutex_;
    std::thread reader_;
    std::atomic<bool> running_{false};
};

// Dongles attached (they need not be openable)
uint32_t rtlsdr_device_count();
// (name, manufacturer, product, serial) of the dongle at `index`
std::tuple<std::string, std::string, std::string, std::string> rtlsdr_device_info(uint32_t index);
