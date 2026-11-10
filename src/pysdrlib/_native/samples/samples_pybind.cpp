// pysdrlib.base.lib._samples: the sample conversion and the sample ring, in C++, for pysdrlib.base.convert
// and pysdrlib.base.buffer. The devices' receive path (include/hardware_rx.hpp) runs the same code, so what
// Python converts and buffers is exactly what a driver thread does.
#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>

#include <memory>
#include <string>

#include "hardware_convert.hpp"
#include "hardware_ring.hpp"
#include "hardware_shm.hpp"

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

// A ShmRing over a buffer the caller maps (a multiprocessing.shared_memory segment): it keeps the buffer exported until
// release(), so the owner must release() before it closes the segment
class PyShmRing {
public:
    PyShmRing(py::buffer buf, py::object capacity, uint64_t stream_id)
        : info_(std::make_unique<py::buffer_info>(buf.request(true))) {
        const size_t size = static_cast<size_t>(info_->size * info_->itemsize);
        if (capacity.is_none()) ring_ = std::make_unique<ShmRing>(info_->ptr, size);
        else ring_ = std::make_unique<ShmRing>(info_->ptr, size, capacity.cast<uint64_t>(), stream_id);
    }
    ShmRing& ring() {
        if (!ring_) throw py::value_error("the ring is released");
        return *ring_;
    }
    void release() {
        ring_.reset();
        info_.reset();
    }
private:
    std::unique_ptr<py::buffer_info> info_;
    std::unique_ptr<ShmRing> ring_;
};

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
        .def("read_indexed", [](Ring& r, size_t count) {
            py::array_t<cfloat> out(count);
            size_t got = 0;
            uint64_t first = 0;
            {
                py::gil_scoped_release release;
                got = r.read(out.mutable_data(), count, &first);
            }
            if (got != count) {
                py::array_t<cfloat> trimmed(got);
                if (got) std::memcpy(trimmed.mutable_data(), out.data(), got * sizeof(cfloat));
                return py::make_tuple(trimmed, first);
            }
            return py::make_tuple(out, first);
        }, py::arg("count"), "(samples, index of the first): up to `count` of the oldest pending samples.")
        .def("wait", [](Ring& r, size_t count, double timeout) {
            py::gil_scoped_release release;
            return r.wait(count, timeout);
        }, py::arg("count"), py::arg("timeout"),
           "Wait until `count` samples are pending (at most the capacity) or `timeout` seconds pass (negative: no "
           "limit); returns how many are pending.")
        .def_property_readonly("total", [](const Ring& r) { return r.stats().total; },
                               "Samples written since reset(): the index the next one gets.")
        .def_property_readonly("age", [](const Ring& r) {
            RingStats st = r.stats();
            return st.seen ? py::object(py::float_(st.age)) : py::object(py::none());
        }, "Seconds since the last write (None: nothing written since reset_stats).")
        .def("skip", [](Ring& r, size_t n) { r.skip(n); }, py::arg("count"),
             "Record `count` samples lost before the ring: they take indices (a reader sees the jump) but no room.")
        .def_property_readonly("available", &Ring::available)
        .def_property_readonly("capacity", &Ring::capacity)
        .def_property_readonly("dropped", &Ring::dropped)
        .def("clear_dropped", &Ring::clear_dropped, "Return the dropped count and reset it to 0.");

    m.def("shm_segment_size", [](uint64_t capacity) { return ShmRing::segment_size(capacity); }, py::arg("capacity"),
          "Bytes of memory a ShmRing of `capacity` samples needs.");

    py::class_<PyShmRing>(m, "ShmRing", "Complex64 samples in memory that several processes map: one writer, any number of "
                                        "readers with a cursor each (see hardware_shm.hpp).")
        .def(py::init<py::buffer, py::object, uint64_t>(), py::arg("buffer"), py::arg("capacity") = py::none(),
             py::arg("stream_id") = 0,
             "With `capacity`, the writer: lays the ring out in `buffer` (zeroed, ShmRing.segment_size(capacity) bytes). "
             "Without, a reader of one a writer laid out.")
        .def_property_readonly("capacity", [](PyShmRing& r) { return r.ring().capacity(); })
        .def_property_readonly("stream_id", [](PyShmRing& r) { return r.ring().stream_id(); })
        .def_property_readonly("head", [](PyShmRing& r) { return r.ring().head(); },
                               "The stream index after the newest published sample.")
        .def_property_readonly("closed", [](PyShmRing& r) { return r.ring().closed(); })
        .def("close", [](PyShmRing& r) { r.ring().close(); }, "Writer: no more samples are coming.")
        .def("publish", [](PyShmRing& r, py::array_t<cfloat, py::array::c_style | py::array::forcecast> samples, uint64_t first) {
            py::buffer_info info = samples.request();
            const cfloat* src = static_cast<const cfloat*>(info.ptr);
            const uint64_t n = static_cast<uint64_t>(info.size);
            ShmRing& ring = r.ring();
            py::gil_scoped_release release;
            ring.publish(src, n, first);
        }, py::arg("samples"), py::arg("first"),
           "Writer: append samples whose first has stream index `first` (at or after head; later records a gap).")
        .def("read", [](PyShmRing& r, py::array_t<cfloat, py::array::c_style> out, uint64_t pos) {
            py::buffer_info info = out.request(true);
            cfloat* dst = static_cast<cfloat*>(info.ptr);
            const size_t want = static_cast<size_t>(info.size);
            ShmRing& ring = r.ring();
            size_t got = 0;
            uint64_t first = 0;
            {
                py::gil_scoped_release release;
                got = ring.read(dst, want, pos, &first);
            }
            return py::make_tuple(got, first);
        }, py::arg("out"), py::arg("pos"),
           "Reader: fill `out` with up to len(out) samples from stream index `pos`; returns (count, index of the first). "
           "The index is past `pos` when the reader was lapped or was in a gap; with count 0 it is where to continue.")
        .def("release", &PyShmRing::release, "Let go of the buffer (before the segment is closed).");
}
