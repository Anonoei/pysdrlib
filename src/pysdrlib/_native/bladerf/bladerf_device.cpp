#include "bladerf_device.hpp"

#include <algorithm>
#include <chrono>
#include <cstring>

#include "hardware_convert.hpp"

namespace {

const SampleFormat SC16_Q11{SampleKind::Int, 12, 16, false};

constexpr unsigned STREAM_TIMEOUT_MS = 3500;   // libbladeRF's own transfers give up after this
constexpr unsigned POLL_MS = 100;              // how long a stream thread waits before it looks at its stop flag
constexpr unsigned NUM_BUFFERS = 32;           // libbladeRF's stream buffers ...
constexpr unsigned BUFFER_SAMPLES = 16384;     // ... of this many samples (a multiple of 1024) ...
constexpr unsigned NUM_TRANSFERS = 16;         // ... of which this many are in flight on USB at once
constexpr size_t CHUNK = 8192;                 // samples per channel one stream thread call handles

bladerf_channel channel(Dir d, size_t chan) {
    return d == Dir::RX ? BLADERF_CHANNEL_RX(static_cast<int>(chan)) : BLADERF_CHANNEL_TX(static_cast<int>(chan));
}

// A bladerf_range in the units it describes: {lo, hi, step}
std::vector<double> range_values(const bladerf_range* r) {
    const double scale = r->scale != 0.f ? r->scale : 1.0;
    return {static_cast<double>(r->min) * scale, static_cast<double>(r->max) * scale, static_cast<double>(r->step) * scale};
}

}  // namespace

void bladerf_check(int code, const char* what) {
    if (code >= 0) return;
    throw HardwareError(code, std::string(what) + ": " + bladerf_strerror(code));
}

std::vector<BladeRFInfo> bladerf_find() {
    bladerf_devinfo* list = nullptr;
    const int n = bladerf_get_device_list(&list);
    std::vector<BladeRFInfo> found;
    if (n == BLADERF_ERR_NODEV) return found;   // none attached is an empty list, not a failure
    bladerf_check(n, "bladerf_get_device_list");
    for (int i = 0; i < n; i++) {
        BladeRFInfo info;
        info["serial"] = list[i].serial;
        info["product"] = list[i].product;
        info["manufacturer"] = list[i].manufacturer;
        info["backend"] = bladerf_backend_str(list[i].backend);
        info["usb_bus"] = std::to_string(list[i].usb_bus);
        info["usb_addr"] = std::to_string(list[i].usb_addr);
        info["instance"] = std::to_string(list[i].instance);
        found.push_back(info);
    }
    if (list) bladerf_free_device_list(list);
    return found;
}

// --- BladeRFDevice --------------------------------------------------------- //

BladeRFDevice::~BladeRFDevice() {
    try { close(); } catch (...) {}
}

HardwareError BladeRFDevice::not_open_error() const {
    return HardwareError(BRF_PY_NOT_OPEN, "bladeRF device is not open");
}

void BladeRFDevice::open(const std::string& identifier) {
    close();
    bladerf* dev = nullptr;
    const int code = bladerf_open(&dev, identifier.empty() ? nullptr : identifier.c_str());
    if (code == BLADERF_ERR_NODEV)
        throw HardwareError(BRF_PY_NOT_FOUND, "No bladeRF found" + (identifier.empty() ? "" : " for '" + identifier + "'"));
    bladerf_check(code, "bladerf_open");
    const std::string board = bladerf_get_board_name(dev);
    if (board != "bladerf2") {
        bladerf_close(dev);
        throw HardwareError(BRF_PY_UNSUPPORTED_BOARD, "This is a " + board + ": only the bladeRF 2.0 micro is supported");
    }
    handle_ = dev;
}

void BladeRFDevice::close() {
    if (!handle_) return;
    try { stop_tx(); } catch (...) {}
    try { stop_rx(); } catch (...) {}
    std::lock_guard<std::mutex> lock(ctl_);
    bladerf* dev = handle_;
    handle_ = nullptr;
    rx_callback_ = nullptr;   // no thread can call it now
    bladerf_close(dev);
}

