#pragma once

// Layout of a device's raw sample stream. Devices differ: 8-bit ints, 12-bit values in 16-bit words,
// 12-bit packed, 4-bit packed, offset-binary unsigned, floats (16, 32 or 64 bits)... Mirrors pysdrlib.base.convert.SampleFormat.

#include <cstddef>

// Layout of one component (I or Q) of the raw stream
enum class SampleKind { Int, UInt, Float };

struct SampleFormat {
    SampleKind kind;
    int bits;          // significant bits per component (e.g. 12)
    int storage_bits;  // bits a component occupies in memory (8/16/32/64); == bits when packed (12 or 4)
    bool packed = false;
    bool big_endian = false;   // byte order of a component wider than 8 bits (devices deliver little endian)

    bool operator==(const SampleFormat& o) const {
        return kind == o.kind && bits == o.bits && storage_bits == o.storage_bits && packed == o.packed
            && big_endian == o.big_endian;
    }

    // Bytes of one complex (I, Q) sample
    size_t bytes_per_sample() const {
        if (packed) return bits == 4 ? 1 : 3;   // 4-bit: I in the high nibble, Q in the low; 12-bit: 3 bytes
        return static_cast<size_t>(2 * storage_bits / 8);
    }
};
