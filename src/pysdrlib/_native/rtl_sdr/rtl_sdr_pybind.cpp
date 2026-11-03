#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <memory>

#include "rtl_sdr_device.hpp"
#include "hardware_pybind.hpp"

namespace py = pybind11;

namespace {

struct Errors {
    static constexpr const char* module = "pysdrlib.rtl_sdr.lib.err";
    static const char* class_for(int code) {
        switch (code) {
            case RTLSDR_ERROR_INVALID_PARAM: return "INVALID_PARAM";
            case RTLSDR_ERROR_NOT_FOUND:     return "NOT_FOUND";
            case RTLSDR_ERROR_NOT_OPEN:      return "NOT_OPEN";
            case RTLSDR_ERROR_OPEN_FAILED:   return "OPEN_FAILED";
            case RTLSDR_ERROR_BUSY:          return "BUSY";
            default:                         return "RTLSDRError";
        }
    }
};

}  // namespace

PYBIND11_MODULE(_rtl_sdr, m) {
    m.doc() = "RTL-SDR bindings (pybind11)";

    // librtlsdr errors -> the pysdrlib.rtl_sdr.lib.err hierarchy
    hw::register_errors<Errors>();

    hw::bind_device<RTLSDRDevice>(m, "device")
        // tuning
        .def("set_freq", &RTLSDRDevice::set_freq)
        .def("set_sample_rate", &RTLSDRDevice::set_sample_rate)
        .def("set_freq_correction", &RTLSDRDevice::set_freq_correction)

        // device-specific controls
        .def("set_rx_rf_gain", &RTLSDRDevice::set_rx_rf_gain)
        .def("set_agc", &RTLSDRDevice::set_agc)
        .def("set_bias_t", &RTLSDRDevice::set_bias_t)

        // queries
        .def("gains", &RTLSDRDevice::gains)
        .def_property_readonly("tuner", &RTLSDRDevice::tuner)
        .def("usb_strings", &RTLSDRDevice::usb_strings);

    m.def("device_count", [] { return rtlsdr_device_count(); });
    m.def("device_info", &rtlsdr_device_info);   // (name, manufacturer, product, serial)

    m.def("open", [](uint32_t index) {
        return hw::open_device<RTLSDRDevice>([&](RTLSDRDevice& d) { d.open(index); });
    }, py::arg("index") = 0);
    m.def("open_by_serial", [](const std::string& serial) {
        return hw::open_device<RTLSDRDevice>([&](RTLSDRDevice& d) { d.open_by_serial(serial); });
    });
}
