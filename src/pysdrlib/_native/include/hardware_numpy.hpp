#pragma once

// Generic pybind11 helpers shared by every device binding.

#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>

#include <cstring>
#include <stdexcept>
#include <vector>

#include "hardware_wrapper.hpp"

namespace hw {

namespace py = pybind11;

// numpy dtype of one component for byte-aligned formats; uint8 (raw bytes) for packed ones.
inline py::dtype numpy_dtype(const SampleFormat& f) {
    if (f.packed) return py::dtype::of<uint8_t>();
    switch (f.kind) {
        case SampleKind::Int:
            switch (f.storage_bits) {
                case 8:  return py::dtype::of<int8_t>();
                case 16: return py::dtype::of<int16_t>();
                case 32: return py::dtype::of<int32_t>();
                case 64: return py::dtype::of<int64_t>();
            }
            break;
        case SampleKind::UInt:
            switch (f.storage_bits) {
                case 8:  return py::dtype::of<uint8_t>();
                case 16: return py::dtype::of<uint16_t>();
                case 32: return py::dtype::of<uint32_t>();
                case 64: return py::dtype::of<uint64_t>();
            }
            break;
        case SampleKind::Float:
            if (f.storage_bits == 32) return py::dtype::of<float>();
            if (f.storage_bits == 64) return py::dtype::of<double>();
            break;
    }
    throw std::invalid_argument("unsupported sample format");
}

// Copy `valid_bytes` of raw data into a new 1-D array of the format's dtype
// (a trailing partial component is dropped).
inline py::array make_rx_array(const SampleFormat& f, const void* data, size_t valid_bytes) {
    py::dtype dt = numpy_dtype(f);
    const size_t itemsize = static_cast<size_t>(dt.itemsize());
    const size_t count = valid_bytes / itemsize;
    std::vector<py::ssize_t> shape{static_cast<py::ssize_t>(count)};
    py::array arr(dt, shape);
    if (count) std::memcpy(arr.mutable_data(), data, count * itemsize);
    return arr;
}

inline const char* kind_name(SampleKind k) {
    return k == SampleKind::Int ? "int" : k == SampleKind::UInt ? "uint" : "float";
}

}  // namespace hw
