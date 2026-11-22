#pragma once

// pybind11 bindings every device shares. A device's own binding file only adds its controls:
//
//     PYBIND11_MODULE(_mydev, m) {
//         hw::register_errors(m, "MyRadioError", {{MY_ERROR_NOT_FOUND, "NOT_FOUND", ""}});
//         hw::bind_device<MyDevice>(m, "device")
//             .def("set_freq", &MyDevice::set_freq);
//         m.def("open", [] { return hw::open_device<MyDevice>([](MyDevice& d) { d.open(); }); });
//     }

#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>

#include <algorithm>
#include <cstring>
#include <map>
#include <memory>
#include <string>
#include <vector>

#include "hardware_wrapper.hpp"

namespace hw {

namespace py = pybind11;

inline const char* kind_name(SampleKind k) {
    return k == SampleKind::Int ? "int" : k == SampleKind::UInt ? "uint" : "float";
}

// A device garbage-collected while open closes it, which joins the driver's threads and can take seconds (a USRP):
// without the GIL, so the rest of the program carries on meanwhile.
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

// One row of a driver's error table: the vendor library's numeric `code`, the name of the Python class it becomes
// (several codes may share one name), and the class's docstring.
struct ErrorSpec {
    int code;
    const char* name;
    const char* doc;
};

// Raised C++ HardwareErrors become instances of the classes this makes, all subclasses of `base` (an Exception), in a
// module `pysdrlib.<device>.lib.err` the extension registers (so `except lib.err.NOT_FOUND` works). A code not in the
// table becomes the table's "OTHER" class if it has one, else `base`. Local to the binding module: every device throws
// the same C++ type, and a global translator would catch the other devices' errors.
//
//     hw::register_errors(m, "MyRadioError", {
//         {MY_ERROR_NOT_FOUND, "NOT_FOUND", "No unit matched"},
//         {MY_ERROR_BUSY, "BUSY", ""},
//     });
inline void register_errors(py::module_& m, const char* base, std::vector<ErrorSpec> table) {
    // pysdrlib.<device>.lib._<device> -> pysdrlib.<device>.lib.err
    std::string name = py::cast<std::string>(m.attr("__name__"));
    name = name.substr(0, name.rfind('.')) + ".err";
    py::module_ err = py::reinterpret_steal<py::module_>(PyImport_AddModule(name.c_str()));
    err.inc_ref();
    m.attr("err") = err;

    auto make = [&](const std::string& cls, const char* doc, PyObject* parent) {
        PyObject* e = PyErr_NewExceptionWithDoc((name + "." + cls).c_str(), doc && *doc ? doc : nullptr, parent, nullptr);
        if (!e) throw py::error_already_set();
        err.attr(cls.c_str()) = py::reinterpret_borrow<py::object>(e);
        return py::reinterpret_steal<py::object>(e);
    };
    py::object root = make(base, "", PyExc_Exception);
    std::map<std::string, py::object> classes;
    // A translator is a plain function, so the table lives in storage of this module (every extension is built with
    // hidden visibility, so each has its own copy of these); kept for the life of the process
    static std::map<int, PyObject*> by_code;
    static PyObject* fallback;
    for (const auto& row : table) {
        auto it = classes.find(row.name);
        if (it == classes.end()) it = classes.emplace(row.name, make(row.name, row.doc, root.ptr())).first;
        by_code[row.code] = it->second.inc_ref().ptr();
    }
    auto other = classes.find("OTHER");
    fallback = (other != classes.end() ? other->second : root).inc_ref().ptr();
    py::register_local_exception_translator([](std::exception_ptr p) {
        try {
            if (p) std::rethrow_exception(p);
        } catch (const HardwareError& e) {
            auto it = by_code.find(e.code());
            PyErr_SetString(it != by_code.end() ? it->second : fallback, e.what());
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

// The class with the interface of HardwareDevice: the receive queues (filled by the driver's thread, converted to
// complex64, no Python involved), streaming, and the counters the stream watchdog reads.
template <typename Device>
py::class_<Device> bind_device(py::module_& m, const char* name) {
    const auto nogil = py::call_guard<py::gil_scoped_release>();

    return py::class_<Device>(m, name)
        .def(py::init([] { return std::unique_ptr<Device>(new GilSafe<Device>()); }),
             "A closed device; use the module's open() to get an open one.")
        .def("close", &Device::close, nogil)
        .def_property_readonly("is_open", &Device::is_open)

        .def("start_rx", &Device::start_rx, nogil)
        .def("stop_rx", &Device::stop_rx, nogil)
        .def("is_streaming", &Device::is_streaming)
        // (kind, significant bits, storage bits, packed): see pysdrlib.base.convert.SampleFormat
        .def("rx_sample_format", [](const Device& d) {
            SampleFormat f = d.rx_sample_format();
            return py::make_tuple(kind_name(f.kind), f.bits, f.storage_bits, f.packed);
        })

        // ---- buffered receive: the driver's thread converts to complex64 and queues, no Python involved ---- //
        .def("set_rx_buffer", [](Device& d, size_t samples, size_t slots) { d.set_rx_buffer(samples, slots); },
             py::arg("samples"), py::arg("slots") = 1,
             "Queue up to `samples` complex samples (the oldest are dropped past that) in each of `slots` queues (one per "
             "channel of a multi-channel stream); discards pending ones.")
        .def_property_readonly("rx_slots", &Device::rx_slots)
        .def("reset_rx_stats", [](Device& d) { d.reset_rx_stats(); })
        .def("read_rx", [](Device& d, py::object count, size_t slot) {
            // Oldest pending samples first (all if `count` is None); removes what it returns. Never spans a gap
            size_t want = d.rx_available(slot);
            if (!count.is_none()) want = std::min(want, count.cast<size_t>());
            py::array_t<cfloat> out(want);
            size_t got = 0;
            if (want) {
                py::gil_scoped_release release;
                got = d.read_rx(out.mutable_data(), want, nullptr, slot);
            }
            if (got == want) return out;
            py::array_t<cfloat> trimmed(got);   // another reader got there first
            if (got) std::memcpy(trimmed.mutable_data(), out.data(), got * sizeof(cfloat));
            return trimmed;
        }, py::arg("count") = py::none(), py::arg("slot") = 0)
        .def("read_rx_into", [](Device& d, py::array_t<cfloat, py::array::c_style> out, double timeout, size_t slot) {
            // The whole of a read in one call, GIL released: wait up to `timeout` seconds for len(out) samples (or all the
            // queue holds), move up to that many into `out` (no copy in between; a read never spans a gap), and read the
            // counters. Returns (count, index of the first sample, then rx_counters()'s five). A jump in the index from
            // the end of the previous read is the number of samples dropped in between.
            py::buffer_info info = out.request(true);
            const size_t want = static_cast<size_t>(info.size);
            cfloat* dst = static_cast<cfloat*>(info.ptr);
            size_t got = 0;
            uint64_t first = 0;
            RingStats s;
            {
                py::gil_scoped_release release;
                d.wait_rx(want, timeout, slot);
                got = d.read_rx(dst, want, &first, slot);
                s = d.rx_stats(slot);
            }
            return py::make_tuple(got, first, s.samples, s.total, s.dropped, s.max_chunk, s.seen ? s.age : -1.0);
        }, py::arg("out"), py::arg("timeout"), py::arg("slot") = 0)
        .def("rx_counters", [](const Device& d, size_t slot) {
            // (samples, total, dropped, max_chunk, age): what the stream watchdog needs, without rx_stats()'s dict.
            // age is -1 until something has been delivered.
            RingStats s = d.rx_stats(slot);
            return py::make_tuple(s.samples, s.total, s.dropped, s.max_chunk, s.seen ? s.age : -1.0);
        }, py::arg("slot") = 0)
        .def("wait_rx", [](Device& d, size_t samples, double timeout, size_t slot) {
            py::gil_scoped_release release;
            return d.wait_rx(samples, timeout, slot);
        }, py::arg("samples"), py::arg("timeout"), py::arg("slot") = 0,
           "Wait until `samples` are queued (at most the queue's size) or `timeout` seconds pass (negative: no limit); "
           "returns how many are queued.")
        .def("rx_stats", [](const Device& d, size_t slot) {
            RingStats s = d.rx_stats(slot);
            py::dict out;
            out["samples"] = s.samples;
            out["total"] = s.total;
            out["skipped"] = s.skipped;
            out["dropped"] = s.dropped;
            out["pending"] = s.pending;
            out["reading"] = s.reading;
            out["max_chunk"] = s.max_chunk;
            out["calls"] = s.calls;
            out["busy"] = s.busy;
            out["age"] = s.seen ? py::object(py::float_(s.age)) : py::none();   // None: nothing delivered yet
            return out;
        }, py::arg("slot") = 0)

        // Test/bench hook: hand `data` (its first `valid` bytes, default all) to the receive path of `slot`, as a
        // driver's streaming thread does with each transfer. Returns what the driver gets back: 0, or -1 (no queue:
        // stop streaming)
        .def("_dispatch_rx", [](Device& d, py::buffer data, py::object valid, size_t slot) {
            py::buffer_info info = data.request();
            const size_t bytes = static_cast<size_t>(info.size * info.itemsize);
            const size_t n = valid.is_none() ? bytes : valid.cast<size_t>();
            if (n > bytes) throw py::value_error("valid is longer than data");
            py::gil_scoped_release release;
            return d.dispatch_rx(info.ptr, n, slot);
        }, py::arg("data"), py::arg("valid") = py::none(), py::arg("slot") = 0)
        // Test hook: `samples` lost before the queue, as a driver reports a hardware overflow (readers see the gap)
        .def("_skip_rx", [](Device& d, size_t samples, size_t slot) { d.skip_rx(samples, slot); },
             py::arg("samples"), py::arg("slot") = 0)
        // The next sample delivered to `slot` was taken at `t` seconds by the radio's clock (a device fed from Python
        // stamps its blocks with this; a driver calls stamp_rx from its thread)
        .def("stamp_rx", [](Device& d, double t, size_t slot) { d.stamp_rx(t, slot); },
             py::arg("t"), py::arg("slot") = 0)
        // [(index, seconds)]: the hardware timestamps of a slot's stream since the last call (index counted like
        // read_rx_into's), oldest first
        .def("take_rx_stamps", [](Device& d, size_t slot) {
            py::list out;
            for (const auto& [index, t] : d.take_rx_stamps(slot)) out.append(py::make_tuple(index, t));
            return out;
        }, py::arg("slot") = 0);
}

// The transmit side every transmitting device shares (HardwareDevice's queues): bind it to a class from bind_device
//     auto dev = hw::bind_device<MyDevice>(m, "device"); ...; hw::bind_tx(dev);
template <typename Device>
void bind_tx(py::class_<Device>& cls) {
    const auto nogil = py::call_guard<py::gil_scoped_release>();
    cls.def("set_tx_buffer", &Device::set_tx_buffer, py::arg("samples"))
        .def("start_tx", &Device::start_tx, nogil)
        .def("stop_tx", &Device::stop_tx, nogil)
        .def("is_transmitting", &Device::is_transmitting)
        .def("write_tx", [](Device& d, py::array_t<cfloat, py::array::c_style | py::array::forcecast> samples,
                            double timeout, size_t slot) {
            py::buffer_info info = samples.request();
            const size_t n = static_cast<size_t>(info.size);
            const cfloat* src = static_cast<const cfloat*>(info.ptr);
            py::gil_scoped_release release;
            return d.write_tx(src, n, timeout, slot);
        }, py::arg("samples"), py::arg("timeout") = -1.0, py::arg("slot") = 0,
           "Queue complex64 samples for the `slot`-th transmit channel, waiting up to `timeout` seconds (negative: no "
           "limit) for room; returns how many were queued.")
        .def("tx_stats", [](Device& d, size_t slot) {
            const auto s = d.tx_stats(slot);
            py::dict out;
            out["written"] = s.written;
            out["sent"] = s.sent;
            out["underruns"] = s.underruns;
            out["pending"] = s.pending;
            out["async_errors"] = d.tx_async_errors();
            return out;
        }, py::arg("slot") = 0)
        .def("tx_error", &Device::tx_error);
}

}  // namespace hw