std::string BladeRFDevice::rx_error() const {
    std::lock_guard<std::mutex> lock(err_m_);
    return rx_error_;
}
std::string BladeRFDevice::tx_error() const {
    std::lock_guard<std::mutex> lock(err_m_);
    return tx_error_;
}

static void check_channels(const std::vector<size_t>& channels, size_t available, const char* what) {
    if (channels.empty() || channels.size() > 2)
        throw HardwareError(BRF_PY_INVALID, std::string(what) + ": one or two channels");
    for (size_t i = 0; i < channels.size(); i++) {
        if (channels[i] >= available)
            throw HardwareError(BRF_PY_INVALID, std::string(what) + ": no channel " + std::to_string(channels[i]));
        for (size_t j = 0; j < i; j++)
            if (channels[j] == channels[i]) throw HardwareError(BRF_PY_INVALID, std::string(what) + ": a channel is listed twice");
    }
    // libbladeRF streams one channel, or both together as an interleaved pair: 0 then 1
    if (channels.size() == 2 && channels[0] != 0)
        throw HardwareError(BRF_PY_INVALID, std::string(what) + ": two channels are streamed as (0, 1)");
}

void BladeRFDevice::set_rx_channels(const std::vector<size_t>& channels) {
    check_channels(channels, num_channels(Dir::RX), "rx channels");
    if (rx_running_.load()) throw HardwareError(BRF_PY_INVALID, "stop_rx() before changing the rx channels");
    rx_channels_ = channels;
}

void BladeRFDevice::set_tx_channels(const std::vector<size_t>& channels) {
    check_channels(channels, num_channels(Dir::TX), "tx channels");
    if (tx_running_.load()) throw HardwareError(BRF_PY_INVALID, "stop_tx() before changing the tx channels");
    tx_channels_ = channels;
}

// libbladeRF's synchronous interface for one direction: configured, then the channels switched on. (It wants the
// stream configured before a module is enabled.)
void BladeRFDevice::sync_setup(Dir d, const std::vector<size_t>& channels) {
    bladerf* dev = require();
    const bool two = channels.size() > 1;
    const bladerf_channel_layout layout = d == Dir::RX ? (two ? BLADERF_RX_X2 : BLADERF_RX_X1)
                                                       : (two ? BLADERF_TX_X2 : BLADERF_TX_X1);
    // Receive carries metadata (a timestamp per message): that is how lost samples are counted. Transmit does not
    const bladerf_format format = d == Dir::RX ? BLADERF_FORMAT_SC16_Q11_META : BLADERF_FORMAT_SC16_Q11;
    bladerf_check(bladerf_sync_config(dev, layout, format, NUM_BUFFERS, BUFFER_SAMPLES, NUM_TRANSFERS, STREAM_TIMEOUT_MS),
                  "bladerf_sync_config");
    size_t done = 0;
    for (; done < channels.size(); done++) {
        const int code = bladerf_enable_module(dev, channel(d, channels[done]), true);
        if (code < 0) {
            for (size_t k = 0; k < done; k++) bladerf_enable_module(dev, channel(d, channels[k]), false);
            bladerf_check(code, "bladerf_enable_module");
        }
    }
}

void BladeRFDevice::module_off(Dir d, const std::vector<size_t>& channels) {
    if (!handle_) return;
    for (size_t ch : channels) bladerf_enable_module(handle_, channel(d, ch), false);
}

// ---- receive ---- //

void BladeRFDevice::start_rx() {
    require();
    if (rx_running_.load()) return;
    if (rx_thread_.joinable()) rx_thread_.join();   // a thread that stopped itself on an error
    if (rx_slots() < rx_channels_.size())
        throw HardwareError(BRF_PY_INVALID, "set_rx_buffer(samples, slots) needs a slot for each rx channel");
    sync_setup(Dir::RX, rx_channels_);
    {
        std::lock_guard<std::mutex> lock(err_m_);
        rx_error_.clear();
    }
    rx_stop_ = false;
    rx_running_ = true;
    rx_thread_ = std::thread([this] { rx_loop(CHUNK); });
}

