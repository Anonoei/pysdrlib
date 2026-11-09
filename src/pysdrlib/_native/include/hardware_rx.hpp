#pragma once

// The receive path every native device shares, run entirely on the driver's streaming thread with no
// Python involved: raw samples -> complex64 -> a ring the consumer drains. Because it never needs the GIL,
// a busy Python thread (a GUI redraw, say) cannot make the driver's callback late and overflow the
// hardware's own buffers.
//
//     RxPipeline rx;
//     rx.configure(format, capacity);        // size the ring (discards pending samples)
//     rx.push(data, valid_bytes);            // driver thread, each transfer
//     rx.read(dst, max_count);               // consumer
//
// The conversion is hardware_convert.hpp's and the ring is hardware_ring.hpp's.

#include <atomic>
#include <stdexcept>

#include "hardware_convert.hpp"
#include "hardware_format.hpp"
#include "hardware_ring.hpp"

namespace hw {

class RxPipeline {
public:
    // Sets the capacity in samples (0 drops everything), discarding pending samples and `dropped`. The
    // first call also fixes the layout, which a device never changes; it enables the pipeline.
    void configure(const SampleFormat& format, size_t capacity) {
        if (enabled_) {
            if (!(format == format_)) throw std::logic_error("the sample format of a receiving device cannot change");
        } else {
            check_format(format);
            format_ = format;
        }
        ring_.reset(capacity);
        enabled_.store(true, std::memory_order_release);
    }

    bool enabled() const { return enabled_.load(std::memory_order_acquire); }

    void reset_stats() { ring_.reset_stats(); }

    // The driver's thread: convert one transfer (`valid_bytes` of raw data) into the ring
    void push(const void* data, size_t valid_bytes) noexcept {
        const size_t bps = format_.bytes_per_sample();
        const unsigned char* src = static_cast<const unsigned char*>(data);
        ring_.push(valid_bytes / bps, [&](cfloat* dst, size_t first, size_t count) {   // whole samples only
            decode_samples(format_, src + first * bps, count, dst);
        });
    }

    size_t available() const { return ring_.available(); }
    size_t read(cfloat* dst, size_t max_count, uint64_t* first = nullptr) { return ring_.read(dst, max_count, first); }
    size_t wait(size_t n, double timeout) { return ring_.wait(n, timeout); }
    RingStats stats() const { return ring_.stats(); }

private:
    Ring ring_;
    std::atomic<bool> enabled_{false};
    SampleFormat format_{SampleKind::Float, 32, 32, false};   // fixed once enabled
};

}  // namespace hw
