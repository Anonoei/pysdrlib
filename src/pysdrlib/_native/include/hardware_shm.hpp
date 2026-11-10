#pragma once

// A ring of complex64 samples in memory that several processes map: one writer (the remote server) and any number
// of readers, each with a cursor of its own, so a reader that falls behind loses samples and no other reader or the
// writer is slowed. The mapping itself (a named shared-memory segment, a file) is the caller's; this works on the
// bytes of it, with std::atomic so that it is correct across processes.
//
//     segment_size(capacity)                   // bytes the segment needs
//     ShmRing ring(base, size, capacity, id);  // writer: lays the header out in a zeroed segment
//     ShmRing ring(base, size);                // reader: checks the header
//     ring.publish(samples, n, first_index);   // writer
//     ring.read(dst, max_count, pos, &first);  // reader: how many it got; `first` is the index of the first one
//
// Every sample has an index, its position in the stream, as in hardware_ring.hpp. Sample i lives in slot i % capacity.
// The writer first reserves the range it is about to overwrite (`reserve`), writes, then publishes it (`head`). A
// reader copies optimistically and then checks `reserve` again: if the writer got into what it copied, it tries
// again from what is still valid. So a reader that is lapped sees `first` jump forward, never torn data.
//
// Samples the hardware lost before the writer ever had them (an index jump in publish) are recorded in a table of gaps;
// a read never spans one, and a reader that is inside one skips to its end, so those too show as a jump in `first`.

#include <atomic>
#include <algorithm>
#include <cstdint>
#include <cstring>
#include <stdexcept>

#include "hardware_ring.hpp"

namespace hw {

class ShmRing {
public:
    static constexpr uint64_t MAGIC = 0x31204d5344525350ull;   // "PSRDSM 1"
    static constexpr size_t GAPS = 1024;                       // gaps remembered (the most recent)
    static constexpr size_t HEADER_BYTES = 32768;              // the header, so the samples start page-aligned

    static size_t segment_size(uint64_t capacity) { return HEADER_BYTES + capacity * sizeof(cfloat); }

    // Writer: `base` is a zeroed segment of at least segment_size(capacity) bytes
    ShmRing(void* base, size_t size, uint64_t capacity, uint64_t stream_id) : h_(static_cast<Header*>(base)) {
        if (!capacity) throw std::invalid_argument("capacity must be at least 1");
        if (size < segment_size(capacity)) throw std::invalid_argument("the segment is too small for that capacity");
        h_->capacity = capacity;
        h_->stream_id = stream_id;
        h_->magic = MAGIC;   // last: a reader that sees it sees the rest
        std::atomic_thread_fence(std::memory_order_release);
        data_ = reinterpret_cast<cfloat*>(static_cast<unsigned char*>(base) + HEADER_BYTES);
    }
    // Reader
    ShmRing(void* base, size_t size) : h_(static_cast<Header*>(base)) {
        std::atomic_thread_fence(std::memory_order_acquire);
        if (size < HEADER_BYTES || h_->magic != MAGIC) throw std::invalid_argument("not a pysdrlib sample ring");
        if (size < segment_size(h_->capacity)) throw std::invalid_argument("the segment is smaller than its ring");
        data_ = reinterpret_cast<cfloat*>(static_cast<unsigned char*>(base) + HEADER_BYTES);
    }

    uint64_t capacity() const { return h_->capacity; }
    uint64_t stream_id() const { return h_->stream_id; }
    uint64_t head() const { return h_->head.load(std::memory_order_acquire); }   // the index after the newest sample
    bool closed() const { return h_->closed.load(std::memory_order_acquire) != 0; }

    // Writer: no more samples are coming
    void close() { h_->closed.store(1, std::memory_order_release); }

