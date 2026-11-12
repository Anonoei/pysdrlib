#pragma once

#include <atomic>
#include <cstdint>
#include <map>
#include <mutex>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#include "libbladeRF.h"

#include "hardware_tx.hpp"
#include "hardware_wrapper.hpp"

// Error codes beyond libbladeRF's own (those are negative: BLADERF_ERR_*)
constexpr int BRF_PY_NOT_FOUND = 2000;
constexpr int BRF_PY_NOT_OPEN = 2001;
constexpr int BRF_PY_INVALID = 2002;
constexpr int BRF_PY_UNSUPPORTED_BOARD = 2003;

// Throws HardwareError(code, "<what>: <libbladeRF's message>") when `code` is a libbladeRF error (negative).
void bladerf_check(int code, const char* what);

using BladeRFInfo = std::map<std::string, std::string>;   // one device: serial, product, manufacturer, backend, bus, address, instance

// Attached bladeRFs, without opening any
std::vector<BladeRFInfo> bladerf_find();

enum class Dir : int { RX = 0, TX = 1 };

class BladeRFDevice : public HardwareDevice<bladerf*> {
public:
    BladeRFDevice() = default;
    ~BladeRFDevice() override;

    // identifier: a libbladeRF device string ("" the first one; "*:serial=ABC123"). Only the bladeRF 2.0 micro (AD9361) is
    // supported: another board is closed again and refused (BRF_PY_UNSUPPORTED_BOARD)
    void open(const std::string& identifier);
    void close() override;

    // SC16 Q11: interleaved signed 16-bit I/Q, 12 significant bits (+-2048 is full scale)
    SampleFormat rx_sample_format() const override { return {SampleKind::Int, 12, 16, false}; }

    // Receive: the stream carries `rx_channels` (default channel 0): one, or both (they are sampled together: their
    // indices line up). Channel k of the list goes to queue slot k (set_rx_buffer(samples, slots) first)
    void set_rx_channels(const std::vector<size_t>& channels);
    std::vector<size_t> rx_channels() const { return rx_channels_; }
    void start_rx() override;
    void stop_rx() override;
    bool is_streaming() const override { return rx_running_.load(); }
    uint64_t rx_overflows() const { return rx_overflows_.load(); }
    uint64_t rx_lost() const { return rx_lost_.load(); }   // samples the overflows lost (from the stream's timestamps)
    std::string rx_error() const;

    // Transmit: write complex64 per channel slot, the transmit thread encodes it. Both channels go out sample for
    // sample together: the thread sends only what every channel's queue has (silence for all when none has any)
    void set_tx_channels(const std::vector<size_t>& channels);
    std::vector<size_t> tx_channels() const { return tx_channels_; }
    void start_tx() override;
    void stop_tx() override;
    bool is_transmitting() const override { return tx_running_.load(); }
    std::string tx_error() const override;

    // Tuning and gain, per channel. The AD9361 has one synthesizer per direction, so a frequency is shared by a
    // direction's channels
    double set_rate(Dir d, double rate, size_t chan);   // returns the rate the hardware chose
    double rate(Dir d, size_t chan) const;
    std::vector<double> rate_range(Dir d, size_t chan) const;        // lo, hi, step
    void set_freq(Dir d, uint64_t freq, size_t chan);
    uint64_t freq(Dir d, size_t chan) const;
    std::vector<double> freq_range(Dir d, size_t chan) const;        // lo, hi, step
    void set_gain_stage(Dir d, const std::string& stage, int gain, size_t chan);
    int gain_stage(Dir d, const std::string& stage, size_t chan) const;
    std::vector<double> gain_stage_range(Dir d, const std::string& stage, size_t chan) const;   // lo, hi, step
    std::vector<std::string> gain_stages(Dir d, size_t chan) const;
    void set_gain_mode(Dir d, int mode, size_t chan);   // a bladerf_gain_mode; receive only
    int gain_mode(Dir d, size_t chan) const;
    double set_bandwidth(Dir d, double bandwidth, size_t chan);   // returns the bandwidth the hardware chose
    double bandwidth(Dir d, size_t chan) const;
    std::vector<double> bandwidth_range(Dir d, size_t chan) const;   // lo, hi, step
    void set_bias_tee(Dir d, bool enable, size_t chan);
    bool bias_tee(Dir d, size_t chan) const;
    size_t num_channels(Dir d) const;
    std::string rf_port(Dir d, size_t chan) const;
    std::vector<std::string> rf_ports(Dir d, size_t chan) const;
    void set_rf_port(Dir d, const std::string& port, size_t chan);

    // Board
    void set_clock_select(const std::string& name);   // "internal" or "external"
    std::string clock_select() const;
    void set_clock_output(bool enable);
    bool clock_output() const;
    std::map<std::string, std::string> sensors() const;   // rfic_temperature, pll_locked
    std::map<std::string, std::string> info() const;      // serial, board, fpga, firmware, usb_speed...

protected:
    HardwareError not_open_error() const override;

private:
    void rx_loop(size_t per_channel);
    void tx_loop(size_t per_channel);
    void sync_setup(Dir d, const std::vector<size_t>& channels);
    void module_off(Dir d, const std::vector<size_t>& channels);

    std::thread rx_thread_, tx_thread_;
    std::atomic<bool> rx_running_{false}, rx_stop_{false}, tx_running_{false}, tx_stop_{false};
    std::atomic<uint64_t> rx_overflows_{0}, rx_lost_{0};
    mutable std::mutex err_m_;
    std::string rx_error_, tx_error_;
    std::vector<size_t> rx_channels_{0}, tx_channels_{0};
    // Serialises control calls with close()
    mutable std::mutex ctl_;
};
