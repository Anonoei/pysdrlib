#include "uhd_device.hpp"

#include <algorithm>
#include <chrono>
#include <sstream>

#include "hardware_convert.hpp"

namespace {

constexpr size_t TEXT = 4096;   // room for a list or a sensor dump from the shim

std::vector<std::string> lines(const std::string& text) {
    std::vector<std::string> out;
    std::istringstream in(text);
    for (std::string line; std::getline(in, line);)
        if (!line.empty()) out.push_back(line);
    return out;
}

std::map<std::string, std::string> key_values(const std::string& text, char sep) {
    std::map<std::string, std::string> out;
    for (const auto& line : lines(text)) {
        const size_t at = line.find(sep);
        if (at == std::string::npos) continue;
        std::string key = line.substr(0, at), value = line.substr(at + 1);
        const size_t start = value.find_first_not_of(' ');
        out[key] = start == std::string::npos ? "" : value.substr(start);
    }
    return out;
}

const SampleFormat SC16{SampleKind::Int, 16, 16, false};

}  // namespace

void uhd_check(int code, const ushim* s, const char* what) {
    if (code == 0) return;
    std::string message = what;
    if (s && ushim_error(s)[0]) message += std::string(": ") + ushim_error(s);
    throw HardwareError(code, message);
}

std::vector<UHDAddress> uhd_find(const std::string& args) {
    char text[TEXT];
    text[0] = 0;
    const int code = ushim_find(args.c_str(), text, sizeof text);
    if (code) throw HardwareError(code, "uhd_usrp_find failed");
    std::vector<UHDAddress> found;
    for (const auto& line : lines(text)) {   // "type=b200,name=...,serial=...": the shim prints UHD's address strings
        UHDAddress address;
        std::istringstream in(line);
        for (std::string pair; std::getline(in, pair, ',');) {
            const size_t eq = pair.find('=');
            if (eq != std::string::npos) address[pair.substr(0, eq)] = pair.substr(eq + 1);
        }
        found.push_back(address);
    }
    return found;
}

// --- UHDDevice ------------------------------------------------------------- //

UHDDevice::~UHDDevice() {
    try { close(); } catch (...) {}
}

HardwareError UHDDevice::not_open_error() const {
    return HardwareError(UHD_PY_NOT_OPEN, "USRP device is not open");
}

void UHDDevice::open(const std::string& args) {
    close();
    if (uhd_find(args).empty()) throw HardwareError(UHD_PY_NOT_FOUND, "No USRP found" + (args.empty() ? "" : " for '" + args + "'"));
    char err[512] = {0};
    ushim* s = nullptr;
    const int code = ushim_open(args.c_str(), &s, err, sizeof err);
    if (code) throw HardwareError(code, std::string("uhd_usrp_make failed") + (err[0] ? std::string(": ") + err : ""));
    handle_ = s;
}

void UHDDevice::close() {
    if (!handle_) return;
    stop_tx();
    stop_rx();
    ushim* s = handle_;
    handle_ = nullptr;
    rx_callback_ = nullptr;   // no thread can call it now
    ushim_close(s);
}

std::string UHDDevice::rx_error() const {
    std::lock_guard<std::mutex> lock(err_m_);
    return rx_error_;
}
std::string UHDDevice::tx_error() const {
    std::lock_guard<std::mutex> lock(err_m_);
    return tx_error_;
}

// ---- receive ---- //

static void check_channels(const std::vector<size_t>& channels, size_t available, size_t slots, const char* what) {
    if (channels.empty() || channels.size() > slots)
        throw HardwareError(UHD_PY_INVALID, std::string(what) + ": 1-" + std::to_string(slots) + " channels");
    for (size_t i = 0; i < channels.size(); i++) {
        if (channels[i] >= available)
            throw HardwareError(UHD_PY_INVALID, std::string(what) + ": no channel " + std::to_string(channels[i]));
        for (size_t j = 0; j < i; j++)
            if (channels[j] == channels[i]) throw HardwareError(UHD_PY_INVALID, std::string(what) + ": a channel is listed twice");
    }
}