    // Writer: append `n` samples whose first has stream index `first` (at or after head(); later is a gap)
    void publish(const cfloat* src, uint64_t n, uint64_t first) {
        const uint64_t cap = h_->capacity;
        const uint64_t head = h_->head.load(std::memory_order_relaxed);
        if (first < head) throw std::invalid_argument("samples must be published in order");
        if (n == 0) return;
        if (first > head) {   // the samples in between were lost before they reached us
            const uint64_t g = h_->gaps.load(std::memory_order_relaxed);
            h_->gap[g % GAPS] = {head, first - head};
            h_->gaps.store(g + 1, std::memory_order_release);
        }
        if (n > cap) {   // more than the ring holds: only the last of it can be kept
            src += n - cap;
            first += n - cap;
            n = cap;
        }
        h_->reserve.store(first + n, std::memory_order_relaxed);
        std::atomic_thread_fence(std::memory_order_seq_cst);   // the reservation is visible before any slot is written
        const uint64_t slot = first % cap;
        const uint64_t part = std::min(n, cap - slot);
        std::memcpy(data_ + slot, src, part * sizeof(cfloat));
        if (n > part) std::memcpy(data_, src + part, (n - part) * sizeof(cfloat));
        h_->head.store(first + n, std::memory_order_release);
    }

    // Reader: up to `max_count` samples from stream index `pos` (or the first still there, if the reader was lapped, or the
    // end of a gap it is in). Returns how many; `*first` is the index of the first one, or (none) where the reader
    // should continue from: it can be past `pos`, which is how samples lost to a lapped cursor or a gap show.
    size_t read(cfloat* dst, size_t max_count, uint64_t pos, uint64_t* first) const {
        const uint64_t cap = h_->capacity;
        for (;;) {
            const uint64_t head = h_->head.load(std::memory_order_acquire);
            const uint64_t reserve = h_->reserve.load(std::memory_order_acquire);
            uint64_t start = std::max(pos, reserve > cap ? reserve - cap : 0);   // older may be being overwritten
            uint64_t end = std::min<uint64_t>(head, start + max_count);
            const uint64_t g = h_->gaps.load(std::memory_order_acquire);
            bool inside = false;
            for (uint64_t i = g, floor = g > GAPS ? g - GAPS : 0; i > floor; --i) {   // newest first
                const Gap rec = h_->gap[(i - 1) % GAPS];
                if (rec.start + rec.count <= start) break;   // they are in order: the older ones end earlier still
                if (rec.start <= start) {                    // in a gap: continue after it
                    pos = rec.start + rec.count;
                    inside = true;
                    break;
                }
                if (rec.start < end) end = rec.start;        // never read across one
            }
            if (inside) continue;
            *first = start;
            if (start >= end) return 0;
            const size_t n = static_cast<size_t>(end - start);
            const uint64_t slot = start % cap;
            const uint64_t part = std::min<uint64_t>(n, cap - slot);
            std::memcpy(dst, data_ + slot, part * sizeof(cfloat));
            if (n > part) std::memcpy(dst + part, data_, (n - part) * sizeof(cfloat));
            std::atomic_thread_fence(std::memory_order_acquire);
            const uint64_t after = h_->reserve.load(std::memory_order_acquire);
            if (start >= (after > cap ? after - cap : 0)) return n;   // nothing we copied was overwritten meanwhile
            pos = start;   // the writer got into it: go again from what is still valid
        }
    }

private:
    struct Gap { uint64_t start, count; };
    struct Header {
        uint64_t magic;
        uint64_t capacity;
        uint64_t stream_id;
        alignas(64) std::atomic<uint64_t> head;       // index after the newest published sample
        alignas(64) std::atomic<uint64_t> reserve;    // index after the sample being written (>= head)
        alignas(64) std::atomic<uint64_t> gaps;       // gaps recorded so far
        alignas(64) std::atomic<uint64_t> closed;
        alignas(64) Gap gap[GAPS];
    };
    static_assert(sizeof(Header) <= HEADER_BYTES, "the header must fit before the samples");

    Header* h_;
    cfloat* data_;
};

}  // namespace hw
