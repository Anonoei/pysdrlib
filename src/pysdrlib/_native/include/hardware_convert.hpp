#pragma once

// Raw samples <-> complex64, for every layout a SampleFormat can describe. This is the one implementation
// of the conversion: the devices' receive path (hardware_rx.hpp) and pysdrlib.base.convert both use it.
// This is where the per-width types live, like hardware_numpy.hpp does for numpy dtypes; nothing else
// generic names them.
//
//     decode_samples(fmt, raw, count, out)    raw -> complex64 in [-1, 1]
//     encode_samples(fmt, in, count, raw)     complex64 in [-1, 1] -> raw, rounded to nearest and clamped
//
// Compile with -ffp-contract=off so the arithmetic matches pysdrlib's numpy reference (tests/reference)
// to the last bit on every CPU, and -fno-trapping-math so the clamps in the encoder can be vectorised
// (nothing here depends on floating-point exceptions).
//
// The hot loops are compiled for several instruction sets and the CPU picks one when the library loads
// (HW_SIMD), the way numpy does it, so a generic wheel still uses AVX2 where there is any.

#include <cstddef>   // defines __GLIBC__ where there is one

#include <algorithm>
#include <cmath>
#include <complex>
#include <cstdint>
#include <cstring>
#include <limits>
#include <stdexcept>
#include <type_traits>

#include "hardware_format.hpp"

// Runtime CPU dispatch needs ifunc: glibc on x86-64, with a compiler that supports target_clones.
// Predefine HW_SIMD (empty to turn it off) to override.
#if defined(HW_SIMD)
#elif defined(__x86_64__) && defined(__linux__) && defined(__GLIBC__) && \
    ((defined(__clang__) && __clang_major__ >= 14) || (!defined(__clang__) && defined(__GNUC__) && __GNUC__ >= 9))
#define HW_SIMD __attribute__((target_clones("default", "avx2")))
#else
#define HW_SIMD
#endif

