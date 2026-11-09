#pragma once

#include <atomic>
#include <cstdint>
#include <string>
#include <utility>
#include <vector>

#include "hackrf.h"
#include "hardware_tx.hpp"
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
    void start_rx() override;
    void stop_rx() override;
    bool is_streaming() const override;

    // Transmit: complex64 in; the libhackrf transmit callback encodes to int8 without the GIL.
    // Half-duplex: start_tx() refuses while receiving and start_rx() while transmitting.
    void set_tx_buffer(size_t samples) { tx_.configure(samples); }
    size_t write_tx(const hw::cfloat* src, size_t n, double timeout) { return tx_.write(src, n, timeout); }
    void start_tx();
    void stop_tx();
    bool is_transmitting() const { return tx_active_.load(); }
    hw::TxQueue::Stats tx_stats() const { return tx_.stats(); }
    std::string tx_error() const;   // non-empty if the transmit stream died under us

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

    // Queries
    uint8_t board_id_read() const;
    std::string version_string_read() const;
    std::string usb_api_version_read() const;
    std::pair<std::string, std::string> board_partid_serialno_read() const;

protected:
    HardwareError not_open_error() const override;

private:
    static int rx_trampoline(hackrf_transfer* transfer);
    static int tx_trampoline(hackrf_transfer* transfer);

    hw::TxQueue tx_;
    std::atomic<bool> tx_active_{false};
    std::vector<hw::cfloat> tx_scratch_;   // complex64 staging for one USB transfer
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
