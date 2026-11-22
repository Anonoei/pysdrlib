#pragma once

// A thread-safe ring of complex64 samples between a producer (a driver's streaming thread) and a consumer.
// The one implementation behind both the devices' receive path (hardware_rx.hpp) and the Python
// pysdrlib.base.buffer.RotatingBuffer.
//
//     Ring ring;
//     ring.reset(capacity);                  // size it (discards pending samples and `dropped`)
//     ring.write(samples, n);                // producer: never blocks on the consumer
//     ring.read(dst, max_count);             // consumer: oldest first, removes what it returns
//     ring.wait(n, timeout);                 // consumer: block until n samples are pending (or timeout)
//
// Every sample has an index: its position in everything written since reset(). read() can report the index of
// the first sample it returns, so a consumer sees exactly where samples were dropped (a jump in the index).
// skip(n) records samples lost before the ring (the hardware overflowed): they take n indices but no room, and a
// read never spans them, so the jump shows where they were.
//
// If the consumer falls behind by more than the capacity, the OLDEST pending samples are dropped, so a
// read is always the most recent data; `dropped` counts them.
//
// A read copies with the lock released, so a slow copy never holds up the producer. While a copy is in flight the
// producer can't overwrite the samples being copied: a write that would (the ring is full) drops the NEWEST samples
// that don't fit instead, as a gap (their indices are skipped, like skip()'s), and counts them in `dropped`.
//
// The ring also keeps count of what passed through it (see RingStats), for the stream watchdog.

#include <algorithm>
#include <chrono>
#include <complex>
#include <condition_variable>
#include <cstdint>
#include <cstring>
#include <deque>
#include <memory>
#include <mutex>
#include <utility>
#include <vector>

namespace hw {

using cfloat = std::complex<float>;

// What the ring has seen, read in one go so the numbers agree with each other
struct RingStats {
    uint64_t samples = 0;      // written since reset_stats()
    uint64_t total = 0;        // written since reset(): the index the next written sample gets
    uint64_t skipped = 0;      // lost before the ring (skip()) since reset_stats()
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
        std::lock_guard<std::mutex> reading(read_m_);   // not while a read is copying out of the old storage
        std::lock_guard<std::mutex> lock(m_);
        buf_ = std::move(storage);
        cap_ = capacity;
        head_ = pending_ = 0;
        dropped_ = 0;
        total_ = 0;
        written_ = gap_offset_ = 0;
        marks_.clear();
        stamps_.clear();
    }

    // Forget the counts (and `dropped`), keeping pending samples and the capacity
    void reset_stats() {
        std::lock_guard<std::mutex> lock(m_);
        samples_ = max_chunk_ = calls_ = dropped_ = skipped_ = 0;
        busy_ = 0;
        seen_ = false;
    }

    // Producer: append `n` samples. `fill(dst, first, count)` must write samples [first, first + count) of
    // the n to `dst`; it may be called twice (the ring wraps) and not at all for samples that are dropped
    // unseen. Runs under the ring's lock.
    template <typename Fill>
    void push(size_t n, Fill&& fill) {
        {
            std::lock_guard<std::mutex> lock(m_);
            push_locked(n, fill);
        }
        cv_.notify_all();
    }

    void write(const cfloat* samples, size_t n) {
        push(n, [samples](cfloat* dst, size_t first, size_t count) {
            if (count) std::memcpy(dst, samples + first, count * sizeof(cfloat));
        });
    }

    // Producer: `n` samples were lost before reaching the ring. They take indices (so the reader sees the jump) but
    // no room
    void skip(size_t n) {
        if (n == 0) return;
        std::lock_guard<std::mutex> lock(m_);
        marks_.emplace_back(written_, n);
        total_ += n;
        skipped_ += n;
    }

    // Producer: the next sample written (index `total`) was taken at time `t` by the radio's own clock (seconds).
    // Stamps queue (the last MAX_STAMPS) until the consumer takes them; it extrapolates from each at the sample rate
    static constexpr size_t MAX_STAMPS = 1024;
    void stamp(double t) {
        std::lock_guard<std::mutex> lock(m_);
        if (stamps_.size() >= MAX_STAMPS) stamps_.pop_front();
        stamps_.emplace_back(total_, t);
    }
    // Consumer: the stamps since the last call, oldest first
    std::vector<std::pair<uint64_t, double>> take_stamps() {
        std::lock_guard<std::mutex> lock(m_);
        std::vector<std::pair<uint64_t, double>> out(stamps_.begin(), stamps_.end());
        stamps_.clear();
        return out;
    }

