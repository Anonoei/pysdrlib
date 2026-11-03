// pysdrlib.base.lib._samples: the sample conversion and the sample ring, in C++, for pysdrlib.base.convert
// and pysdrlib.base.buffer. The devices' receive path (include/hardware_rx.hpp) runs the same code, so what
// Python converts and buffers is exactly what a driver thread does.
#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>

#include <string>

#include "hardware_convert.hpp"
#include "hardware_ring.hpp"

namespace py = pybind11;
using namespace hw;

namespace {

SampleFormat make_format(const std::string& kind, int bits, int storage, bool packed, bool big_endian) {
    if (kind != "int" && kind != "uint" && kind != "float")
        throw py::value_error("kind must be int, uint or float, not '" + kind + "'");
    SampleKind k = kind == "int" ? SampleKind::Int : kind == "uint" ? SampleKind::UInt : SampleKind::Float;
    SampleFormat f{k, bits, storage, packed, big_endian};
    check_format(f);   // std::invalid_argument -> ValueError
    return f;
}

// The bytes of a C-contiguous buffer (any dtype), as the memory a driver would hand over
std::pair<const unsigned char*, size_t> contiguous_bytes(const py::buffer_info& info) {
    py::ssize_t expected = info.itemsize;
    for (py::ssize_t d = info.ndim - 1; d >= 0; --d) {
        if (info.shape[d] > 1 && info.strides[d] != expected) throw py::value_error("raw samples must be C-contiguous");
        expected *= info.shape[d];
    }
    return {static_cast<const unsigned char*>(info.ptr), static_cast<size_t>(info.size * info.itemsize)};
}

}  // namespace

PYBIND11_MODULE(_samples, m) {
    m.doc() = "Sample conversion and ring buffer (pybind11)";

    // raw bytes in `kind/bits/storage/packed/big_endian` layout -> complex64 in [-1, 1]. `valid` is how many
    // bytes of `raw` count (default all); a trailing partial sample is ignored.
    m.def("decode", [](py::buffer raw, const std::string& kind, int bits, int storage, bool packed, bool big_endian,
                       py::object valid) {
        const SampleFormat f = make_format(kind, bits, storage, packed, big_endian);
        py::buffer_info info = raw.request();
        auto [ptr, nbytes] = contiguous_bytes(info);
        if (!valid.is_none()) nbytes = std::min<size_t>(nbytes, std::max<py::ssize_t>(0, valid.cast<py::ssize_t>()));
        const size_t count = nbytes / f.bytes_per_sample();
        py::array_t<cfloat> out(count);
        if (count) {
            py::gil_scoped_release release;
            decode_samples(f, ptr, count, out.mutable_data());
        }
        return out;
    }, py::arg("raw"), py::arg("kind"), py::arg("bits"), py::arg("storage"), py::arg("packed") = false,
       py::arg("big_endian") = false, py::arg("valid") = py::none());

    // complex64 -> raw bytes (uint8) in that layout, rounded to the nearest value and clamped to its range
    m.def("encode", [](py::array_t<cfloat, py::array::c_style | py::array::forcecast> iq, const std::string& kind, int bits,
                       int storage, bool packed, bool big_endian) {
        const SampleFormat f = make_format(kind, bits, storage, packed, big_endian);
        const size_t count = static_cast<size_t>(iq.size());
        py::array_t<unsigned char> out(count * f.bytes_per_sample());
        if (count) {
            py::gil_scoped_release release;
            encode_samples(f, iq.data(), count, out.mutable_data());
        }
        return out;
    }, py::arg("iq"), py::arg("kind"), py::arg("bits"), py::arg("storage"), py::arg("packed") = false,
       py::arg("big_endian") = false);

    py::class_<Ring>(m, "Ring", "Thread-safe ring of complex64 samples that drops the oldest when full.")
        .def(py::init<>())
        .def("reset", [](Ring& r, size_t size) { r.reset(size); },
             "Set the capacity; discards pending samples and clears `dropped`.")
        .def("update", [](Ring& r, py::array_t<cfloat, py::array::c_style | py::array::forcecast> samples) {
            py::gil_scoped_release release;
            r.write(samples.data(), static_cast<size_t>(samples.size()));
        }, "Append samples, dropping the oldest pending ones if capacity is exceeded.")
        .def("read", [](Ring& r, py::object count) {
            size_t want = r.available();
            if (!count.is_none()) want = std::min<size_t>(want, std::max<py::ssize_t>(0, count.cast<py::ssize_t>()));
            py::array_t<cfloat> out(want);
            size_t got = 0;
            if (want) {
                py::gil_scoped_release release;
                got = r.read(out.mutable_data(), want);
            }
            if (got == want) return out;
            py::array_t<cfloat> trimmed(got);   // another reader got there first
            if (got) std::memcpy(trimmed.mutable_data(), out.data(), got * sizeof(cfloat));
            return trimmed;
        }, py::arg("count") = py::none(), "Remove and return up to `count` of the oldest pending samples (all if None).")
        .def_property_readonly("available", &Ring::available)
        .def_property_readonly("capacity", &Ring::capacity)
        .def_property_readonly("dropped", &Ring::dropped)
        .def("clear_dropped", &Ring::clear_dropped, "Return the dropped count and reset it to 0.");
}
