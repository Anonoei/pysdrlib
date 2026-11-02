#pragma once

// Generic, device-agnostic pieces shared by every C++ driver wrapper.
// Device-specific wrappers live next to their device, in <device>/lib/.

#include <cstdint>
#include <functional>
#include <stdexcept>
#include <string>

// Error carrying the vendor library's numeric error code.
class HardwareError : public std::runtime_error {
public:
    HardwareError(int code, const std::string& what)
        : std::runtime_error(what), code_(code) {}
    int code() const noexcept { return code_; }
private:
    int code_;
};

// Layout of one component (I or Q) of the raw stream a device delivers. Devices differ:
// 8-bit ints, 12-bit values in 16-bit words, 12-bit packed, offset-binary unsigned, floats...
// Mirrors pysdrlib.base.convert.SampleFormat, which does the conversion to complex64.
enum class SampleKind { Int, UInt, Float };

struct SampleFormat {
    SampleKind kind;
    int bits;          // significant bits per component (e.g. 12)
    int storage_bits;  // bits a component occupies in memory (8/16/32/64); == bits when packed
    bool packed = false;

    bool operator==(const SampleFormat& o) const {
        return kind == o.kind && bits == o.bits && storage_bits == o.storage_bits && packed == o.packed;
    }
};

// Owns a native device handle. Child classes implement open/close and the
// device-specific controls; they report failures by throwing HardwareError.
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

protected:
    HandleT handle_ = nullptr;
};