void BladeRFDevice::stop_rx() {
    require();   // a closed device is an error, like the other drivers'
    if (!rx_thread_.joinable() && !rx_running_.load()) return;
    rx_stop_ = true;
    if (rx_thread_.joinable()) rx_thread_.join();   // it looks at the flag every POLL_MS
    rx_running_ = false;
    module_off(Dir::RX, rx_channels_);
}

void BladeRFDevice::rx_loop(size_t per_channel) {
    bladerf* dev = handle_;
    const size_t n = rx_channels_.size();
    std::vector<int16_t> raw(2 * n * per_channel);   // I, Q of channel 0, I, Q of channel 1... for each sample instant
    std::vector<std::vector<int16_t>> bufs(n, std::vector<int16_t>(2 * per_channel));
    bladerf_metadata meta;
    std::memset(&meta, 0, sizeof meta);
    meta.flags = BLADERF_META_FLAG_RX_NOW;   // read what is arriving, whatever its timestamp
    // Where the next call's first sample should be (the stream's sample counter): a later one means samples were lost on
    // the way (the host fell behind: an overrun). Every channel lost the same ones: their queues skip them, so readers
    // see the gap and the channels stay aligned
    uint64_t next_ts = 0;
    bool have_ts = false;
    while (!rx_stop_.load()) {
        const int code = bladerf_sync_rx(dev, raw.data(), static_cast<unsigned>(n * per_channel), &meta, POLL_MS);
        if (rx_stop_.load()) break;
        if (code == BLADERF_ERR_TIMEOUT) continue;
        if (code < 0) {
            std::lock_guard<std::mutex> lock(err_m_);
            rx_error_ = std::string("bladerf_sync_rx: ") + bladerf_strerror(code);
            break;
        }
        const size_t got = meta.actual_count / n;   // samples per channel (the count is of every channel's)
        if (meta.status & BLADERF_META_STATUS_OVERRUN) ++rx_overflows_;
        if (have_ts && meta.timestamp > next_ts) {
            const uint64_t lost = meta.timestamp - next_ts;
            if (lost < (1ull << 40)) {   // a counter that restarted is not a loss
                for (size_t k = 0; k < n; k++) skip_rx(static_cast<size_t>(lost), k);
                rx_lost_ += lost;
            }
        }
        next_ts = meta.timestamp + got;
        have_ts = true;
        if (!got) continue;
        bool keep = true;
        if (n == 1) {
            keep = dispatch_rx(raw.data(), got * 4, per_channel * 4, 0) == 0;
        } else {
            for (size_t i = 0; i < got; i++)   // split the interleaved pairs into one buffer per channel
                for (size_t k = 0; k < n; k++) {
                    bufs[k][2 * i] = raw[2 * (n * i + k)];
                    bufs[k][2 * i + 1] = raw[2 * (n * i + k) + 1];
                }
            for (size_t k = 0; k < n && keep; k++)
                keep = dispatch_rx(bufs[k].data(), got * 4, bufs[k].size() * sizeof(int16_t), k) == 0;
        }
        if (!keep) break;
    }
    rx_running_ = false;
}

// ---- transmit ---- //

void BladeRFDevice::start_tx() {
    require();
    if (tx_running_.load()) return;
    if (tx_thread_.joinable()) tx_thread_.join();
    if (tx_[0].capacity() == 0) throw HardwareError(BRF_PY_INVALID, "set_tx_buffer() before start_tx()");
    sync_setup(Dir::TX, tx_channels_);
    {
        std::lock_guard<std::mutex> lock(err_m_);
        tx_error_.clear();
    }
    for (size_t k = 0; k < tx_channels_.size(); k++) tx_[k].open();
    tx_stop_ = false;
    tx_running_ = true;
    tx_thread_ = std::thread([this] { tx_loop(CHUNK); });
}