void UHDDevice::set_rx_channels(const std::vector<size_t>& channels) {
    check_channels(channels, num_channels(Dir::RX), MAX_RX_SLOTS, "rx channels");
    if (rx_running_.load()) throw HardwareError(UHD_PY_INVALID, "stop_rx() before changing the rx channels");
    rx_channels_ = channels;
}

void UHDDevice::set_tx_channels(const std::vector<size_t>& channels) {
    check_channels(channels, num_channels(Dir::TX), MAX_TX_SLOTS, "tx channels");
    if (tx_running_.load()) throw HardwareError(UHD_PY_INVALID, "stop_tx() before changing the tx channels");
    tx_channels_ = channels;
}

void UHDDevice::start_rx() {
    ushim* s = require();
    if (rx_running_.load()) return;
    if (rx_thread_.joinable()) rx_thread_.join();   // a thread that stopped itself on an error
    if (rx_slots() < rx_channels_.size())
        throw HardwareError(UHD_PY_INVALID, "set_rx_buffer(samples, slots) needs a slot for each rx channel");
    {
        double r = 0;
        ushim_rate(s, static_cast<int>(Dir::RX), rx_channels_[0], &r);
        rx_rate_ = r;
        rx_clock_moved_ = true;
    }
    size_t max_samps = 0;
    uhd_check(ushim_rx_open(s, rx_channels_.data(), rx_channels_.size(), &max_samps), s, "rx stream");
    int code = ushim_rx_start(s);
    if (code) {
        ushim_rx_close(s);
        uhd_check(code, s, "start rx stream");
    }
    {
        std::lock_guard<std::mutex> lock(err_m_);
        rx_error_.clear();
    }
    rx_stop_ = false;
    rx_running_ = true;
    rx_thread_ = std::thread([this, max_samps] { rx_loop(max_samps); });
}

void UHDDevice::stop_rx() {
    require();   // a closed device is an error, like the other drivers'
    if (!rx_thread_.joinable() && !rx_running_.load()) return;
    // Stop the radio first and let the thread read what is still in flight until the stream goes quiet: packets left
    // behind would reach the next stream (UHD's sequence errors, and a radio that stops answering after a clock change)
    if (handle_) ushim_rx_stop(handle_);
    rx_stop_ = true;
    if (rx_thread_.joinable()) rx_thread_.join();
    rx_running_ = false;
    if (handle_) ushim_rx_close(handle_);
}

void UHDDevice::rx_loop(size_t max_samps) {
    ushim* s = handle_;
    const size_t n = rx_channels_.size();
    std::vector<std::vector<int16_t>> bufs(n, std::vector<int16_t>(2 * max_samps));
    std::vector<void*> ptrs(n);
    for (size_t k = 0; k < n; k++) ptrs[k] = bufs[k].data();
    // Where the next packet should start (device time): a later one means samples were lost on the way (an overflow).
    // Every channel lost the same ones: their queues skip them, so readers see the gap and the channels stay aligned
    double next_time = 0;
    bool have_time = false;
    auto account = [&](size_t got) {
        double t = 0;
        const double rate = rx_rate_.load();
        if (rx_clock_moved_.exchange(false)) have_time = false;
        if (ushim_rx_time(s, &t) != 0 || rate <= 0) { have_time = false; return; }
        if (have_time) {
            const double missing = (t - next_time) * rate;
            if (missing > 0.5 && missing < 60 * rate) {
                const size_t lost = static_cast<size_t>(missing + 0.5);
                for (size_t k = 0; k < n; k++) skip_rx(lost, k);
                rx_lost_ += lost;
            }
        }
        next_time = t + static_cast<double>(got) / rate;
        have_time = true;
    };
    auto deliver = [&](size_t got) {   // every channel's samples, each to its slot; false: stop
        if (!got) return true;
        account(got);
        for (size_t k = 0; k < n; k++)
            if (dispatch_rx(bufs[k].data(), got * 4, bufs[k].size() * sizeof(int16_t), k) != 0) return false;
        return true;
    };
    std::chrono::steady_clock::time_point drain_until{};
    for (;;) {
        if (rx_stop_.load()) {   // draining: read what is in flight, deliver none of it
            const auto now = std::chrono::steady_clock::now();
            if (drain_until == std::chrono::steady_clock::time_point{}) drain_until = now + std::chrono::seconds(1);
            else if (now > drain_until) break;
        }
        size_t got = 0;
        const int code = ushim_rx_recv(s, ptrs.data(), max_samps, 0.1, &got);
        if (rx_stop_.load()) {
            if (code == USHIM_RX_TIMEOUT || (code != 0 && code != USHIM_RX_OVERFLOW)) break;   // quiet: done
            continue;
        }
        if (code == 0) {
            if (!deliver(got)) break;
        } else if (code == USHIM_RX_TIMEOUT) {
            continue;
        } else if (code == USHIM_RX_OVERFLOW) {
            ++rx_overflows_;   // samples were lost between the device and us; the next timestamp says how many
            if (!deliver(got)) break;
        } else {
            std::lock_guard<std::mutex> lock(err_m_);
            rx_error_ = ushim_error(s);
            break;
        }
    }
    rx_running_ = false;
}