namespace hw {

using cfloat = std::complex<float>;

// Throws std::invalid_argument for a layout the conversion cannot handle
inline void check_format(const SampleFormat& f) {
    bool ok;
    if (f.packed) ok = f.kind == SampleKind::Int && f.bits == 12 && f.storage_bits == 12;
    else if (f.kind == SampleKind::Float) ok = f.bits == f.storage_bits && (f.storage_bits == 32 || f.storage_bits == 64);
    else ok = (f.storage_bits == 8 || f.storage_bits == 16 || f.storage_bits == 32 || f.storage_bits == 64)
              && f.bits >= 1 && f.bits <= f.storage_bits;
    if (!ok) throw std::invalid_argument("unsupported sample format");
}

namespace detail {

#if defined(__BYTE_ORDER__) && __BYTE_ORDER__ == __ORDER_BIG_ENDIAN__
constexpr bool host_big_endian = true;
#else
constexpr bool host_big_endian = false;
#endif

// Component i of the array at p, in the byte order `Big` (alignment-safe; compiles to plain vector loads)
template <typename T, bool Big>
inline T load(const unsigned char* p, size_t i) {
    unsigned char b[sizeof(T)];
    std::memcpy(b, p + i * sizeof(T), sizeof(T));
    if (Big != host_big_endian) std::reverse(b, b + sizeof(T));
    T v;
    std::memcpy(&v, b, sizeof(T));
    return v;
}

template <typename T, bool Big>
inline void store(unsigned char* p, size_t i, T v) {
    unsigned char b[sizeof(T)];
    std::memcpy(b, &v, sizeof(T));
    if (Big != host_big_endian) std::reverse(b, b + sizeof(T));
    std::memcpy(p + i * sizeof(T), b, sizeof(T));
}

// ---- raw -> float ---- //
template <typename T, bool Big>
HW_SIMD inline void scale_in(const unsigned char* src, size_t count, float offset, float scale, float* out) {
    for (size_t i = 0; i < count; ++i) out[i] = (static_cast<float>(load<T, Big>(src, i)) - offset) * scale;
}

template <typename T, bool Big>
HW_SIMD inline void copy_in(const unsigned char* src, size_t count, float* out) {
    for (size_t i = 0; i < count; ++i) out[i] = static_cast<float>(load<T, Big>(src, i));
}

// ---- float -> raw ---- //
// The range of a `bits`-bit value held in a T
template <typename T>
inline void limits(int bits, T& lo, T& hi) {
    const bool whole = bits >= static_cast<int>(8 * sizeof(T));
    if (std::is_signed<T>::value) {
        hi = whole ? std::numeric_limits<T>::max() : static_cast<T>((int64_t(1) << (bits - 1)) - 1);
        lo = static_cast<T>(-hi - 1);
    } else {
        hi = whole ? std::numeric_limits<T>::max() : static_cast<T>((uint64_t(1) << bits) - 1);
        lo = 0;
    }
}

// x -> the nearest integer of the format (ties to even), saturating at its range
template <typename T, bool Big>
inline void round_out(const float* in, size_t count, float scale, bool offset_binary, int bits, unsigned char* dst) {
    T lo, hi;
    limits<T>(bits, lo, hi);
    const double lo_d = static_cast<double>(lo), top = static_cast<double>(hi);   // top may round up past 2^53
    for (size_t i = 0; i < count; ++i) {
        float y = in[i] * scale;
        if (offset_binary) y = y + scale;
        const double r = std::nearbyint(static_cast<double>(y));
        T v;
        if (r >= top) v = hi;
        else if (!(r > lo_d)) v = lo;   // also catches NaN
        else v = static_cast<T>(r);
        store<T, Big>(dst, i, v);
    }
}

// The same result for formats of 16 bits or fewer, without a libm call per sample so the compiler can
// vectorise it. Clamping first is equivalent (the range ends are integers), and adding then subtracting
// 1.5 * 2^23 rounds a float of magnitude below 2^22 to the nearest integer, ties to even, in plain SSE2.
template <typename T, bool Big>
HW_SIMD inline void round_out_small(const float* in, size_t count, float scale, bool offset_binary, int bits, unsigned char* dst) {
    T lo_t, hi_t;
    limits<T>(bits, lo_t, hi_t);
    const float lo = static_cast<float>(lo_t), hi = static_cast<float>(hi_t), magic = 12582912.0f;
    for (size_t i = 0; i < count; ++i) {
        float y = in[i] * scale;
        if (offset_binary) y = y + scale;
        y = y > lo ? y : lo;   // NaN -> lo
        y = y < hi ? y : hi;
        y = (y + magic) - magic;
        store<T, Big>(dst, i, static_cast<T>(static_cast<int32_t>(y)));   // via 32 bits: one SSE2 convert, then narrow
    }
}

template <typename T, bool Big>
inline void cast_out(const float* in, size_t count, unsigned char* dst) {
    for (size_t i = 0; i < count; ++i) store<T, Big>(dst, i, static_cast<T>(in[i]));
}

inline double full_scale(const SampleFormat& f) {   // component magnitude that maps to 1.0
    if (f.kind == SampleKind::UInt) return (std::ldexp(1.0, f.bits) - 1.0) / 2.0;
    if (f.kind == SampleKind::Int) return std::ldexp(1.0, f.bits - 1);
    return 1.0;
}

template <bool Big>
inline void decode(const SampleFormat& f, const unsigned char* src, size_t count, float* o) {
    const size_t n = 2 * count;   // components
    if (f.kind == SampleKind::Float) {   // already in range: no scaling
        if (f.storage_bits == 32) copy_in<float, Big>(src, n, o);
        else copy_in<double, Big>(src, n, o);
        return;
    }
    const bool is_unsigned = f.kind == SampleKind::UInt;
    const double fs = full_scale(f);
    const float scale = static_cast<float>(1.0 / fs);
    const float offset = is_unsigned ? static_cast<float>(fs) : 0.0f;   // offset binary: mid-scale is zero
    switch (f.storage_bits) {
        case 8:
            if (is_unsigned) scale_in<uint8_t, Big>(src, n, offset, scale, o);
            else scale_in<int8_t, Big>(src, n, offset, scale, o);
            break;
        case 16:
            if (is_unsigned) scale_in<uint16_t, Big>(src, n, offset, scale, o);
            else scale_in<int16_t, Big>(src, n, offset, scale, o);
            break;
        case 32:
            if (is_unsigned) scale_in<uint32_t, Big>(src, n, offset, scale, o);
            else scale_in<int32_t, Big>(src, n, offset, scale, o);
            break;
        default:
            if (is_unsigned) scale_in<uint64_t, Big>(src, n, offset, scale, o);
            else scale_in<int64_t, Big>(src, n, offset, scale, o);
    }
}

template <bool Big>
inline void encode(const SampleFormat& f, const float* in, size_t count, unsigned char* dst) {
    const size_t n = 2 * count;
    if (f.kind == SampleKind::Float) {
        if (f.storage_bits == 32) cast_out<float, Big>(in, n, dst);
        else cast_out<double, Big>(in, n, dst);
        return;
    }
    const bool is_unsigned = f.kind == SampleKind::UInt;
    const float scale = static_cast<float>(full_scale(f));
    switch (f.storage_bits) {
        case 8:
            if (is_unsigned) round_out_small<uint8_t, Big>(in, n, scale, true, f.bits, dst);
            else round_out_small<int8_t, Big>(in, n, scale, false, f.bits, dst);
            break;
        case 16:
            if (is_unsigned) round_out_small<uint16_t, Big>(in, n, scale, true, f.bits, dst);
            else round_out_small<int16_t, Big>(in, n, scale, false, f.bits, dst);
            break;
        case 32:
            if (is_unsigned) round_out<uint32_t, Big>(in, n, scale, true, f.bits, dst);
            else round_out<int32_t, Big>(in, n, scale, false, f.bits, dst);
            break;
        default:
            if (is_unsigned) round_out<uint64_t, Big>(in, n, scale, true, f.bits, dst);
            else round_out<int64_t, Big>(in, n, scale, false, f.bits, dst);
    }
}

// Signed 12-bit, I and Q in 3 bytes
HW_SIMD inline void decode_packed12(const unsigned char* src, size_t count, float* o) {
    const float scale = static_cast<float>(1.0 / 2048.0);
    for (size_t i = 0; i < count; ++i) {
        const unsigned char* b = src + 3 * i;
        int iv = b[0] | ((b[1] & 0x0F) << 8);
        int qv = (b[1] >> 4) | (b[2] << 4);
        iv = (iv ^ 0x800) - 0x800;
        qv = (qv ^ 0x800) - 0x800;
        o[2 * i] = static_cast<float>(iv) * scale;
        o[2 * i + 1] = static_cast<float>(qv) * scale;
    }
}

HW_SIMD inline void encode_packed12(const float* x, size_t count, unsigned char* dst) {
    for (size_t i = 0; i < count; ++i) {
        int c[2];
        for (int k = 0; k < 2; ++k) {   // clamp, then round to even (see round_out_small)
            float y = x[2 * i + k] * 2048.0f;
            y = y > -2048.0f ? y : -2048.0f;   // NaN -> -2048
            y = y < 2047.0f ? y : 2047.0f;
            y = (y + 12582912.0f) - 12582912.0f;
            c[k] = static_cast<int>(y) & 0x0FFF;
        }
        unsigned char* b = dst + 3 * i;
        b[0] = static_cast<unsigned char>(c[0] & 0xFF);
        b[1] = static_cast<unsigned char>((c[0] >> 8) | ((c[1] & 0x0F) << 4));
        b[2] = static_cast<unsigned char>(c[1] >> 4);
    }
}

}  // namespace detail

// `count` complex samples at `src` (in layout `f`) -> complex64 in [-1, 1] at `out`.
inline void decode_samples(const SampleFormat& f, const unsigned char* src, size_t count, cfloat* out) {
    float* o = reinterpret_cast<float*>(out);
    if (f.packed) {
        detail::decode_packed12(src, count, o);
    } else if (f.big_endian) {
        detail::decode<true>(f, src, count, o);
    } else {
        detail::decode<false>(f, src, count, o);
    }
}

// `count` complex samples at `in` -> layout `f` at `dst` (f.bytes_per_sample() * count bytes), rounded to
// the nearest value and clamped to the format's range
inline void encode_samples(const SampleFormat& f, const cfloat* in, size_t count, unsigned char* dst) {
    const float* x = reinterpret_cast<const float*>(in);
    if (f.packed) {
        detail::encode_packed12(x, count, dst);
    } else if (f.big_endian) {
        detail::encode<true>(f, x, count, dst);
    } else {
        detail::encode<false>(f, x, count, dst);
    }
}

}  // namespace hw
