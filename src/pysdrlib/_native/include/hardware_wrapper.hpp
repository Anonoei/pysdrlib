#pragma once

// Generic, device-agnostic pieces shared by every C++ driver wrapper.
// Device-specific wrappers live in their own folder next to this one: _native/<device>/.

#include <array>
#include <atomic>
#include <cstdint>
#include <functional>
#include <stdexcept>
#include <string>

#include "hardware_format.hpp"
#include "hardware_rx.hpp"

// Error carrying the vendor library's numeric error code.
class HardwareError : public std::runtime_error {
public:
    HardwareError(int code, const std::string& what)
        : std::runtime_error(what), code_(code) {}
    int code() const noexcept { return code_; }
private:
    int code_;
};

// Owns a native device handle. Child classes implement open/close, streaming and the device-specific
// controls; they report failures by throwing HardwareError. Everything the Python bindings need to
// treat devices alike (see hardware_pybind.hpp) is in this interface.
template <typename HandleT>
class HardwareDevice {
public:
    // Called from the driver's streaming thread with the device's raw bytes, laid out as
    // `rx_sample_format()` describes. Lengths are in bytes. Return 0 to keep streaming.
    using RxCallbackFn = std::function<int(const void* data, size_t valid_bytes, size_t buffer_bytes)>;

    HardwareDevice() = default;
    HardwareDevice(const HardwareDevice&) = delete;
    HardwareDevice& operator=(const HardwareDevice&) = delete;
    virtual ~HardwareDevice() = default;

    virtual void close() = 0;
    bool is_open() const { return handle_ != nullptr; }

    // Layout of the raw receive stream (what the RxCallbackFn data contains)
    virtual SampleFormat rx_sample_format() const = 0;

    // Raw tap: set a callback before start_rx() to receive the device's bytes yourself. It is dropped by
    // close(). With a callback set, the buffered path below is not used.
    void set_rx_callback(RxCallbackFn callback) { rx_callback_ = std::move(callback); }

    // Buffered receive (the default way to read a device): with no callback set, each transfer is
    // converted to complex64 and queued on the streaming thread, without Python. `samples` is how many the
    // queue holds before it drops the oldest.
    //
    // A device streaming several channels at once queues each in its own slot (slot k: the k-th channel of the
    // stream); everything else uses slot 0.
    static constexpr size_t MAX_RX_SLOTS = 8;
    void set_rx_buffer(size_t samples, size_t slots = 1) {
        if (slots < 1 || slots > MAX_RX_SLOTS) throw std::invalid_argument("rx slots must be 1-" + std::to_string(MAX_RX_SLOTS));
        for (size_t k = 0; k < MAX_RX_SLOTS; k++)
            if (k < slots || rx_[k].enabled()) rx_[k].configure(rx_sample_format(), k < slots ? samples : 0);
        rx_slots_ = slots;
    }
    size_t rx_slots() const { return rx_slots_; }
    void reset_rx_stats() { for (auto& r : rx_) r.reset_stats(); }
    size_t rx_available(size_t slot = 0) const { return rx_.at(slot).available(); }
    size_t read_rx(hw::cfloat* dst, size_t max_count, uint64_t* first = nullptr, size_t slot = 0) { return rx_.at(slot).read(dst, max_count, first); }
    // Block (GIL released by the binding) until `samples` are queued or `timeout` seconds pass; returns how many are
    size_t wait_rx(size_t samples, double timeout, size_t slot = 0) { return rx_.at(slot).wait(samples, timeout); }
    // The driver's thread: `samples` were lost before reaching us (the hardware said so); readers see the gap
    void skip_rx(size_t samples, size_t slot = 0) const { if (slot < MAX_RX_SLOTS) rx_[slot].skip(samples); }
    hw::RingStats rx_stats(size_t slot = 0) const { return rx_.at(slot).stats(); }

    virtual void start_rx() = 0;
    virtual void stop_rx() = 0;
    virtual bool is_streaming() const = 0;

    // What a driver's streaming thread calls with each buffer: hands it to the callback if there is one,
    // else queues it (see set_rx_buffer), else returns -1 (stop streaming). Public so tests can drive the
    // real path without hardware.
    int dispatch_rx(const void* data, size_t valid_bytes, size_t buffer_bytes, size_t slot = 0) const {
        if (slot == 0 && rx_callback_) return rx_callback_(data, valid_bytes, buffer_bytes);
        if (slot < MAX_RX_SLOTS && rx_[slot].enabled()) {
            rx_[slot].push(data, valid_bytes);
            return 0;
        }
        return -1;
    }

protected:
    // The handle, or a HardwareError (see not_open_error) if the device isn't open
    HandleT require() const {
        if (!handle_) throw not_open_error();
        return handle_;
    }
    virtual HardwareError not_open_error() const = 0;

    HandleT handle_ = nullptr;
    // Only safe to drop once no streaming thread can call it, i.e. after the driver has stopped
    RxCallbackFn rx_callback_;
    mutable std::array<hw::RxPipeline, MAX_RX_SLOTS> rx_;
    size_t rx_slots_ = 1;
};
