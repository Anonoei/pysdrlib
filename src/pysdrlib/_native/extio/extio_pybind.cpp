#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <memory>

#include "extio_device.hpp"
#include "hardware_pybind.hpp"

namespace py = pybind11;

namespace {

struct Errors {
    static constexpr const char* module = "pysdrlib.extio.lib.err";
    static const char* class_for(int code) {
        switch (code) {
            case EXTIO_ERROR_INVALID_PARAM: return "INVALID_PARAM";
            case EXTIO_ERROR_NOT_FOUND:     return "NOT_FOUND";
            case EXTIO_ERROR_NOT_OPEN:      return "NOT_OPEN";
            default:                        return "ExtIOError";
        }
    }
};

}  // namespace

PYBIND11_MODULE(_extio, m) {
    m.doc() = "RX888 (ExtIO_sddc) bindings (pybind11)";

    // Driver errors -> the pysdrlib.extio.lib.err hierarchy
    hw::register_errors<Errors>();

    hw::bind_device<ExtIODevice>(m, "device")
        // tuning
        .def("set_freq", &ExtIODevice::set_freq)
        .def("set_sample_rate", &ExtIODevice::set_sample_rate, py::call_guard<py::gil_scoped_release>())   // may restart the stream
        .def("sample_rates", &ExtIODevice::sample_rates)

        // device-specific controls
        .def("set_rx_rf_gain", &ExtIODevice::set_rx_rf_gain)
        .def("set_rx_if_gain", &ExtIODevice::set_rx_if_gain)
        .def("set_bias_t", &ExtIODevice::set_bias_t)
        .def("set_dither", &ExtIODevice::set_dither)
        .def("set_random", &ExtIODevice::set_random)
        .def("set_pga", &ExtIODevice::set_pga)

        // queries
        .def_property_readonly("name", &ExtIODevice::name)
        .def_property_readonly("firmware", &ExtIODevice::firmware)
        .def_property_readonly("is_vhf", &ExtIODevice::is_vhf);

    m.def("device_count", [] {
        py::gil_scoped_release release;
        return usb_device_count_devices();
    });

    m.def("open", [] {   // uploads firmware: takes seconds
        return hw::open_device<ExtIODevice>([](ExtIODevice& d) { d.open(); });
    });
}