// ---- transmit ---- //

void UHDDevice::start_tx() {
    ushim* s = require();
    if (tx_running_.load()) return;
    if (tx_thread_.joinable()) tx_thread_.join();
    if (tx_[0].capacity() == 0) throw HardwareError(UHD_PY_INVALID, "set_tx_buffer() before start_tx()");
    size_t max_samps = 0;
    uhd_check(ushim_tx_open(s, tx_channels_.data(), tx_channels_.size(), &max_samps), s, "tx stream");
    {
        std::lock_guard<std::mutex> lock(err_m_);
        tx_error_.clear();
    }
    for (size_t k = 0; k < tx_channels_.size(); k++) tx_[k].open();
    tx_stop_ = false;
    tx_running_ = true;
    tx_thread_ = std::thread([this, max_samps] { tx_loop(max_samps); });
}

void UHDDevice::stop_tx() {
    require();
    if (!tx_thread_.joinable() && !tx_running_.load()) return;
    tx_stop_ = true;
    for (auto& q : tx_) q.close();   // wakes writers and the transmit thread
    if (tx_thread_.joinable()) tx_thread_.join();
    tx_running_ = false;
    if (handle_) ushim_tx_close(handle_);
}

void UHDDevice::tx_loop(size_t max_samps) {
    ushim* s = handle_;
    const size_t nch = tx_channels_.size();
    std::vector<std::vector<hw::cfloat>> in(nch, std::vector<hw::cfloat>(max_samps));
    std::vector<std::vector<int16_t>> out(nch, std::vector<int16_t>(2 * max_samps));
    std::vector<const void*> ptrs(nch);
    for (size_t k = 0; k < nch; k++) ptrs[k] = out[k].data();
    bool first = true;
    auto fail = [&] {
        std::lock_guard<std::mutex> lock(err_m_);
        tx_error_ = ushim_error(s);
    };
    // What every channel has queued: they go out together, so the channels stay aligned as they were written
    auto ready = [&] {
        size_t n = max_samps;
        for (size_t k = 0; k < nch; k++) n = std::min(n, tx_[k].available());
        return n;
    };
    while (!tx_stop_.load()) {
        size_t n = ready();
        if (n == 0) {   // wait a little for the slowest channel's writer
            for (int i = 0; i < 20 && n == 0 && !tx_stop_.load(); i++) {
                std::this_thread::sleep_for(std::chrono::milliseconds(1));
                n = ready();
            }
        }
        if (n == 0) {
            if (first || tx_stop_.load()) continue;   // nothing to send yet
            n = max_samps / 4;                        // keep the radio fed with silence rather than underflow
            for (size_t k = 0; k < nch; k++) {
                std::fill(in[k].begin(), in[k].begin() + n, hw::cfloat(0.f, 0.f));
                tx_[k].note_underrun(n);
            }
        } else {
            for (size_t k = 0; k < nch; k++) tx_[k].read(in[k].data(), n, 0.0);
        }
        for (size_t k = 0; k < nch; k++)
            hw::encode_samples(SC16, in[k].data(), n, reinterpret_cast<unsigned char*>(out[k].data()));
        size_t sent = 0;
        const int code = ushim_tx_send(s, ptrs.data(), n, 0.5, first ? 1 : 0, 0, &sent);
        if (code) { fail(); break; }
        first = false;
        if (ushim_tx_async(s) > 0) ++tx_async_errors_;
    }
    if (!first) {   // end the burst
        size_t sent = 0;
        ushim_tx_send(s, ptrs.data(), 0, 0.1, 0, 1, &sent);
    }
    tx_running_ = false;
}

