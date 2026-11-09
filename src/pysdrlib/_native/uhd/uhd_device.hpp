#pragma once

#include <array>
#include <atomic>
#include <cstdint>
#include <map>
#include <mutex>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#include "hardware_tx.hpp"
#include "hardware_wrapper.hpp"
#include "uhd_shim.h"

// Error codes beyond UHD's own (see uhd_shim.h for the shim's)
constexpr int UHD_PY_NOT_FOUND = 2000;
constexpr int UHD_PY_NOT_OPEN = 2001;
constexpr int UHD_PY_INVALID = 2002;

// Throws HardwareError(code, "<what>: <UHD's message>") when `code` is non-zero.
void uhd_check(int code, const ushim* s, const char* what);

using UHDAddress = std::map<std::string, std::string>;   // one device: type, serial, name, product...

// Attached USRPs; `args` narrows the search (UHD device address string)
std::vector<UHDAddress> uhd_find(const std::string& args = "");

enum class Dir : int { RX = 0, TX = 1 };

class UHDDevice : public HardwareDevice<ushim*> {
public:
    UHDDevice() = default;
    ~UHDDevice() override;

    void open(const std::string& args);
    void close() override;

    // UHD delivers (and takes) sc16: interleaved signed 16-bit I/Q
    SampleFormat rx_sample_format() const override { return {SampleKind::Int, 16, 16, false}; }

    // Receive: the stream carries `rx_channels` (default channel 0); channel k of the list goes to queue slot k
    // (set_rx_buffer(samples, slots) first). All of them are sampled together: their indices line up
    void set_rx_channels(const std::vector<size_t>& channels);
    std::vector<size_t> rx_channels() const { return rx_channels_; }
    void start_rx() override;
    void stop_rx() override;
    bool is_streaming() const override { return rx_running_.load(); }
    uint64_t rx_overflows() const { return rx_overflows_.load(); }
    uint64_t rx_lost() const { return rx_lost_.load(); }   // samples the overflows lost (from the packets' timestamps)
    std::string rx_error() const;

    // Transmit: write complex64 per channel slot, the transmit thread encodes it. Several channels go out sample
    // for sample together: the thread sends only what every channel's queue has (silence for all when none has any)
    static constexpr size_t MAX_TX_SLOTS = 4;
    void set_tx_channels(const std::vector<size_t>& channels);
    std::vector<size_t> tx_channels() const { return tx_channels_; }
    void set_tx_buffer(size_t samples) { for (auto& q : tx_) q.configure(samples); }
    size_t write_tx(const hw::cfloat* src, size_t n, double timeout, size_t slot = 0) { return tx_.at(slot).write(src, n, timeout); }
    void start_tx();
    void stop_tx();
    bool is_transmitting() const { return tx_running_.load(); }
    hw::TxQueue::Stats tx_stats(size_t slot = 0) const { return tx_.at(slot).stats(); }
    uint64_t tx_async_errors() const { return tx_async_errors_.load(); }
    std::string tx_error() const;

    // Tuning and gain, per channel
    void set_rate(Dir d, double rate, size_t chan);
    double rate(Dir d, size_t chan) const;
    std::vector<std::vector<double>> rate_ranges(Dir d, size_t chan) const;
    void set_freq(Dir d, double freq, size_t chan);
    double freq(Dir d, size_t chan) const;
    std::pair<double, double> freq_range(Dir d, size_t chan) const;
    void set_gain(Dir d, double gain, size_t chan);
    double gain(Dir d, size_t chan) const;
    std::vector<double> gain_range(Dir d, size_t chan) const;   // lo, hi, step
    void set_agc(bool enable, size_t chan);
    void set_bandwidth(Dir d, double bw, size_t chan);
    double bandwidth(Dir d, size_t chan) const;
    std::pair<double, double> bandwidth_range(Dir d, size_t chan) const;
    void set_antenna(Dir d, const std::string& name, size_t chan);
    std::string antenna(Dir d, size_t chan) const;
    std::vector<std::string> antennas(Dir d, size_t chan) const;
    void set_dc_offset(bool enable, size_t chan);
    void set_iq_balance(bool enable, size_t chan);
    size_t num_channels(Dir d) const;
    std::map<std::string, std::string> sensors(Dir d, size_t chan) const;

    // Motherboard
    void set_master_clock_rate(double rate);
    double master_clock_rate() const;
    void set_clock_source(const std::string& name);
    std::string clock_source() const;
    std::vector<std::string> clock_sources() const;
    void set_time_source(const std::string& name);
    std::string time_source() const;
    std::vector<std::string> time_sources() const;
    double time_now() const;
    void set_time_now(double seconds);
    void set_time_next_pps(double seconds);
    std::map<std::string, std::string> mboard_sensors() const;
    std::map<std::string, std::string> info() const;

protected:
    HardwareError not_open_error() const override;

private:
    void rx_loop(size_t max_samps);
    void tx_loop(size_t max_samps);

    std::thread rx_thread_, tx_thread_;
    std::atomic<bool> rx_running_{false}, rx_stop_{false}, tx_running_{false}, tx_stop_{false};
    std::atomic<uint64_t> rx_overflows_{0}, rx_lost_{0}, tx_async_errors_{0};
    std::atomic<double> rx_rate_{0.0};        // what the receive timestamps are counted in
    std::atomic<bool> rx_clock_moved_{false}; // the rate or the device time changed: don't compare timestamps across it
    mutable std::mutex err_m_;
    std::string rx_error_, tx_error_;
    std::array<hw::TxQueue, MAX_TX_SLOTS> tx_;
    std::vector<size_t> rx_channels_{0}, tx_channels_{0};
    // Serialises control calls with the streaming threads' use of the same handle during open/close
    mutable std::mutex ctl_;
};