void BladeRFDevice::stop_tx() {
    require();
    if (!tx_thread_.joinable() && !tx_running_.load()) return;
    tx_stop_ = true;
    for (auto& q : tx_) q.close();   // wakes writers and the transmit thread
    if (tx_thread_.joinable()) tx_thread_.join();
    tx_running_ = false;
    module_off(Dir::TX, tx_channels_);
}

void BladeRFDevice::tx_loop(size_t per_channel) {
    bladerf* dev = handle_;
    const size_t nch = tx_channels_.size();
    std::vector<std::vector<hw::cfloat>> in(nch, std::vector<hw::cfloat>(per_channel));
    std::vector<std::vector<int16_t>> enc(nch, std::vector<int16_t>(2 * per_channel));
    std::vector<int16_t> out(2 * nch * per_channel);   // the channels' samples interleaved, as libbladeRF takes them
    bool first = true;
    // What every channel has queued: they go out together, so the channels stay aligned as they were written
    auto ready = [&] {
        size_t n = per_channel;
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
            n = per_channel / 4;                      // keep the radio fed with silence rather than let it underrun
            for (size_t k = 0; k < nch; k++) {
                std::fill(in[k].begin(), in[k].begin() + n, hw::cfloat(0.f, 0.f));
                tx_[k].note_underrun(n);
            }
        } else {
            for (size_t k = 0; k < nch; k++) tx_[k].read(in[k].data(), n, 0.0);
        }
        for (size_t k = 0; k < nch; k++)
            hw::encode_samples(SC16_Q11, in[k].data(), n, reinterpret_cast<unsigned char*>(enc[k].data()));
        const int16_t* src = enc[0].data();
        if (nch > 1) {
            for (size_t i = 0; i < n; i++)
                for (size_t k = 0; k < nch; k++) {
                    out[2 * (nch * i + k)] = enc[k][2 * i];
                    out[2 * (nch * i + k) + 1] = enc[k][2 * i + 1];
                }
            src = out.data();
        }
        first = false;
        for (;;) {   // libbladeRF queues what it is given; a full queue times out, and the thread looks at its stop flag
            const int code = bladerf_sync_tx(dev, src, static_cast<unsigned>(n * nch), nullptr, POLL_MS);
            if (code == BLADERF_ERR_TIMEOUT && !tx_stop_.load()) continue;
            if (code < 0 && code != BLADERF_ERR_TIMEOUT) {
                std::lock_guard<std::mutex> lock(err_m_);
                tx_error_ = std::string("bladerf_sync_tx: ") + bladerf_strerror(code);
                tx_stop_ = true;
            }
            break;
        }
    }
    tx_running_ = false;
}

// ---- controls ---- //

#define CTL() std::lock_guard<std::mutex> lock_(ctl_); bladerf* dev = require()