    // Consumer: wait until at least `n` samples are pending (at most the capacity), for up to `timeout`
    // seconds (negative: no limit). Returns the number pending. Call without holding the GIL; callers that
    // must notice a stopped stream wait in short slices.
    size_t wait(size_t n, double timeout) {
        std::unique_lock<std::mutex> lock(m_);
        auto ready = [&] { return pending_ >= std::min(n, cap_); };   // a ring never holds more than its capacity
        if (timeout < 0) {
            cv_.wait(lock, ready);
        } else {
            cv_.wait_for(lock, std::chrono::duration<double>(timeout), ready);
        }
        return pending_;
    }

private:
    template <typename Fill>
    void push_locked(size_t n, Fill& fill) {
        const auto start = clock::now();
        size_t lost = 0;   // dropped newest: the slots they'd take are being copied by a read
        if (copying_ && written_ + n > copy_from_ + cap_) {
            lost = static_cast<size_t>(written_ + n - (copy_from_ + cap_));
            n -= lost;
        }
        if (cap_ == 0) {
            dropped_ += n;
        } else if (copying_) {   // fits in front of the copy: nothing pending is overwritten either (it is all after it)
            const size_t first = std::min(n, cap_ - head_);
            if (first) fill(samples() + head_, 0, first);
            if (n > first) fill(samples(), first, n - first);
            head_ = (head_ + n) % cap_;
            pending_ += n;
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
        written_ += n;
        if (lost) {
            marks_.emplace_back(written_, lost);
            dropped_ += lost;
            n += lost;
        }
        samples_ += n;
        total_ += n;
        max_chunk_ = std::max<uint64_t>(max_chunk_, n);
        ++calls_;
        seen_ = true;
        last_ = clock::now();
        busy_ += std::chrono::duration<double>(last_ - start).count();
    }

public:

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

    // Consumer: move up to `max_count` of the oldest pending samples to `dst`; returns how many.
    // `first_index`: set to the index of the first sample returned (or of the next one to come, if none)
    // The copy runs with the lock released (one reader at a time): the samples are claimed first, and the producer
    // keeps clear of them until it is done.
    size_t read(cfloat* dst, size_t max_count, uint64_t* first_index = nullptr) {
        std::lock_guard<std::mutex> reading(read_m_);
        size_t take, start;
        {
            std::lock_guard<std::mutex> lock(m_);
            const uint64_t at = written_ - pending_;    // write position of the oldest pending sample
            while (!marks_.empty() && marks_.front().first <= at) {   // gaps before it: their indices are behind us
                gap_offset_ += marks_.front().second;
                marks_.pop_front();
            }
            if (first_index) *first_index = at + gap_offset_;
            take = std::min(max_count, pending_);
            if (!marks_.empty()) take = std::min<uint64_t>(take, marks_.front().first - at);   // never across a gap
            if (take == 0) return 0;
            start = (head_ + cap_ - pending_) % cap_;
            pending_ -= take;
            copying_ = true;
            copy_from_ = at;
        }
        const size_t first = std::min(take, cap_ - start);
        std::memcpy(dst, samples() + start, first * sizeof(cfloat));
        if (take > first) std::memcpy(dst + first, samples(), (take - first) * sizeof(cfloat));
        std::lock_guard<std::mutex> lock(m_);
        copying_ = false;
        return take;
    }

    RingStats stats() const {
        std::lock_guard<std::mutex> lock(m_);
        RingStats s;
        s.samples = samples_;
        s.total = total_;
        s.skipped = skipped_;
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
    std::mutex read_m_;                          // one read at a time (taken before m_); reset() waits for it
    bool copying_ = false;                       // a read is copying samples from write position copy_from_ on
    uint64_t copy_from_ = 0;
    std::condition_variable cv_;
    std::unique_ptr<float[]> buf_;   // 2 * cap_ floats: cap_ complex samples
    size_t cap_ = 0, head_ = 0, pending_ = 0;   // capacity; next write; unread
    uint64_t dropped_ = 0, samples_ = 0, max_chunk_ = 0, calls_ = 0, total_ = 0, skipped_ = 0;
    uint64_t written_ = 0, gap_offset_ = 0;          // real samples written; gaps the reader has passed
    std::deque<std::pair<uint64_t, uint64_t>> marks_; // (write position, samples lost there) not yet passed
    double busy_ = 0;
    bool seen_ = false;
    clock::time_point last_;
    std::deque<std::pair<uint64_t, double>> stamps_;   // stamp(): (index, the radio's time of that sample)
};

}  // namespace hw