// ---- controls ---- //

#define CTL() std::lock_guard<std::mutex> lock_(ctl_); ushim* s = require()
#define DIR(d) static_cast<int>(d)

void UHDDevice::set_rate(Dir d, double rate, size_t chan) {
    CTL();
    uhd_check(ushim_set_rate(s, DIR(d), rate, chan), s, "set rate");
    if (d == Dir::RX && !rx_channels_.empty() && chan == rx_channels_[0]) {
        double r = 0;
        if (ushim_rate(s, DIR(d), chan, &r) == 0) rx_rate_ = r;
        rx_clock_moved_ = true;
    }
}
double UHDDevice::rate(Dir d, size_t chan) const { CTL(); double v = 0; uhd_check(ushim_rate(s, DIR(d), chan, &v), s, "get rate"); return v; }
std::vector<std::vector<double>> UHDDevice::rate_ranges(Dir d, size_t chan) const {
    CTL();
    double flat[3 * 32];
    size_t n = 0;
    uhd_check(ushim_rate_ranges(s, DIR(d), chan, flat, 32, &n), s, "get rates");
    std::vector<std::vector<double>> out;
    for (size_t i = 0; i < n; i++) out.push_back({flat[3 * i], flat[3 * i + 1], flat[3 * i + 2]});
    return out;
}
void UHDDevice::set_freq(Dir d, double freq, size_t chan) { CTL(); uhd_check(ushim_set_freq(s, DIR(d), freq, chan), s, "set freq"); }
double UHDDevice::freq(Dir d, size_t chan) const { CTL(); double v = 0; uhd_check(ushim_freq(s, DIR(d), chan, &v), s, "get freq"); return v; }
std::pair<double, double> UHDDevice::freq_range(Dir d, size_t chan) const {
    CTL();
    double lo = 0, hi = 0;
    uhd_check(ushim_freq_range(s, DIR(d), chan, &lo, &hi), s, "get freq range");
    return {lo, hi};
}
void UHDDevice::set_gain(Dir d, double gain, size_t chan) { CTL(); uhd_check(ushim_set_gain(s, DIR(d), gain, chan), s, "set gain"); }
double UHDDevice::gain(Dir d, size_t chan) const { CTL(); double v = 0; uhd_check(ushim_gain(s, DIR(d), chan, &v), s, "get gain"); return v; }
std::vector<double> UHDDevice::gain_range(Dir d, size_t chan) const {
    CTL();
    double lo = 0, hi = 0, step = 0;
    uhd_check(ushim_gain_range(s, DIR(d), chan, &lo, &hi, &step), s, "get gain range");
    return {lo, hi, step};
}
void UHDDevice::set_agc(bool enable, size_t chan) { CTL(); uhd_check(ushim_set_agc(s, enable, chan), s, "set agc"); }
void UHDDevice::set_bandwidth(Dir d, double bw, size_t chan) { CTL(); uhd_check(ushim_set_bandwidth(s, DIR(d), bw, chan), s, "set bandwidth"); }
double UHDDevice::bandwidth(Dir d, size_t chan) const { CTL(); double v = 0; uhd_check(ushim_bandwidth(s, DIR(d), chan, &v), s, "get bandwidth"); return v; }
std::pair<double, double> UHDDevice::bandwidth_range(Dir d, size_t chan) const {
    CTL();
    double lo = 0, hi = 0;
    uhd_check(ushim_bandwidth_range(s, DIR(d), chan, &lo, &hi), s, "get bandwidth range");
    return {lo, hi};
}
void UHDDevice::set_antenna(Dir d, const std::string& name, size_t chan) { CTL(); uhd_check(ushim_set_antenna(s, DIR(d), name.c_str(), chan), s, "set antenna"); }
std::string UHDDevice::antenna(Dir d, size_t chan) const {
    CTL();
    char text[256] = {0};
    uhd_check(ushim_antenna(s, DIR(d), chan, text, sizeof text), s, "get antenna");
    return text;
}
std::vector<std::string> UHDDevice::antennas(Dir d, size_t chan) const {
    CTL();
    char text[TEXT] = {0};
    uhd_check(ushim_antennas(s, DIR(d), chan, text, sizeof text), s, "get antennas");
    return lines(text);
}
void UHDDevice::set_dc_offset(bool enable, size_t chan) { CTL(); uhd_check(ushim_set_dc_offset(s, enable, chan), s, "set dc offset"); }
void UHDDevice::set_iq_balance(bool enable, size_t chan) { CTL(); uhd_check(ushim_set_iq_balance(s, enable, chan), s, "set iq balance"); }
size_t UHDDevice::num_channels(Dir d) const { CTL(); size_t n = 0; uhd_check(ushim_num_channels(s, DIR(d), &n), s, "get channels"); return n; }
std::map<std::string, std::string> UHDDevice::sensors(Dir d, size_t chan) const {
    CTL();
    char text[TEXT] = {0};
    uhd_check(ushim_channel_sensors(s, DIR(d), chan, text, sizeof text), s, "get sensors");
    return key_values(text, '=');
}