double BladeRFDevice::set_rate(Dir d, double rate, size_t chan) {
    CTL();
    bladerf_sample_rate actual = 0;
    bladerf_check(bladerf_set_sample_rate(dev, channel(d, chan), static_cast<bladerf_sample_rate>(rate + 0.5), &actual), "bladerf_set_sample_rate");
    return actual;
}
double BladeRFDevice::rate(Dir d, size_t chan) const {
    CTL();
    bladerf_sample_rate v = 0;
    bladerf_check(bladerf_get_sample_rate(dev, channel(d, chan), &v), "bladerf_get_sample_rate");
    return v;
}
std::vector<double> BladeRFDevice::rate_range(Dir d, size_t chan) const {
    CTL();
    const bladerf_range* r = nullptr;
    bladerf_check(bladerf_get_sample_rate_range(dev, channel(d, chan), &r), "bladerf_get_sample_rate_range");
    return range_values(r);
}
void BladeRFDevice::set_freq(Dir d, uint64_t freq, size_t chan) {
    CTL();
    bladerf_check(bladerf_set_frequency(dev, channel(d, chan), freq), "bladerf_set_frequency");
}
uint64_t BladeRFDevice::freq(Dir d, size_t chan) const {
    CTL();
    bladerf_frequency v = 0;
    bladerf_check(bladerf_get_frequency(dev, channel(d, chan), &v), "bladerf_get_frequency");
    return v;
}
std::vector<double> BladeRFDevice::freq_range(Dir d, size_t chan) const {
    CTL();
    const bladerf_range* r = nullptr;
    bladerf_check(bladerf_get_frequency_range(dev, channel(d, chan), &r), "bladerf_get_frequency_range");
    return range_values(r);
}
void BladeRFDevice::set_gain_stage(Dir d, const std::string& stage, int gain, size_t chan) {
    CTL();
    bladerf_check(bladerf_set_gain_stage(dev, channel(d, chan), stage.c_str(), gain), "bladerf_set_gain_stage");
}
int BladeRFDevice::gain_stage(Dir d, const std::string& stage, size_t chan) const {
    CTL();
    int v = 0;
    bladerf_check(bladerf_get_gain_stage(dev, channel(d, chan), stage.c_str(), &v), "bladerf_get_gain_stage");
    return v;
}
std::vector<double> BladeRFDevice::gain_stage_range(Dir d, const std::string& stage, size_t chan) const {
    CTL();
    const bladerf_range* r = nullptr;
    bladerf_check(bladerf_get_gain_stage_range(dev, channel(d, chan), stage.c_str(), &r), "bladerf_get_gain_stage_range");
    return range_values(r);
}
std::vector<std::string> BladeRFDevice::gain_stages(Dir d, size_t chan) const {
    CTL();
    const char* names[8] = {nullptr};
    const int n = bladerf_get_gain_stages(dev, channel(d, chan), names, 8);
    bladerf_check(n, "bladerf_get_gain_stages");
    std::vector<std::string> out;
    for (int i = 0; i < n && i < 8; i++) out.emplace_back(names[i]);
    return out;
}
void BladeRFDevice::set_gain_mode(Dir d, int mode, size_t chan) {
    CTL();
    bladerf_check(bladerf_set_gain_mode(dev, channel(d, chan), static_cast<bladerf_gain_mode>(mode)), "bladerf_set_gain_mode");
}
int BladeRFDevice::gain_mode(Dir d, size_t chan) const {
    CTL();
    bladerf_gain_mode m = BLADERF_GAIN_DEFAULT;
    bladerf_check(bladerf_get_gain_mode(dev, channel(d, chan), &m), "bladerf_get_gain_mode");
    return static_cast<int>(m);
}
double BladeRFDevice::set_bandwidth(Dir d, double bandwidth, size_t chan) {
    CTL();
    bladerf_bandwidth actual = 0;
    bladerf_check(bladerf_set_bandwidth(dev, channel(d, chan), static_cast<bladerf_bandwidth>(bandwidth + 0.5), &actual), "bladerf_set_bandwidth");
    return actual;
}
double BladeRFDevice::bandwidth(Dir d, size_t chan) const {
    CTL();
    bladerf_bandwidth v = 0;
    bladerf_check(bladerf_get_bandwidth(dev, channel(d, chan), &v), "bladerf_get_bandwidth");
    return v;
}
std::vector<double> BladeRFDevice::bandwidth_range(Dir d, size_t chan) const {
    CTL();
    const bladerf_range* r = nullptr;
    bladerf_check(bladerf_get_bandwidth_range(dev, channel(d, chan), &r), "bladerf_get_bandwidth_range");
    return range_values(r);
}
void BladeRFDevice::set_bias_tee(Dir d, bool enable, size_t chan) {
    CTL();
    bladerf_check(bladerf_set_bias_tee(dev, channel(d, chan), enable), "bladerf_set_bias_tee");
}
bool BladeRFDevice::bias_tee(Dir d, size_t chan) const {
    CTL();
    bool v = false;
    bladerf_check(bladerf_get_bias_tee(dev, channel(d, chan), &v), "bladerf_get_bias_tee");
    return v;
}
size_t BladeRFDevice::num_channels(Dir d) const {
    CTL();
    return bladerf_get_channel_count(dev, d == Dir::RX ? BLADERF_RX : BLADERF_TX);
}
std::string BladeRFDevice::rf_port(Dir d, size_t chan) const {
    CTL();
    const char* name = nullptr;
    bladerf_check(bladerf_get_rf_port(dev, channel(d, chan), &name), "bladerf_get_rf_port");
    return name ? name : "";
}
std::vector<std::string> BladeRFDevice::rf_ports(Dir d, size_t chan) const {
    CTL();
    const char* names[32] = {nullptr};
    const int n = bladerf_get_rf_ports(dev, channel(d, chan), names, 32);
    bladerf_check(n, "bladerf_get_rf_ports");
    std::vector<std::string> out;
    for (int i = 0; i < n && i < 32; i++) out.emplace_back(names[i]);
    return out;
}
void BladeRFDevice::set_rf_port(Dir d, const std::string& port, size_t chan) {
    CTL();
    bladerf_check(bladerf_set_rf_port(dev, channel(d, chan), port.c_str()), "bladerf_set_rf_port");
}

