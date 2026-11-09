#pragma once

// The transmit path a native device can share: a bounded queue of complex64 samples between the consumer of
// the stream (Python, which writes, blocking while the queue is full) and the driver's transmit thread (which
// reads). The mirror image of hardware_rx.hpp: Python hands over complex64, the driver's thread encodes to the
// hardware's format without the GIL.
//
//     TxQueue tx;
//     tx.configure(capacity);                  // size it (discards pending samples)
//     tx.write(src, n, timeout);               // producer: waits for room; returns how many it queued
//     tx.read(dst, max_count, timeout);        // driver thread: waits for samples; 0 on timeout or close
//     tx.close() / tx.open()                   // wake blocked callers when streaming stops / start again

#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <cstring>
#include <memory>
#include <mutex>

#include "hardware_ring.hpp"

namespace hw {

class TxQueue {
public:
    // Capacity in samples; discards pending samples and the counts
    void configure(size_t capacity) {
        std::unique_ptr<float[]> storage(capacity ? new float[2 * capacity] : nullptr);
        std::lock_guard<std::mutex> lock(m_);
        buf_ = std::move(storage);
        cap_ = capacity;
        head_ = tail_ = pending_ = 0;
        written_ = sent_ = underruns_ = 0;
    }

    void open() {
        std::lock_guard<std::mutex> lock(m_);
        closed_ = false;
    }
    // Wake everyone waiting; write() returns what it queued so far and read() returns 0 until open()
    void close() {
        {
            std::lock_guard<std::mutex> lock(m_);
            closed_ = true;
        }
        space_.notify_all();
        data_.notify_all();
    }

    // Queue up to `n` samples, waiting up to `timeout` seconds (negative: no limit) for room. Returns how many
    size_t write(const cfloat* src, size_t n, double timeout) {
        std::unique_lock<std::mutex> lock(m_);
        const auto deadline = clock::now() + std::chrono::duration_cast<clock::duration>(std::chrono::duration<double>(timeout < 0 ? 0 : timeout));
        size_t done = 0;
        while (done < n) {
            if (cap_ == 0) break;
            auto room = [&] { return closed_ || cap_ - pending_ > 0; };
            if (timeout < 0) space_.wait(lock, room);
            else if (!space_.wait_until(lock, deadline, room)) break;
            if (closed_) break;
            const size_t take = std::min(n - done, cap_ - pending_);
            const size_t first = std::min(take, cap_ - head_);
            std::memcpy(samples() + head_, src + done, first * sizeof(cfloat));
            if (take > first) std::memcpy(samples(), src + done + first, (take - first) * sizeof(cfloat));
            head_ = (head_ + take) % cap_;
            pending_ += take;
            written_ += take;
            done += take;
            data_.notify_all();
        }
        return done;
    }

    // Driver thread: up to `max_count` samples, waiting up to `timeout` seconds for at least one
    size_t read(cfloat* dst, size_t max_count, double timeout) {
        std::unique_lock<std::mutex> lock(m_);
        data_.wait_for(lock, std::chrono::duration<double>(timeout), [&] { return closed_ || pending_ > 0; });
        if (pending_ == 0) return 0;
        const size_t take = std::min(max_count, pending_);
        const size_t first = std::min(take, cap_ - tail_);
        std::memcpy(dst, samples() + tail_, first * sizeof(cfloat));
        if (take > first) std::memcpy(dst + first, samples(), (take - first) * sizeof(cfloat));
        tail_ = (tail_ + take) % cap_;
        pending_ -= take;
        sent_ += take;
        lock.unlock();
        space_.notify_all();
        return take;
    }

    // Driver thread: it had nothing to send for `n` samples and sent silence instead
    void note_underrun(size_t n) {
        std::lock_guard<std::mutex> lock(m_);
        underruns_ += n;
    }

    struct Stats { uint64_t written, sent, underruns, pending; };
    Stats stats() const {
        std::lock_guard<std::mutex> lock(m_);
        return {written_, sent_, underruns_, pending_};
    }
    size_t capacity() const {
        std::lock_guard<std::mutex> lock(m_);
        return cap_;
    }

private:
    using clock = std::chrono::steady_clock;
    cfloat* samples() { return reinterpret_cast<cfloat*>(buf_.get()); }

    mutable std::mutex m_;
    std::condition_variable space_, data_;
    std::unique_ptr<float[]> buf_;
    size_t cap_ = 0, head_ = 0, tail_ = 0, pending_ = 0;
    uint64_t written_ = 0, sent_ = 0, underruns_ = 0;
    bool closed_ = true;
};

}  // namespace hw