void UHDDevice::set_master_clock_rate(double rate) { CTL(); uhd_check(ushim_set_master_clock_rate(s, rate), s, "set master clock rate"); rx_clock_moved_ = true; }
double UHDDevice::master_clock_rate() const { CTL(); double v = 0; uhd_check(ushim_master_clock_rate(s, &v), s, "get master clock rate"); return v; }
void UHDDevice::set_clock_source(const std::string& name) { CTL(); uhd_check(ushim_set_clock_source(s, name.c_str()), s, "set clock source"); }
std::string UHDDevice::clock_source() const { CTL(); char t[256] = {0}; uhd_check(ushim_clock_source(s, t, sizeof t), s, "get clock source"); return t; }
std::vector<std::string> UHDDevice::clock_sources() const { CTL(); char t[TEXT] = {0}; uhd_check(ushim_clock_sources(s, t, sizeof t), s, "get clock sources"); return lines(t); }
void UHDDevice::set_time_source(const std::string& name) { CTL(); uhd_check(ushim_set_time_source(s, name.c_str()), s, "set time source"); }
std::string UHDDevice::time_source() const { CTL(); char t[256] = {0}; uhd_check(ushim_time_source(s, t, sizeof t), s, "get time source"); return t; }
std::vector<std::string> UHDDevice::time_sources() const { CTL(); char t[TEXT] = {0}; uhd_check(ushim_time_sources(s, t, sizeof t), s, "get time sources"); return lines(t); }
double UHDDevice::time_now() const { CTL(); double v = 0; uhd_check(ushim_time_now(s, &v), s, "get time"); return v; }
void UHDDevice::set_time_now(double seconds) { CTL(); uhd_check(ushim_set_time_now(s, seconds), s, "set time"); rx_clock_moved_ = true; }
void UHDDevice::set_time_next_pps(double seconds) { CTL(); uhd_check(ushim_set_time_next_pps(s, seconds), s, "set time at pps"); rx_clock_moved_ = true; }
std::map<std::string, std::string> UHDDevice::mboard_sensors() const {
    CTL();
    char text[TEXT] = {0};
    uhd_check(ushim_mboard_sensors(s, text, sizeof text), s, "get mboard sensors");
    return key_values(text, '=');
}

std::map<std::string, std::string> UHDDevice::info() const {
    CTL();
    char text[TEXT] = {0};
    uhd_check(ushim_info(s, text, sizeof text), s, "get info");
    return key_values(text, '=');
}
