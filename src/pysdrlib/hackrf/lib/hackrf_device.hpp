#pragma once

#include <cstdint>
#include <string>
#include <utility>
#include <vector>

#include "hackrf.h"
#include "hardware_wrapper.hpp"

// libhackrf crashes if used before hackrf_init(). These track init state so every
// entry point can call ensure_init() and Python can't segfault the interpreter.
void hackrf_ensure_init();
void hackrf_init_checked();
void hackrf_exit_checked();

// Throws HardwareError(code, "<name>: <what>") when `result` is a libhackrf error.
void hackrf_check(int result, const char* what);

class HackRFDevice : public HardwareDevice<hackrf_device*> {
public:
    HackRFDevice() = default;
    ~HackRFDevice() override;

    // Opening
    void open();
    void open_by_serial(const std::string& serial);
    void open_from_list(hackrf_device_list_t* list, int index);
    void close() override;

    // libhackrf delivers interleaved signed 8-bit I/Q
    SampleFormat rx_sample_format() const override { return {SampleKind::Int, 8, 8, false}; }

    // Streaming
    void set_rx_callback(RxCallbackFn callback);
    void start_rx();
    void stop_rx();
    bool is_streaming() const;

    // Tuning
    void set_freq(uint64_t freq_hz);
    void set_sample_rate(double sample_rate_hz);
    void set_baseband_filter_bandwidth(uint32_t bandwidth_hz);
    void set_hw_sync_mode(bool enable);

    // Device-specific gain stages
    void set_rx_rf_gain(bool amp_enable);   // RF amp (0 / 11 dB)
    void set_rx_if_gain(uint32_t gain);     // LNA, 0-40 dB in 8 dB steps
    void set_rx_bb_gain(uint32_t gain);     // VGA, 0-62 dB in 2 dB steps
    void set_tx_rf_gain(bool amp_enable);   // RF amp (0 / 11 dB)
    void set_tx_if_gain(uint32_t gain);     // TXVGA, 0-47 dB in 1 dB steps

    // libhackrf-named aliases (kept so existing callers keep working)
    void set_amp_enable(bool enable) { set_rx_rf_gain(enable); }
    void set_lna_gain(uint32_t gain) { set_rx_if_gain(gain); }
    void set_vga_gain(uint32_t gain) { set_rx_bb_gain(gain); }
    void set_txvga_gain(uint32_t gain) { set_tx_if_gain(gain); }
    void set_antenna_enable(bool enable);   // bias-tee

    // Run `data` through the same path libhackrf's streaming thread uses
    // (trampoline -> callback). For tests; needs no hardware.
    int simulate_rx(const uint8_t* data, int valid_length, int buffer_length);

    // Queries
    uint8_t board_id_read() const;
    std::string version_string_read() const;
    std::string usb_api_version_read() const;
    std::pair<std::string, std::string> board_partid_serialno_read() const;

private:
    hackrf_device* require() const;
    static int rx_trampoline(hackrf_transfer* transfer);

    RxCallbackFn rx_callback_;
};

// Snapshot of attached devices.
class HackRFDeviceList {
public:
    HackRFDeviceList();
    ~HackRFDeviceList();
    HackRFDeviceList(const HackRFDeviceList&) = delete;
    HackRFDeviceList& operator=(const HackRFDeviceList&) = delete;

    void close();
    int device_count() const;
    std::vector<std::string> serial_numbers() const;
    std::vector<int> usb_board_ids() const;
    hackrf_device_list_t* get() const { return list_; }

private:
    hackrf_device_list_t* list_ = nullptr;
};
