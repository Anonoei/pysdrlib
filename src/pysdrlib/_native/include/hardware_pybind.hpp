#pragma once

// pybind11 bindings every device shares. A device's own binding file only adds its controls:
//
//     PYBIND11_MODULE(_mydev, m) {
//         hw::register_errors<MyErrors>();
//         hw::bind_device<MyDevice>(m, "device")
//             .def("set_freq", &MyDevice::set_freq);
//         m.def("open", [] { return hw::open_device<MyDevice>([](MyDevice& d) { d.open(); }); });
//     }

#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>

#include <algorithm>
#include <cstring>
#include <memory>
#include <string>
#include <thread>

#include "hardware_numpy.hpp"
#include "hardware_wrapper.hpp"

namespace hw {

namespace py = pybind11;

// A device garbage-collected while streaming has to join the driver's streaming thread in close(),
// and that thread may be waiting for the GIL inside the Python callback. Release it first.
template <typename Device>
class GilSafe : public Device {
public:
    ~GilSafe() override {
        if (PyGILState_Check()) {
            py::gil_scoped_release release;
            try { this->close(); } catch (...) {}
        }
    }
};

// Dropping the Python callback needs the GIL, which the destructor path may not hold.
struct PyCallback {
    explicit PyCallback(py::function fn) : fn_(new py::function(std::move(fn)), [](py::function* p) {
        py::gil_scoped_acquire gil;
        delete p;
    }) {}
    std::shared_ptr<py::function> fn_;
};

// Raised C++ HardwareErrors become `<Errors::module>.<Errors::class_for(code)>`, a class in the device's
// Python error module. Local to the binding module: every device throws the same C++ type, and a global
// translator would catch the other devices' errors.
//
//     struct MyErrors {
//         static constexpr const char* module = "pysdrlib.mydev.lib.err";
//         static const char* class_for(int code);
//     };
template <typename Errors>
void register_errors() {
    py::register_local_exception_translator([](std::exception_ptr p) {
        try {
            if (p) std::rethrow_exception(p);
        } catch (const HardwareError& e) {
            py::object cls = py::module_::import(Errors::module).attr(Errors::class_for(e.code()));
            PyErr_SetString(cls.ptr(), e.what());
        }
    });
}

// A new closed device whose open() has been run with the GIL released (opening can take seconds)
template <typename Device, typename OpenFn>
std::unique_ptr<Device> open_device(OpenFn&& open) {
    std::unique_ptr<Device> d(new GilSafe<Device>());
    py::gil_scoped_release release;
    open(*d);
    return d;
}

// The class with the interface of HardwareDevice. The callback gets (array of the device's raw samples
// in its native dtype, valid_bytes, buffer_bytes); returning non-zero asks the driver to stop streaming
// (for drivers that can), None means 0, and raising stops and reports the exception as unraisable.
template <typename Device>
py::class_<Device> bind_device(py::module_& m, const char* name) {
    const auto nogil = py::call_guard<py::gil_scoped_release>();

    return py::class_<Device>(m, name)
        .def(py::init([] { return std::unique_ptr<Device>(new GilSafe<Device>()); }),
             "A closed device; use the module's open() to get an open one.")
        .def("close", &Device::close, nogil)
        .def_property_readonly("is_open", &Device::is_open)

        .def("set_rx_callback", [](Device& d, py::function fn) {
            PyCallback holder(std::move(fn));
            const SampleFormat format = d.rx_sample_format();
            d.set_rx_callback([holder, format](const void* data, size_t valid, size_t buflen) -> int {
                py::gil_scoped_acquire gil;
                try {
                    py::object r = (*holder.fn_)(make_rx_array(format, data, valid), valid, buflen);
                    return r.is_none() ? 0 : r.cast<int>();
                } catch (py::error_already_set& e) {
                    e.discard_as_unraisable("pysdrlib rx_callback");
                    return -1;
                }
            });
        })
        .def("start_rx", &Device::start_rx, nogil)
        .def("stop_rx", &Device::stop_rx, nogil)
        .def("is_streaming", &Device::is_streaming)
        // (kind, significant bits, storage bits, packed): see pysdrlib.base.convert.SampleFormat
        .def("rx_sample_format", [](const Device& d) {
            SampleFormat f = d.rx_sample_format();
            return py::make_tuple(kind_name(f.kind), f.bits, f.storage_bits, f.packed);
        })

        // ---- buffered receive: the driver's thread converts to complex64 and queues, no Python involved ---- //
        .def("set_rx_buffer", [](Device& d, size_t samples) { d.set_rx_buffer(samples); },
             "Queue up to `samples` complex samples (the oldest are dropped past that); discards pending ones.")
        .def("reset_rx_stats", [](Device& d) { d.reset_rx_stats(); })
        .def_property_readonly("rx_available", &Device::rx_available)
        .def("read_rx", [](Device& d, py::object count) {
            // Oldest pending samples first (all if `count` is None); removes what it returns
            size_t want = d.rx_available();
            if (!count.is_none()) want = std::min(want, count.cast<size_t>());
            py::array_t<cfloat> out(want);
            size_t got = 0;
            if (want) {
                py::gil_scoped_release release;
                got = d.read_rx(out.mutable_data(), want);
            }
            if (got == want) return out;
            py::array_t<cfloat> trimmed(got);   // another reader got there first
            if (got) std::memcpy(trimmed.mutable_data(), out.data(), got * sizeof(cfloat));
            return trimmed;
        }, py::arg("count") = py::none())
        .def("read_rx_indexed", [](Device& d, size_t count) {
            // (samples, index of the first): up to `count` of the oldest pending samples. A jump in the index from
            // the end of the previous read is the number of samples dropped in between.
            py::array_t<cfloat> out(count);
            size_t got = 0;
            uint64_t first = 0;
            {
                py::gil_scoped_release release;
                got = d.read_rx(out.mutable_data(), count, &first);
            }
            if (got != count) {
                py::array_t<cfloat> trimmed(got);
                if (got) std::memcpy(trimmed.mutable_data(), out.data(), got * sizeof(cfloat));
                return py::make_tuple(trimmed, first);
            }
            return py::make_tuple(out, first);
        }, py::arg("count"))
        .def("wait_rx", [](Device& d, size_t samples, double timeout) {
            py::gil_scoped_release release;
            return d.wait_rx(samples, timeout);
        }, py::arg("samples"), py::arg("timeout"),
           "Wait until `samples` are queued (at most the queue's size) or `timeout` seconds pass (negative: no limit); "
           "returns how many are queued.")
        .def("rx_stats", [](const Device& d) {
            RingStats s = d.rx_stats();
            py::dict out;
            out["samples"] = s.samples;
            out["total"] = s.total;
            out["dropped"] = s.dropped;
            out["pending"] = s.pending;
            out["max_chunk"] = s.max_chunk;
            out["calls"] = s.calls;
            out["busy"] = s.busy;
            out["age"] = s.seen ? py::object(py::float_(s.age)) : py::none();   // None: nothing delivered yet
            return out;
        })

        // Test/bench hook: hand `data` to the receive path on the calling thread
        .def("_dispatch_rx", [](Device& d, py::buffer data) {
            py::buffer_info info = data.request();
            const size_t bytes = static_cast<size_t>(info.size * info.itemsize);
            py::gil_scoped_release release;
            return d.dispatch_rx(info.ptr, bytes, bytes);
        })

        // Test hook: run `data` through the same path a driver's streaming thread uses (callback marshalling
        // from a native thread, GIL handling), without hardware. Returns what the callback asked for.
        .def("_simulate_rx", [](Device& d, py::bytes data, size_t valid, size_t buflen) {
            std::string buf = data;
            if (valid > buf.size()) throw py::value_error("valid is longer than data");
            int result = 0;
            py::gil_scoped_release release;
            std::thread t([&] { result = d.dispatch_rx(buf.data(), valid, buflen); });
            t.join();
            return result;
        });
}

}  // namespace hw
