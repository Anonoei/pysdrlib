#pragma once

// Layout of a device's raw sample stream. Devices differ: 8-bit ints, 12-bit values in 16-bit words,
// 12-bit packed, offset-binary unsigned, floats... Mirrors pysdrlib.base.convert.SampleFormat.

#include <cstddef>

// Layout of one component (I or Q) of the raw stream
enum class SampleKind { Int, UInt, Float };

struct SampleFormat {
    SampleKind kind;
    int bits;          // significant bits per component (e.g. 12)
    int storage_bits;  // bits a component occupies in memory (8/16/32/64); == bits when packed
    bool packed = false;
    bool big_endian = false;   // byte order of a component wider than 8 bits (devices deliver little endian)

    bool operator==(const SampleFormat& o) const {
        return kind == o.kind && bits == o.bits && storage_bits == o.storage_bits && packed == o.packed
            && big_endian == o.big_endian;
    }

    // Bytes of one complex (I, Q) sample
    size_t bytes_per_sample() const { return packed ? 3 : static_cast<size_t>(2 * storage_bits / 8); }
};
