#pragma once

// A thread-safe ring of complex64 samples between a producer (a driver's streaming thread) and a consumer.
// The one implementation behind both the devices' receive path (hardware_rx.hpp) and the Python
// pysdrlib.base.buffer.RotatingBuffer.
//
//     Ring ring;
//     ring.reset(capacity);                  // size it (discards pending samples and `dropped`)
//     ring.write(samples, n);                // producer: never blocks on the consumer
//     ring.read(dst, max_count);             // consumer: oldest first, removes what it returns
//
// If the consumer falls behind by more than the capacity, the OLDEST pending samples are dropped, so a
// read is always the most recent data; `dropped` counts them.
//
// The ring also keeps count of what passed through it (see RingStats), for the stream watchdog.

#include <algorithm>
#include <chrono>
#include <complex>
#include <cstdint>
#include <cstring>
#include <memory>
#include <mutex>

namespace hw {

using cfloat = std::complex<float>;

// What the ring has seen, read in one go so the numbers agree with each other
struct RingStats {
    uint64_t samples = 0;      // written since reset_stats()
    uint64_t dropped = 0;      // discarded because the consumer fell behind
    uint64_t pending = 0;      // waiting to be read
    uint64_t max_chunk = 0;    // samples in the largest write
    uint64_t calls = 0;        // writes
    double busy = 0;           // seconds spent in them (including converting, for the receive path)
    bool seen = false;         // any write since reset_stats()
    double age = 0;            // seconds since the last write (when `seen`)
};

class Ring {
public:
    using clock = std::chrono::steady_clock;

    // Capacity in samples (0 drops everything). Discards pending samples and clears `dropped`.
    void reset(size_t capacity) {
        // Plain floats, not `new cfloat[n]`: std::complex's constructor would zero all of it (a 40 MB memset).
        // std::complex<float> is layout-compatible with float[2], so the ring views it as complex.
        std::unique_ptr<float[]> storage(capacity ? new float[2 * capacity] : nullptr);
        std::lock_guard<std::mutex> lock(m_);
        buf_ = std::move(storage);
        cap_ = capacity;
        head_ = pending_ = 0;
        dropped_ = 0;
    }

    // Forget the counts (and `dropped`), keeping pending samples and the capacity
    void reset_stats() {
        std::lock_guard<std::mutex> lock(m_);
        samples_ = max_chunk_ = calls_ = dropped_ = 0;
        busy_ = 0;
        seen_ = false;
    }

    // Producer: append `n` samples. `fill(dst, first, count)` must write samples [first, first + count) of
    // the n to `dst`; it may be called twice (the ring wraps) and not at all for samples that are dropped
    // unseen. Runs under the ring's lock.
    template <typename Fill>
    void push(size_t n, Fill&& fill) {
        const auto start = clock::now();
        std::lock_guard<std::mutex> lock(m_);
        if (cap_ == 0) {
            dropped_ += n;
        } else if (n >= cap_) {   // this write alone fills the ring
            dropped_ += pending_ + (n - cap_);
            fill(samples(), n - cap_, cap_);
            head_ = 0;
            pending_ = cap_;
        } else {
            const size_t first = std::min(n, cap_ - head_);
            fill(samples() + head_, 0, first);
            if (n > first) fill(samples(), first, n - first);   // wrapped
            head_ = (head_ + n) % cap_;
            size_t pending = pending_ + n;
            if (pending > cap_) {
                dropped_ += pending - cap_;
                pending = cap_;
            }
            pending_ = pending;
        }
        samples_ += n;
        max_chunk_ = std::max<uint64_t>(max_chunk_, n);
        ++calls_;
        seen_ = true;
        last_ = clock::now();
        busy_ += std::chrono::duration<double>(last_ - start).count();
    }

    void write(const cfloat* samples, size_t n) {
        push(n, [samples](cfloat* dst, size_t first, size_t count) {
            if (count) std::memcpy(dst, samples + first, count * sizeof(cfloat));
        });
    }

    size_t available() const {
        std::lock_guard<std::mutex> lock(m_);
        return pending_;
    }
    size_t capacity() const {
        std::lock_guard<std::mutex> lock(m_);
        return cap_;
    }
    uint64_t dropped() const {
        std::lock_guard<std::mutex> lock(m_);
        return dropped_;
    }
    // Return the dropped count and reset it to 0
    uint64_t clear_dropped() {
        std::lock_guard<std::mutex> lock(m_);
        const uint64_t d = dropped_;
        dropped_ = 0;
        return d;
    }

    // Consumer: move up to `max_count` of the oldest pending samples to `dst`; returns how many
    size_t read(cfloat* dst, size_t max_count) {
        std::lock_guard<std::mutex> lock(m_);
        const size_t take = std::min(max_count, pending_);
        if (take == 0) return 0;
        const size_t start = (head_ + cap_ - pending_) % cap_;
        const size_t first = std::min(take, cap_ - start);
        std::memcpy(dst, samples() + start, first * sizeof(cfloat));
        if (take > first) std::memcpy(dst + first, samples(), (take - first) * sizeof(cfloat));
        pending_ -= take;
        return take;
    }

    RingStats stats() const {
        std::lock_guard<std::mutex> lock(m_);
        RingStats s;
        s.samples = samples_;
        s.dropped = dropped_;
        s.pending = pending_;
        s.max_chunk = max_chunk_;
        s.calls = calls_;
        s.busy = busy_;
        s.seen = seen_;
        s.age = seen_ ? std::chrono::duration<double>(clock::now() - last_).count() : 0.0;
        return s;
    }

private:
    cfloat* samples() { return reinterpret_cast<cfloat*>(buf_.get()); }

    mutable std::mutex m_;
    std::unique_ptr<float[]> buf_;   // 2 * cap_ floats: cap_ complex samples
    size_t cap_ = 0, head_ = 0, pending_ = 0;   // capacity; next write; unread
    uint64_t dropped_ = 0, samples_ = 0, max_chunk_ = 0, calls_ = 0;
    double busy_ = 0;
    bool seen_ = false;
    clock::time_point last_;
};

}  // namespace hw