void BladeRFDevice::set_clock_select(const std::string& name) {
    CTL();
    if (name != "internal" && name != "external") throw HardwareError(BRF_PY_INVALID, "clock source: 'internal' or 'external'");
    bladerf_check(bladerf_set_clock_select(dev, name == "internal" ? CLOCK_SELECT_ONBOARD : CLOCK_SELECT_EXTERNAL), "bladerf_set_clock_select");
}
std::string BladeRFDevice::clock_select() const {
    CTL();
    bladerf_clock_select sel = CLOCK_SELECT_ONBOARD;
    bladerf_check(bladerf_get_clock_select(dev, &sel), "bladerf_get_clock_select");
    return sel == CLOCK_SELECT_ONBOARD ? "internal" : "external";
}
void BladeRFDevice::set_clock_output(bool enable) {
    CTL();
    bladerf_check(bladerf_set_clock_output(dev, enable), "bladerf_set_clock_output");
}
bool BladeRFDevice::clock_output() const {
    CTL();
    bool v = false;
    bladerf_check(bladerf_get_clock_output(dev, &v), "bladerf_get_clock_output");
    return v;
}

std::map<std::string, std::string> BladeRFDevice::sensors() const {
    CTL();
    std::map<std::string, std::string> out;
    float temp = 0;
    if (bladerf_get_rfic_temperature(dev, &temp) == 0) out["rfic_temperature"] = std::to_string(temp);
    bool locked = false;
    if (bladerf_get_pll_lock_state(dev, &locked) == 0) out["pll_locked"] = locked ? "true" : "false";
    return out;
}

std::map<std::string, std::string> BladeRFDevice::info() const {
    CTL();
    std::map<std::string, std::string> out;
    out["board"] = bladerf_get_board_name(dev);
    char serial[BLADERF_SERIAL_LENGTH] = {0};
    if (bladerf_get_serial(dev, serial) == 0) out["serial"] = serial;
    struct bladerf_version v;
    if (bladerf_fw_version(dev, &v) == 0) out["firmware"] = v.describe ? v.describe : "";
    if (bladerf_fpga_version(dev, &v) == 0) out["fpga"] = v.describe ? v.describe : "";
    bladerf_fpga_size size;
    if (bladerf_get_fpga_size(dev, &size) == 0) out["fpga_size"] = std::to_string(static_cast<int>(size)) + " kLE";
    switch (bladerf_device_speed(dev)) {
        case BLADERF_DEVICE_SPEED_SUPER: out["usb_speed"] = "super"; break;
        case BLADERF_DEVICE_SPEED_HIGH: out["usb_speed"] = "high"; break;
        default: out["usb_speed"] = "unknown";
    }
    struct bladerf_version lib;
    bladerf_version(&lib);
    out["library"] = lib.describe ? lib.describe : "";
    return out;
}
